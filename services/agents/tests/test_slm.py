"""10.1 SLM explanations.

The SLM only rewrites deterministic text; it never picks a route. These tests run with
no model server: the runtimes are stand-ins built on `httpx.MockTransport` that answer
exactly the JSON shapes Ollama / llama.cpp produce, so what is asserted is *our*
behaviour — prompt contents, fact guard, fallbacks, honest speculative-decoding labels,
cache — not a model's prose. A real-server measurement is `scripts/slm_bench.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from arco_agents.explain import (
    CachedExplainer,
    DeterministicExplainer,
    LlamaCppBackend,
    OllamaBackend,
    RouteFacts,
    SlmExplainer,
    check_rewrite,
    required_facts,
)
from arco_agents.navigator import Navigator
from arco_agents.population import Population
from arco_agents.routing_config import RoutingConfig
from arco_agents.runner import make_explainer
from arco_agents.settings import AgentsSettings
from arco_blackboard.service import BlackboardService
from arco_schemas import RoadEdge, Route

DEMO_POPULATION = Path(__file__).resolve().parents[3] / "data/samples/lahaina/demo_population.json"
M = 0.001


def _edge(eid: int, p0: tuple[float, float], p1: tuple[float, float], name: str) -> RoadEdge:
    return RoadEdge.model_validate(
        {
            "id": eid,
            "geom": {"type": "LineString", "coordinates": [list(p0), list(p1)]},
            "u": eid,
            "v": eid + 1,
            "length_m": 111.0,
            "base_cost": 50.0,
            "current_cost": 50.0,
            "status": "open",
            "capacity_estimate": 40.0,
            "name": name,
        }
    )


def _facts(**kw: Any) -> RouteFacts:
    """Two 'Main St' edges heading east (same shape as the navigator unit tests)."""
    edges = (
        _edge(1, (0.0, 0.0), (M, M / 2), "Main St"),
        _edge(2, (M, M / 2), (2 * M, 0.0), "Main St"),
    )
    base: dict[str, Any] = {
        "cell_id": "A",
        "zone_id": "Z",
        "zone_name": "Civic Center",
        "group_size": 100,
        "length_m": 222.0,
        "cost_s": 100.0,
        "edges": edges,
        "avoided_severed": 2,
        "avoided_penalized": 0,
        "load_ratio": 0.4,
        "split_fraction": 0.6,
    }
    base.update(kw)
    return RouteFacts(**base)


# --- fake runtimes --------------------------------------------------------------------------


class FakeOllama:
    """Answers /api/generate like Ollama 0.3+: `response`, `eval_count`, `eval_duration` ns."""

    def __init__(self, reply: str | Exception, eval_count: int = 31, eval_ns: int = 310_000_000):
        self.reply = reply
        self.eval_count = eval_count
        self.eval_ns = eval_ns
        self.requests: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/generate"
        body = json.loads(request.content)
        self.requests.append(body)
        if isinstance(self.reply, Exception):
            raise self.reply
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "response": self.reply,
                "done": True,
                "eval_count": self.eval_count,
                "eval_duration": self.eval_ns,
            },
        )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


class FakeLlamaCpp:
    """Answers /completion like llama-server. With `draft=True` it includes the
    `draft_n` / `draft_n_accepted` timings a server started with `-md <draft>` reports."""

    def __init__(self, reply: str, *, draft: bool, predicted_n: int = 28):
        self.reply = reply
        self.draft = draft
        self.predicted_n = predicted_n
        self.requests: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/completion"
        body = json.loads(request.content)
        self.requests.append(body)
        timings: dict[str, Any] = {
            "predicted_n": self.predicted_n,
            "predicted_ms": 420.0,
            "predicted_per_second": self.predicted_n / 0.42,
        }
        if self.draft:
            timings["draft_n"] = 40
            timings["draft_n_accepted"] = 26
        return httpx.Response(
            200,
            json={
                "content": self.reply,
                "model": "/models/qwen2.5-1.5b-q4.gguf",
                "tokens_predicted": self.predicted_n,
                "timings": timings,
            },
        )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


GOOD = (
    "Head east on Main St for 220 m, then you will reach Civic Center; stay calm and keep moving."
)


def _ollama_explainer(fake: FakeOllama) -> SlmExplainer:
    return SlmExplainer(
        "http://ollama.test", "qwen2.5:1.5b-instruct", 2.0, transport=fake.transport()
    )


# --- disabled path ---------------------------------------------------------------------------


def test_slm_disabled_builds_no_client_and_no_network() -> None:
    settings = AgentsSettings(arco_slm_enabled=False, ollama_url="http://127.0.0.1:1")
    ex = make_explainer(settings)
    assert isinstance(ex, DeterministicExplainer)
    out = ex.explain(_facts())
    assert out.source == "deterministic" and out.timing is None


def test_make_explainer_picks_backend_from_settings() -> None:
    ex = make_explainer(
        AgentsSettings(
            arco_slm_enabled=True, arco_slm_backend="llamacpp", llamacpp_url="http://x.test"
        )
    )
    assert isinstance(ex, SlmExplainer) and isinstance(ex.backend, LlamaCppBackend)
    ex2 = make_explainer(AgentsSettings(arco_slm_enabled=True, ollama_url="http://y.test"))
    assert isinstance(ex2, SlmExplainer) and isinstance(ex2.backend, OllamaBackend)


# --- fact guard -------------------------------------------------------------------------------


def test_required_facts_are_roads_distances_and_destination() -> None:
    assert required_facts(_facts()) == ["Main St", "220 m", "Civic Center"]


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("", "empty"),
        ("Head east on Front Street for 220 m to Civic Center.", "dropped: Main St"),
        ("Head east on Main St for 200 m to Civic Center.", "dropped: 220 m"),
        ("Head east on Main St for 220 m to the shelter.", "dropped: Civic Center"),
        ("word " * 61 + "Main St 220 m Civic Center", "too long (67 words)"),
    ],
)
def test_check_rewrite_rejects_altered_facts(text: str, reason: str) -> None:
    assert check_rewrite(text, _facts()) == reason


def test_check_rewrite_accepts_faithful_text() -> None:
    assert check_rewrite(GOOD, _facts()) is None


# --- Ollama backend ---------------------------------------------------------------------------


def test_ollama_rewrite_is_used_and_timing_comes_from_the_runtime() -> None:
    fake = FakeOllama(GOOD, eval_count=31, eval_ns=310_000_000)
    ex = _ollama_explainer(fake)
    out = ex.explain(_facts())
    assert out.source == "slm" and out.instruction == GOOD
    # rationale stays deterministic: the model never explains *why*
    assert "2 closed road" in out.rationale
    assert out.timing is not None
    assert out.timing.backend == "ollama" and out.timing.tokens == 31
    assert out.timing.tokens_per_s == pytest.approx(100.0)
    assert out.timing.speculative == "unsupported"  # Ollama has no speculative API
    assert out.timing.latency_ms > 0
    # prompt + decoding knobs we rely on
    req = fake.requests[0]
    assert req["stream"] is False and req["options"]["temperature"] == 0
    assert req["options"]["num_predict"] == 80
    assert "Main St" in req["prompt"] and "Civic Center" in req["prompt"]
    assert ex.stats.snapshot()["accepted"] == 1 and ex.stats.snapshot()["calls"] == 1


def test_ollama_output_is_cleaned_of_quotes_and_labels() -> None:
    fake = FakeOllama(f'Rewritten: "{GOOD}"\nSecond line that must be ignored.')
    out = _ollama_explainer(fake).explain(_facts())
    assert out.source == "slm" and out.instruction == GOOD


def test_ollama_dropping_a_fact_falls_back_and_is_counted() -> None:
    fake = FakeOllama("Head east on Main St and you will be safe soon.")
    ex = _ollama_explainer(fake)
    out = ex.explain(_facts())
    assert out.source == "deterministic" and "220 m" in out.instruction
    snap = ex.stats.snapshot()
    assert snap["rejected"] == 1 and snap["accepted"] == 0
    assert ex.stats.rejections == ["dropped: 220 m, Civic Center"]


def test_ollama_timeout_falls_back_and_is_counted() -> None:
    fake = FakeOllama(httpx.ReadTimeout("slow model"))
    ex = _ollama_explainer(fake)
    out = ex.explain(_facts())
    assert out.source == "deterministic"
    snap = ex.stats.snapshot()
    assert snap["errors"] == 1 and str(snap["last_error"]).startswith("ReadTimeout")


def test_ollama_http_error_falls_back() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    ex = SlmExplainer("http://o.test", "m", 1.0, transport=httpx.MockTransport(handler))
    assert ex.explain(_facts()).source == "deterministic"
    assert ex.stats.errors == 1


def test_slm_explainer_falls_back_when_server_unreachable() -> None:
    ex = SlmExplainer("http://127.0.0.1:1", "qwen2.5:1.5b-instruct", timeout_s=0.3)
    out = ex.explain(_facts())
    assert out.source == "deterministic" and "Main St" in out.instruction


# --- llama.cpp backend + speculative decoding --------------------------------------------------


def test_llamacpp_reports_real_draft_acceptance_when_server_speculates() -> None:
    fake = FakeLlamaCpp(GOOD, draft=True)
    ex = SlmExplainer(
        "http://llama.test",
        "qwen2.5-1.5b",
        2.0,
        backend="llamacpp",
        speculative_n_max=12,
        transport=fake.transport(),
    )
    out = ex.explain(_facts())
    assert out.source == "slm" and out.timing is not None
    t = out.timing
    assert t.backend == "llamacpp" and t.speculative == "active"
    assert t.draft_n == 40 and t.draft_accepted == 26
    assert t.draft_acceptance == pytest.approx(0.65)
    assert t.tokens == 28 and t.tokens_per_s == pytest.approx(28 / 0.42)
    assert t.model.endswith("qwen2.5-1.5b-q4.gguf")  # the server's loaded model, not ours
    req = fake.requests[0]
    assert req["speculative.n_max"] == 12 and req["temperature"] == 0
    assert req["cache_prompt"] is True and req["n_predict"] == 80
    snap = ex.stats.snapshot()
    assert snap["speculative"] == "active" and snap["draft_acceptance"] == pytest.approx(0.65)


def test_llamacpp_without_draft_model_is_labelled_not_reported() -> None:
    fake = FakeLlamaCpp(GOOD, draft=False)
    ex = SlmExplainer("http://llama.test", "m", 2.0, backend="llamacpp", transport=fake.transport())
    out = ex.explain(_facts())
    assert out.source == "slm" and out.timing is not None
    assert out.timing.speculative == "not_reported"
    assert out.timing.draft_n is None and out.timing.draft_acceptance is None
    assert ex.stats.snapshot()["draft_acceptance"] is None


# --- cache + "SLM never chooses the route" --------------------------------------------------


def test_cache_means_an_unchanged_route_hits_the_model_once() -> None:
    fake = FakeOllama(GOOD)
    cached = CachedExplainer(_ollama_explainer(fake))
    a = cached.explain(_facts())
    b = cached.explain(_facts())
    assert a == b and len(fake.requests) == 1 and cached.hits == 1
    stats = cached.stats()
    assert stats["cache_hits"] == 1 and isinstance(stats["slm"], dict)
    flat = cached.flat_stats()
    assert flat["slm_calls"] == 1 and flat["slm_accepted"] == 1
    assert all(not isinstance(v, dict | list) for v in flat.values())


@pytest.mark.integration
def test_slm_never_changes_the_route(
    service: BlackboardService, routing_config: RoutingConfig
) -> None:
    """Same blackboard, same demand: every route's edges, length and cost are identical
    with the SLM on and off; only instruction text and `explanation_source` differ. The
    SLM is wired *after* path selection, so it cannot be an input to it."""
    population = Population.load(DEMO_POPULATION)
    det_nav = Navigator(service, routing_config, DeterministicExplainer())
    det_nav.set_population(population)
    det = det_nav.recompute(trigger="manual", scenario_t=0.0)

    fake = FakeOllama(GOOD)  # GOOD is wrong for these roads -> rejected -> fallback text;
    slm_nav = Navigator(service, routing_config, _ollama_explainer(fake))  # routes unaffected
    slm_nav.set_population(population)
    slm = slm_nav.recompute(trigger="manual", scenario_t=0.0)

    def key(r: Route) -> tuple[str, str, str]:
        return (r.origin_cell_id, r.safe_zone_id, r.crowd_group_id)

    a = {key(r): (tuple(r.edge_ids), round(r.length_m, 3), round(r.cost, 3)) for r in det.routes}
    b = {key(r): (tuple(r.edge_ids), round(r.length_m, 3), round(r.cost, 3)) for r in slm.routes}
    assert a and a == b
    assert len(fake.requests) == len(slm.routes)  # one model call per distinct route
    ev = service.list_agent_events(agent="navigator")[0]
    assert ev.data["slm_calls"] == len(slm.routes) and ev.data["slm_rejected"] == len(slm.routes)
