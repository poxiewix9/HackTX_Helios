"""10.2 `make demo-smoke`: the full Lahaina scenario end to end against the running stack.

Host-side, black-box, over HTTP only — nothing is mocked. The simulator plays the
scenario at high speed through the real blackboard and the real agents; the test asserts
what the system *claims* in its own data (agent events with reasoning, routes with text,
expected blocked edges, the consensus path, the cellular chaos event, the label on the
player, no raw video path) rather than anything it computes itself.

Needs `docker compose up -d --wait` (or `make up`). Without the simulator reachable the
module skips with the reason; `make demo-smoke` brings the stack up first.
"""

from __future__ import annotations

import os
import time
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

BB = os.environ.get("BLACKBOARD_URL", "http://localhost:8001")
BASE = os.environ.get("BASE_STATION_URL", "http://localhost:8002")
AGENTS = os.environ.get("AGENTS_URL", "http://localhost:8003")
SIM = os.environ.get("SIMULATOR_URL", "http://localhost:8004")
BENCH = os.environ.get("BENCHMARK_URL", "http://localhost:8005")
DASHBOARD = os.environ.get("DASHBOARD_URL")  # optional; set by `make demo`
SCENARIO = os.environ.get("ARCO_DEFAULT_SCENARIO", "lahaina_2023")
SPEED = float(os.environ.get("DEMO_SMOKE_SPEED", "200"))
SIMULATION_LABEL = "Simulated on real event geometry"


