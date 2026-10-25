"""Route explanations: deterministic templates always; a local SLM optionally.

The SLM never chooses routes. It only rewrites the deterministic facts into friendlier
text, and any failure (disabled, unreachable, timeout, bad output, dropped facts) falls
back to the template. Results are cached by route hash so an unchanged route is never
re-explained.

Two local runtimes are supported behind `SlmBackend`:

* **Ollama** (`/api/generate`). Ollama does not expose speculative decoding, so the
  timing it returns is labelled `speculative="unsupported"`.
* **llama.cpp server** (`/completion`). When the server was started with a draft model
  (`llama-server -m <target> -md <draft>`) it reports `draft_n` / `draft_n_accepted` in
  `timings`; we forward the request-level `speculative.n_max` knob and report the real
  acceptance rate. A server without a draft model answers without those fields and is
  labelled `speculative="not_reported"` — never assumed.

Nothing here invents numbers: every latency / token figure comes from the runtime's own
response, and `SlmStats` counts what actually happened (calls, fallbacks, rejections).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx

from arco_agents.geo import bearing_deg, compass
from arco_agents.logging import get_logger
from arco_schemas import RoadEdge

log = get_logger("agents.explain")

ExplanationSource = Literal["slm", "deterministic"]
SpeculativeState = Literal["active", "unsupported", "not_reported"]
SlmBackendName = Literal["ollama", "llamacpp"]

MAX_WORDS = 60


@dataclass(frozen=True)
class RouteFacts:
    """Everything an explanation may mention. Built from the computed route only."""

    cell_id: str
    zone_id: str
    zone_name: str
    group_size: int
    length_m: float
    cost_s: float
    edges: tuple[RoadEdge, ...]
    avoided_severed: int
    avoided_penalized: int
    load_ratio: float
    split_fraction: float

    def route_hash(self) -> str:
        payload = {
            "cell": self.cell_id,
            "zone": self.zone_id,
            "edges": [e.id for e in self.edges],
            "status": [e.status for e in self.edges],
            "severed": self.avoided_severed,
            "frac": round(self.split_fraction, 2),
        }
        return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class SlmTiming:
    """What the runtime reported for one generation. Nothing is estimated locally except
    `latency_ms`, which is wall time around the HTTP call."""

    backend: SlmBackendName
    model: str
    latency_ms: float
    tokens: int
    tokens_per_s: float | None
    speculative: SpeculativeState
    draft_n: int | None = None
    draft_accepted: int | None = None

    @property
    def draft_acceptance(self) -> float | None:
        if self.draft_n and self.draft_accepted is not None:
            return self.draft_accepted / self.draft_n
        return None


@dataclass(frozen=True)
class Explanation:
    instruction: str
    rationale: str
    source: ExplanationSource
    timing: SlmTiming | None = None


class Explainer(Protocol):
    def explain(self, facts: RouteFacts) -> Explanation: ...


def _road_legs(edges: tuple[RoadEdge, ...]) -> list[tuple[str, float, float]]:
    """Collapse consecutive edges with the same name into (name, metres, bearing)."""
    legs: list[tuple[str, float, float]] = []
    for e in edges:
        name = e.name or (e.highway or "unnamed road").replace("_", " ")
        c0, c1 = e.geom.coordinates[0], e.geom.coordinates[-1]
        b = bearing_deg(c0[0], c0[1], c1[0], c1[1])
        if legs and legs[-1][0] == name:
            prev = legs[-1]
            legs[-1] = (name, prev[1] + e.length_m, prev[2])
        else:
            legs.append((name, e.length_m, b))
    return legs


def _fmt_m(m: float) -> str:
    return f"{m / 1000:.1f} km" if m >= 950 else f"{int(round(m / 10.0) * 10)} m"


def required_facts(facts: RouteFacts) -> list[str]:
    """Strings a rewrite must keep verbatim: the road names and distances the template
    mentions, and the destination. Used to reject SLM output that drops or alters facts."""
    legs = _road_legs(facts.edges)
    keep: list[str] = []
    for name, metres, _b in legs[:3]:
        keep.append(name)
        keep.append(_fmt_m(metres))
    keep.append(facts.zone_name)
    return keep


class DeterministicExplainer:
    """Template text from route facts. Always available; the fallback for the SLM."""

    def explain(self, facts: RouteFacts) -> Explanation:
        legs = _road_legs(facts.edges)
        if not legs:
            return Explanation(
                f"You are already at {facts.zone_name}.",
                "Origin is inside the safe zone.",
                "deterministic",
            )
        first = legs[0]
        parts = [f"Head {compass(first[2])} on {first[0]} for {_fmt_m(first[1])}"]
        for name, metres, _b in legs[1:3]:
            parts.append(f"continue onto {name} for {_fmt_m(metres)}")
        if len(legs) > 3:
            rest = len(legs) - 3
            parts.append(f"follow signs for {rest} more turn{'s' if rest != 1 else ''}")
        minutes = max(1, round(facts.cost_s / 60))
        instruction = (
            f"{'; '.join(parts)}; arrive at {facts.zone_name} ({_fmt_m(facts.length_m)}, "
            f"~{minutes} min)."
        )
        reasons: list[str] = []
        if facts.avoided_severed:
            reasons.append(f"avoids {facts.avoided_severed} closed road segment(s)")
        if facts.avoided_penalized:
            reasons.append(f"routes around {facts.avoided_penalized} degraded segment(s)")
        if facts.split_fraction < 0.999:
            reasons.append(
                f"carries {facts.split_fraction:.0%} of this area's people to spread crowds"
            )
        reasons.append(f"peak road load {facts.load_ratio:.0%} of capacity")
        return Explanation(instruction, "; ".join(reasons) + ".", "deterministic")


# ----------------------------------------------------------------------------- backends


class SlmBackend(Protocol):
    name: SlmBackendName
    model: str

    def generate(self, prompt: str, max_tokens: int) -> tuple[str, SlmTiming]: ...


def _as_int(v: object) -> int | None:
    return int(v) if isinstance(v, int | float) else None


def _as_float(v: object) -> float | None:
    return float(v) if isinstance(v, int | float) else None


class OllamaBackend:
    """`POST /api/generate` with greedy decoding. Ollama reports `eval_count` and
    `eval_duration` (ns); it has no speculative-decoding API."""

    name: SlmBackendName = "ollama"

    def __init__(
        self,
        url: str,
        model: str,
        timeout_s: float,
        *,
        keep_alive: str = "10m",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.keep_alive = keep_alive
        self._client = httpx.Client(timeout=timeout_s, transport=transport)

    def generate(self, prompt: str, max_tokens: int) -> tuple[str, SlmTiming]:
        t0 = time.perf_counter()
        r = self._client.post(
            f"{self.url}/api/generate",
            json={
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "keep_alive": self.keep_alive,
                "options": {"temperature": 0, "num_predict": max_tokens, "seed": 7},
            },
        )
        r.raise_for_status()
        latency = (time.perf_counter() - t0) * 1000
        body: dict[str, Any] = r.json()
        tokens = _as_int(body.get("eval_count")) or 0
        dur_ns = _as_int(body.get("eval_duration"))
        tps = tokens / (dur_ns / 1e9) if dur_ns else None
        timing = SlmTiming(
            backend="ollama",
            model=self.model,
            latency_ms=latency,
            tokens=tokens,
            tokens_per_s=tps,
            speculative="unsupported",
        )
        return str(body.get("response", "")), timing


class LlamaCppBackend:
    """`POST /completion` on llama.cpp's server. Speculative decoding happens server-side
    when a draft model was loaded; `timings.draft_n` / `draft_n_accepted` tell us whether
    it did and how well the draft was accepted."""

    name: SlmBackendName = "llamacpp"

    def __init__(
        self,
        url: str,
        model: str,
        timeout_s: float,
        *,
        speculative_n_max: int = 16,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.speculative_n_max = speculative_n_max
        self._client = httpx.Client(timeout=timeout_s, transport=transport)

    def generate(self, prompt: str, max_tokens: int) -> tuple[str, SlmTiming]:
        t0 = time.perf_counter()
        r = self._client.post(
            f"{self.url}/completion",
            json={
                "prompt": prompt,
                "n_predict": max_tokens,
                "temperature": 0,
                "cache_prompt": True,
                "stop": ["\n"],
                "speculative.n_max": self.speculative_n_max,
            },
        )
        r.raise_for_status()
        latency = (time.perf_counter() - t0) * 1000
        body: dict[str, Any] = r.json()
        timings_raw = body.get("timings")
        timings: dict[str, Any] = timings_raw if isinstance(timings_raw, dict) else {}
        tokens = _as_int(timings.get("predicted_n")) or _as_int(body.get("tokens_predicted")) or 0
        draft_n = _as_int(timings.get("draft_n"))
        draft_acc = _as_int(timings.get("draft_n_accepted"))
        state: SpeculativeState = "active" if draft_n else "not_reported"
        timing = SlmTiming(
            backend="llamacpp",
            model=str(body.get("model") or self.model),
            latency_ms=latency,
            tokens=tokens,
            tokens_per_s=_as_float(timings.get("predicted_per_second")),
            speculative=state,
            draft_n=draft_n,
            draft_accepted=draft_acc if draft_n else None,
        )
        return str(body.get("content", "")), timing


def make_backend(
    backend: SlmBackendName,
    url: str,
    model: str,
    timeout_s: float,
    *,
    speculative_n_max: int = 16,
    transport: httpx.BaseTransport | None = None,
) -> SlmBackend:
    if backend == "llamacpp":
        return LlamaCppBackend(
            url, model, timeout_s, speculative_n_max=speculative_n_max, transport=transport
        )
    return OllamaBackend(url, model, timeout_s, transport=transport)


# ---------------------------------------------------------------------------- explainer


@dataclass
class SlmStats:
    calls: int = 0
    accepted: int = 0
    rejected: int = 0  # answered, but dropped or altered a required fact
    errors: int = 0  # unreachable / timeout / bad JSON
    latency_ms_total: float = 0.0
    tokens_total: int = 0
    draft_n_total: int = 0
    draft_accepted_total: int = 0
    last_speculative: SpeculativeState | None = None
    last_error: str | None = None
    rejections: list[str] = field(default_factory=list)

    def snapshot(self) -> dict[str, object]:
        acc = self.draft_accepted_total / self.draft_n_total if self.draft_n_total else None
        return {
            "calls": self.calls,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "errors": self.errors,
            "mean_latency_ms": round(self.latency_ms_total / self.accepted, 1)
            if self.accepted
            else None,
            "tokens_total": self.tokens_total,
            "speculative": self.last_speculative,
            "draft_acceptance": round(acc, 3) if acc is not None else None,
            "last_error": self.last_error,
        }


_QUOTES = "\"'`\u201c\u201d\u2018\u2019"  # ascii + curly quotes


def _clean(text: str) -> str:
    line = text.strip().split("\n")[0].strip()
    line = line.strip(_QUOTES).strip()
    line = re.sub(r"^(rewritten|instruction)\s*:\s*", "", line, flags=re.IGNORECASE)
    return line.strip(_QUOTES).strip()


def check_rewrite(text: str, facts: RouteFacts) -> str | None:
    """Return a rejection reason if `text` is not a faithful rewrite, else None."""
    if not text:
        return "empty"
    if len(text.split()) > MAX_WORDS:
        return f"too long ({len(text.split())} words)"
    missing = [k for k in required_facts(facts) if k not in text]
    if missing:
        return "dropped: " + ", ".join(missing)
    return None


class SlmExplainer:
    """Backend-agnostic rewrite of the deterministic explanation. Any problem -> fallback.

    With `ARCO_SLM_ENABLED=false` this class is never constructed."""

    def __init__(
        self,
        url: str,
        model: str,
        timeout_s: float,
        *,
        backend: SlmBackendName = "ollama",
        max_tokens: int = 80,
        speculative_n_max: int = 16,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.backend = make_backend(
            backend,
            url,
            model,
            timeout_s,
            speculative_n_max=speculative_n_max,
            transport=transport,
        )
        self.max_tokens = max_tokens
        self.fallback = DeterministicExplainer()
        self.stats = SlmStats()

    @staticmethod
    def prompt_for(base: Explanation) -> str:
        return (
            "You write one-sentence evacuation instructions for the public. Rewrite the "
            "following route instruction so it is calm, concrete and under 40 words. Keep "
            "every road name, distance and the destination name exactly as written. Do not "
            "add roads, numbers or advice. Answer with the sentence only.\n\n"
            f"Instruction: {base.instruction}\nWhy: {base.rationale}\n\nRewritten:"
        )

    def explain(self, facts: RouteFacts) -> Explanation:
        base = self.fallback.explain(facts)
        self.stats.calls += 1
        try:
            raw, timing = self.backend.generate(self.prompt_for(base), self.max_tokens)
        except Exception as exc:
            self.stats.errors += 1
            self.stats.last_error = f"{type(exc).__name__}: {exc}"[:200]
            log.warning("slm unavailable; deterministic fallback", error=self.stats.last_error)
            return base
        text = _clean(raw)
        reason = check_rewrite(text, facts)
        if reason is not None:
            self.stats.rejected += 1
            if len(self.stats.rejections) < 20:
                self.stats.rejections.append(reason)
            log.warning("slm rewrite rejected; deterministic fallback", reason=reason)
            return base
        self.stats.accepted += 1
        self.stats.latency_ms_total += timing.latency_ms
        self.stats.tokens_total += timing.tokens
        self.stats.last_speculative = timing.speculative
        if timing.draft_n:
            self.stats.draft_n_total += timing.draft_n
            self.stats.draft_accepted_total += timing.draft_accepted or 0
        return Explanation(text, base.rationale, "slm", timing)


class CachedExplainer:
    def __init__(self, inner: Explainer) -> None:
        self.inner = inner
        self.cache: dict[str, Explanation] = {}
        self.hits = 0

    def explain(self, facts: RouteFacts) -> Explanation:
        key = facts.route_hash()
        if key in self.cache:
            self.hits += 1
            return self.cache[key]
        out = self.inner.explain(facts)
        self.cache[key] = out
        return out

    def stats(self) -> dict[str, object]:
        out: dict[str, object] = {"cache_hits": self.hits, "cache_size": len(self.cache)}
        if isinstance(self.inner, SlmExplainer):
            out["slm"] = self.inner.stats.snapshot()
        return out

    def flat_stats(self) -> dict[str, float | int | str | None]:
        """Scalar view for `AgentEvent.data` (SLM figures only when an SLM is wired)."""
        if not isinstance(self.inner, SlmExplainer):
            return {}
        snap = self.inner.stats.snapshot()
        out: dict[str, float | int | str | None] = {}
        for k, v in snap.items():
            if isinstance(v, int | float | str) or v is None:
                out[f"slm_{k}"] = v
        return out