def _reachable(url: str) -> bool:
    try:
        return httpx.get(f"{url}/health", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(
    not _reachable(SIM), reason=f"simulator not reachable at {SIM}; run `make up` first"
)


@pytest.fixture(scope="module")
def client() -> Iterator[httpx.Client]:
    with httpx.Client(timeout=30) as c:
        yield c


@pytest.fixture(scope="module")
def played(client: httpx.Client) -> dict[str, Any]:
    """Load the scenario from t=0 and play it to the end once for the whole module."""
    for url in (BB, BASE, AGENTS, BENCH):
        assert _reachable(url), f"{url}/health not ok"
    client.post(f"{SIM}/pause")
    r = client.post(f"{SIM}/load", json={"scenario_id": SCENARIO})
    r.raise_for_status()
    state = r.json()
    assert state["loaded"] and state["scenario_id"] == SCENARIO
    duration = float(state["duration_s"])
    client.post(f"{SIM}/seek", json={"t": 0}).raise_for_status()
    client.post(f"{SIM}/speed", json={"speed": SPEED}).raise_for_status()
    client.post(f"{SIM}/chaos/cellular", json={"up": True, "reason": "smoke reset"})
    # Bus sequence number at the start: everything this run publishes has seq > start_seq.
    start_seq = _last_seq(client)
    t0 = time.monotonic()
    t0_wall = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    client.post(f"{SIM}/play").raise_for_status()
    budget_s = duration / SPEED * 3 + 60  # 3x slack for agent compute + model start-up
    last: dict[str, Any] = state
    while time.monotonic() - t0 < budget_s:
        last = client.get(f"{SIM}/state").json()
        if last["t"] >= duration or (not last["playing"] and last["t"] > 0):
            break
        time.sleep(1.0)
    assert last["t"] >= duration, f"scenario did not finish within {budget_s:.0f}s: {last}"
    # Let the agents drain the last events before we read the blackboard: the scripted
    # `hazard_resolve` at the end of the timeline must have produced its agent event.
    deadline = time.monotonic() + 30
    events: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        events = _agent_events_since(client, t0_wall)
        kinds = Counter(e["kind"] for e in events)
        if any(k.startswith("hazard.resolved") for k in kinds):
            break
        time.sleep(1.0)
    bus = _bus_since(client, start_seq)
    return {
        "state": last,
        "duration": duration,
        "wall_s": time.monotonic() - t0,
        "events": events,
        "kinds": Counter(e["kind"] for e in events),
        "bus": bus,
        "bus_types": Counter(str(env["message"].get("type")) for env in bus),
    }


def _last_seq(client: httpx.Client) -> int:
    seq = 0
    since = 0
    while True:
        page = client.get(f"{BB}/events", params={"since_seq": since, "limit": 5000}).json()
        if not page:
            return seq
        seq = max(int(env["message"]["seq"]) for env in page)
        since = seq


def _bus_since(client: httpx.Client, start_seq: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    since = start_seq
    while True:
        page = client.get(f"{BB}/events", params={"since_seq": since, "limit": 5000}).json()
        if not page:
            return out
        out.extend(page)
        since = max(int(env["message"]["seq"]) for env in page)


def _agent_events_since(client: httpx.Client, wall_iso: str) -> list[dict[str, Any]]:
    """Agent events written during this run (the endpoint returns newest first, capped)."""
    events: list[dict[str, Any]] = client.get(f"{BB}/agent-events", params={"limit": 5000}).json()
    return [e for e in events if e["wall_t"] >= wall_iso]


def test_player_is_labelled_simulated_and_finished(played: dict[str, Any]) -> None:
    st = played["state"]
    assert st["label"] == SIMULATION_LABEL
    assert st["t"] >= played["duration"]
    assert st["timeline_cursor"] == st["timeline_total"]


def test_cartographer_applied_every_scripted_hazard_with_reasoning(
    played: dict[str, Any], client: httpx.Client
) -> None:
    scenario = client.get(f"{SIM}/scenario").json()
    appear = sum(1 for e in scenario["timeline"] if e["type"] == "hazard_appear")
    kinds: Counter[str] = played["kinds"]
    applied = sum(v for k, v in kinds.items() if k.startswith("hazard.applied"))
    assert applied >= appear, kinds
    # `.noop` suffix = the scripted resolve found no edges left to restore (another front
    # still covers them); either way the Cartographer processed it and explained itself.
    assert any(k.startswith("hazard.resolved") for k in kinds), kinds
    for ev in played["events"]:
        assert ev["reasoning"], f"agent event without reasoning: {ev['kind']}"
        assert ev["agent"] in {"cartographer", "navigator", "consensus"}


def test_expected_blocked_edges_are_severed(client: httpx.Client, played: dict[str, Any]) -> None:
    chk = client.get(f"{SIM}/check").json()
    assert chk["expected"], "scenario has no expected_blocked_edges"
    assert chk["ok"], f"missing severed edges: {chk['missing'][:10]}"


def test_navigator_rerouted_and_every_route_has_text(
    client: httpx.Client, played: dict[str, Any]
) -> None:
    kinds: Counter[str] = played["kinds"]
    recomputes = sum(v for k, v in kinds.items() if k.startswith("routes.recomputed"))
    assert recomputes >= 3, kinds
    routes = client.get(f"{BB}/routes").json()
    assert routes, "no routes on the blackboard after the scenario"
    for r in routes:
        assert r["instruction"] and r["rationale"]
        assert r["explanation_source"] in {"deterministic", "slm"}
        assert len(r["edge_ids"]) > 0 and r["length_m"] > 0
    health = client.get(f"{AGENTS}/health").json()
    assert health["recent_errors"] == [], health["recent_errors"]
    assert "explainer" in health and health["explainer"]["cache_size"] >= 0
    if not health["slm_enabled"]:
        assert {r["explanation_source"] for r in routes} == {"deterministic"}


def test_community_reports_went_through_consensus(
    client: httpx.Client, played: dict[str, Any]
) -> None:
    kinds: Counter[str] = played["kinds"]
    reports = client.get(f"{BB}/reports").json()
    assert len(reports) >= 2
    assert all(r["signature"] and r["device_pubkey"] and r["reporter_hash"] for r in reports)
    consensus = [e for e in played["events"] if e["agent"] == "consensus"]
    assert consensus, kinds
    assert kinds.get("community.hazard.created", 0) >= 1, kinds


def test_cellular_chaos_and_clock_went_over_the_bus(
    played: dict[str, Any], client: httpx.Client
) -> None:
    """The scripted `cell_network_down` becomes a `network.cellular(down)` bus event and
    the simulator's clock ticks reach every subscriber through the blackboard."""
    timeline = client.get(f"{SIM}/scenario").json()["timeline"]
    scripted = any(e["type"] == "cell_network_down" for e in timeline)
    assert scripted, "scenario has no cell_network_down event"
    types: Counter[str] = played["bus_types"]
    assert types["clock.tick"] > 10, types
    downs = [
        env["message"]
        for env in played["bus"]
        if env["message"].get("type") == "network.cellular" and not env["message"]["cellular_up"]
    ]
    assert downs, types
    assert downs[0]["scenario_t"] == pytest.approx(300.0) and downs[0]["reason"]


def test_no_raw_video_leaves_the_local_network(client: httpx.Client) -> None:
    """The only video in the system is the simulator's synthetic MJPEG, served on the LAN;
    the base station accepts *features* over `/ws/features` and reports frame counts, not
    pixels, and the edge node refuses non-local base stations unless explicitly allowed."""
    base = client.get(f"{BASE}/health").json()
    assert "frames" in base and "ws_clients" in base
    assert base["blackboard_url"].startswith(
        ("http://blackboard", "http://localhost", "http://127.")
    )
    bb = client.get(f"{BB}/openapi.json").json()
    paths = " ".join(bb["paths"].keys())
    assert "upload" not in paths and "video" not in paths and "frame" not in paths


def test_benchmark_telemetry_is_labelled_and_hardware_is_read_from_the_machine(
    client: httpx.Client,
) -> None:
    h = client.get(f"{BENCH}/health").json()
    assert h["live_running"] or h["replay_run_id"], h
    hw = h["replay_hardware"]
    assert hw["hostname"] and hw["cpu"] and hw["platform"]


@pytest.mark.skipif(not DASHBOARD, reason="DASHBOARD_URL not set")
def test_dashboard_serves(client: httpx.Client) -> None:
    assert DASHBOARD is not None
    r = client.get(DASHBOARD, timeout=60)
    assert r.status_code == 200 and "ARCO" in r.text
