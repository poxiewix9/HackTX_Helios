"""6.1 Consensus: signing, clustering, trust/rate limits, and the override logic —
including the smoke-false-negative case: vision sees nothing on a road that is on a
route, two independent trusted phones pin smoke there, a community hazard is created,
the Cartographer penalizes/severs the edge and the Navigator reroutes, with the override
explained in `agent_events`."""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from shapely.geometry import LineString as ShapelyLine

from arco_agents.cartographer import Cartographer
from arco_agents.clustering import dbscan
from arco_agents.consensus import Consensus, ConsensusPolicy, TrustLedger
from arco_agents.explain import DeterministicExplainer
from arco_agents.navigator import Navigator
from arco_agents.penalties import PenaltyPolicy
from arco_agents.pipeline import AgentState, Pipeline
from arco_agents.population import Population
from arco_agents.reports import DeviceKey, canonical_bytes_of, iso_ms_z, verify_report
from arco_agents.routing_config import RoutingConfig
from arco_agents.settings import DEFAULT_CONSENSUS
from arco_blackboard.service import BlackboardService
from arco_blackboard.testing import make_hazard, square
from arco_schemas import BusEnvelope, CommunityReport, HazardClass, HazardEvent

DEMO_POPULATION = Path(__file__).resolve().parents[3] / "data/samples/lahaina/demo_population.json"
SMOKE, FLOOD, FIRE = HazardClass("smoke"), HazardClass("flooded_road"), HazardClass("fire")
LON, LAT = -156.6787, 20.8745  # Front St, Lahaina (sample bbox); test-only anchor


@pytest.fixture(scope="session")
def consensus_policy() -> ConsensusPolicy:
    return ConsensusPolicy.load(DEFAULT_CONSENSUS)


def _phone(label: str) -> DeviceKey:
    return DeviceKey.from_seed(label.encode())


def _pin(
    phone: DeviceKey,
    lon: float,
    lat: float,
    cls: HazardClass = SMOKE,
    at: datetime | None = None,
    note: str | None = None,
) -> CommunityReport:
    return phone.make_report(
        lon=lon, lat=lat, report_type="hazard_pin", hazard_class=cls, created_at=at, note=note
    )


# ----------------------------------------------------------------------------- signing


def test_sign_verify_and_tamper() -> None:
    phone = _phone("phone-01")
    r = _pin(phone, LON, LAT, note="smoke over the road")
    assert verify_report(r) == (True, "ok")
    assert r.reporter_hash == hashlib.sha256(base64.b64decode(r.device_pubkey)).hexdigest()
    moved = r.model_copy(
        update={"geom": r.geom.model_copy(update={"coordinates": [LON + 0.001, LAT]})}
    )
    assert verify_report(moved)[0] is False
    other = _phone("phone-02")
    forged = r.model_copy(update={"device_pubkey": other.pubkey_b64})
    ok, why = verify_report(forged)
    assert ok is False and "fingerprint" in why
    forged2 = r.model_copy(
        update={"device_pubkey": other.pubkey_b64, "reporter_hash": other.reporter_hash}
    )
    ok, why = verify_report(forged2)
    assert ok is False and "signature" in why
    # canonical form is stable and documented (mobile signs the same bytes)
    assert canonical_bytes_of(r) == canonical_bytes_of(
        CommunityReport.model_validate(r.model_dump(mode="json"))
    )
    assert (
        iso_ms_z(datetime(2026, 10, 24, 21, 0, 0, 123456, tzinfo=UTC)) == "2026-10-24T21:00:00.123Z"
    )
    assert _phone("phone-01").pubkey_b64 == phone.pubkey_b64  # deterministic scenario keys


# ----------------------------------------------------------------------------- clustering


def test_dbscan_groups_by_space_and_time() -> None:
    t0 = datetime(2026, 10, 24, 20, 0, tzinfo=UTC)
    a, b, c = _phone("a"), _phone("b"), _phone("c")
    near1 = _pin(a, LON, LAT, at=t0)
    near2 = _pin(b, LON + 0.0003, LAT, at=t0 + timedelta(seconds=60))  # ~31 m, 1 min
    far = _pin(c, LON + 0.01, LAT, at=t0)  # ~1 km
    late = _pin(c, LON, LAT, at=t0 + timedelta(seconds=3600))  # same spot, an hour later
    clusters = dbscan([near1, near2, far, late], eps_m=60, eps_s=300, min_pts=2)
    assert len(clusters) == 1
    assert {r.id for r in clusters[0].reports} == {near1.id, near2.id}
    assert clusters[0].reporter_hashes == {a.reporter_hash, b.reporter_hash}
    assert dbscan([near1, far], eps_m=60, eps_s=300, min_pts=2) == []
    # chain growth through core points: p0-p1-p2 each 40 m apart form one cluster
    chain = [_pin(_phone(str(i)), LON + i * 0.00038, LAT, at=t0) for i in range(3)]
    assert len(dbscan(chain, eps_m=60, eps_s=300, min_pts=2)) == 1
    assert dbscan([], eps_m=1, eps_s=1, min_pts=1) == []


# ----------------------------------------------------------------------------- trust


def test_trust_ledger_bounds_and_rate_limit(consensus_policy: ConsensusPolicy) -> None:
    led = TrustLedger(consensus_policy.trust, consensus_policy.rate_limit)
    assert led.get("x") == consensus_policy.trust.prior
    for _ in range(20):
        led.adjust("x", consensus_policy.trust.corroborated)
    assert led.get("x") == consensus_policy.trust.cap
    for _ in range(20):
        led.adjust("x", consensus_policy.trust.contradicted)
    assert led.get("x") == consensus_policy.trust.floor and led.counts("x") is False
    t0 = datetime.now(UTC)
    allowed = [
        led.allow("y", t0 + timedelta(seconds=i))
        for i in range(consensus_policy.rate_limit.max_reports + 2)
    ]
    assert allowed == [True] * consensus_policy.rate_limit.max_reports + [False, False]
    assert led.allow("y", t0 + timedelta(seconds=consensus_policy.rate_limit.window_s + 1)) is True


# ----------------------------------------------------------------------------- integration


@pytest.fixture
def agents(
    service: BlackboardService,
    policy: PenaltyPolicy,
    routing_config: RoutingConfig,
    consensus_policy: ConsensusPolicy,
) -> Pipeline:
    nav = Navigator(service, routing_config, DeterministicExplainer())
    nav.set_population(Population.load(DEMO_POPULATION))
    nav.load_graph()
    nav.recompute(trigger="manual", scenario_t=0.0)
    return Pipeline(Cartographer(service, policy), nav, Consensus(service, consensus_policy))


def _report_env(service: BlackboardService, report: CommunityReport, t: float) -> BusEnvelope:
    ev = service.add_report(report, scenario_t=t)
    return BusEnvelope(
        topic="report", published_at=datetime.now(UTC), publisher="tests", message=ev
    )


def _route_midpoint(pipeline: Pipeline, service: BlackboardService) -> tuple[float, float, int]:
    assert pipeline.navigator.last is not None
    victim = max(pipeline.navigator.last.routes, key=lambda r: r.length_m)
    edge = service.list_edges(ids=[victim.edge_ids[len(victim.edge_ids) // 2]])[0]
    mid = ShapelyLine(edge.geom.coordinates).interpolate(0.5, normalized=True)
    return float(mid.x), float(mid.y), edge.id


@pytest.mark.integration
def test_smoke_false_negative_two_trusted_reports_override_and_reroute(
    agents: Pipeline, service: BlackboardService
) -> None:
    lon, lat, edge_id = _route_midpoint(agents, service)
    first_routes = agents.navigator.last
    # vision is NEGATIVE here: no active vision hazard at all
    assert service.list_hazards(status="active") == []

    p1, p2 = _phone("phone-01"), _phone("phone-02")
    t0 = datetime.now(UTC)
    out1 = agents.run(_report_env(service, _pin(p1, lon, lat, at=t0), 10.0))
    assert out1["errors"] == []
    assert _kind(out1) == "report.pending" and _cons(out1)["hazards"] == 0
    assert service.list_hazards(status="active") == []  # one phone moves nothing
    # the same phone again is NOT independent
    out1b = agents.run(
        _report_env(service, _pin(p1, lon + 0.0001, lat, at=t0 + timedelta(seconds=20)), 11.0)
    )
    assert _kind(out1b) == "report.pending" and service.list_hazards(status="active") == []

    out2 = agents.run(
        _report_env(service, _pin(p2, lon + 0.0002, lat, at=t0 + timedelta(seconds=40)), 12.0)
    )
    assert out2["errors"] == []
    assert _kind(out2) == "community.hazard.created"
    active = service.list_hazards(status="active")
    assert (
        len(active) == 1 and active[0].source == "community" and active[0].hazard_class == "smoke"
    )
    assert active[0].camera_id is None and 0.6 <= active[0].confidence <= 0.9
    # the same run applied it: cartographer acted on the edge, navigator rerouted
    assert (
        out2["cartographer"] is not None
        and out2["cartographer"]["penalized"] + out2["cartographer"]["severed"] >= 1
    )
    assert out2["navigator"] is not None and out2["navigator"]["routes"] >= 1
    assert agents.navigator.last is not first_routes
    edge = service.list_edges(ids=[edge_id])[0]
    assert edge.status in ("penalized", "severed") and edge.current_cost >= edge.base_cost
    # the override is explained
    evs = service.list_agent_events(agent="consensus")
    kinds = [e.kind for e in evs]
    assert "community.hazard.created" in kinds and "report.pending" in kinds
    created = next(e for e in evs if e.kind == "community.hazard.created")
    assert "vision-negative" in created.reasoning and "2 independent" in created.reasoning
    assert len(created.report_ids) >= 2 and active[0].id in created.hazard_ids
    # trust: both reporters were rewarded for agreeing independently
    cons = agents.consensus
    assert cons is not None
    assert cons.trust.get(p1.reporter_hash) > cons.policy.trust.prior
    # when the bus delivers the same hazard.created event, the cartographer does not run twice
    hz_env = BusEnvelope(
        topic="hazard",
        published_at=datetime.now(UTC),
        publisher="tests",
        message=_hazard_event(out2),
    )
    before = service.graph_version()
    out3 = agents.run(hz_env)
    assert out3.get("cartographer") is None and service.graph_version() == before
    # the report rows carry the agent's trust opinion
    rep = service.list_reports(limit=10)
    assert all(
        r.trust_score > 0.5 for r in rep if r.reporter_hash in (p1.reporter_hash, p2.reporter_hash)
    )


@pytest.mark.integration
def test_pins_near_active_vision_hazard_corroborate_instead_of_duplicating(
    agents: Pipeline, service: BlackboardService
) -> None:
    lon, lat, _ = _route_midpoint(agents, service)
    vision = make_hazard(
        square(lon, lat, 0.0003),
        hazard_class="smoke",
        source="micro_vision",
        stream="micro",
        camera_id="cam-1",
        confidence=0.6,
        velocity_m_s=0.0,
    )
    service.upsert_hazard(vision, scenario_t=5.0)
    p1, p2 = _phone("c-01"), _phone("c-02")
    o1 = agents.run(_report_env(service, _pin(p1, lon, lat), 6.0))
    assert _kind(o1) == "hazard.corroborated"
    o2 = agents.run(_report_env(service, _pin(p2, lon, lat), 7.0))
    assert _kind(o2) == "hazard.corroborated"
    o3 = agents.run(_report_env(service, _pin(p2, lon, lat), 8.0))  # same phone again
    assert _kind(o3) == "report.ignored"
    h = service.get_hazard(vision.id)
    assert (
        h is not None
        and h.confidence == pytest.approx(0.7, abs=1e-6)
        and h.source == "micro_vision"
    )
    assert [x for x in service.list_hazards(status="active") if x.source == "community"] == []


@pytest.mark.integration
def test_denials_dispute_vision_but_never_delete_and_resolve_community(
    agents: Pipeline, service: BlackboardService
) -> None:
    lon, lat, _ = _route_midpoint(agents, service)
    vision = make_hazard(
        square(lon, lat, 0.0003),
        hazard_class="debris",
        source="micro_vision",
        stream="micro",
        camera_id="cam-1",
        confidence=0.8,
        velocity_m_s=0.0,
    )
    service.upsert_hazard(vision, scenario_t=5.0)
    d1, d2 = _phone("d-01"), _phone("d-02")
    o1 = agents.run(
        _report_env(
            service,
            d1.make_report(lon=lon, lat=lat, report_type="all_clear", target_hazard_id=vision.id),
            6.0,
        )
    )
    assert _kind(o1) == "report.pending"
    assert service.get_hazard(vision.id).status == "active"  # type: ignore[union-attr]
    o2 = agents.run(
        _report_env(
            service,
            d2.make_report(lon=lon, lat=lat, report_type="deny", target_hazard_id=vision.id),
            7.0,
        )
    )
    assert _kind(o2) == "hazard.disputed"
    h = service.get_hazard(vision.id)
    assert h is not None and h.status == "disputed" and h.confidence == pytest.approx(0.4, abs=1e-6)
    assert h.geom == vision.geom  # geometry untouched, nothing deleted
    assert out_kinds(service) >= {"hazard.disputed", "report.pending"}
    # a disputed hazard releases its penalties (cartographer treats non-active as release)
    assert out_has(o2, "cartographer")

    # community hazard: two pins create it, two independent denials resolve it
    lon2, lat2 = lon + 0.004, lat
    a, b = _phone("e-01"), _phone("e-02")
    t0 = datetime.now(UTC)
    agents.run(_report_env(service, _pin(a, lon2, lat2, cls=FLOOD, at=t0), 20.0))
    o = agents.run(
        _report_env(
            service,
            _pin(b, lon2 + 0.0001, lat2, cls=FLOOD, at=t0 + timedelta(seconds=10)),
            21.0,
        )
    )
    assert _kind(o) == "community.hazard.created"
    comm = next(x for x in service.list_hazards(status="active") if x.source == "community")
    agents.run(
        _report_env(
            service,
            d1.make_report(lon=lon2, lat=lat2, report_type="deny", target_hazard_id=comm.id),
            30.0,
        )
    )
    o4 = agents.run(
        _report_env(
            service,
            d2.make_report(lon=lon2, lat=lat2, report_type="deny", target_hazard_id=comm.id),
            31.0,
        )
    )
    assert _kind(o4) == "hazard.resolved_by_community"
    assert service.get_hazard(comm.id).status == "resolved"  # type: ignore[union-attr]
    cons = agents.consensus
    assert cons is not None and cons.trust.get(a.reporter_hash) < cons.trust.get(d1.reporter_hash)


@pytest.mark.integration
def test_bad_signature_and_rate_limit_are_rejected_and_logged(
    agents: Pipeline, service: BlackboardService
) -> None:
    lon, lat, _ = _route_midpoint(agents, service)
    phone = _phone("spam-01")
    good = _pin(phone, lon, lat)
    forged = good.model_copy(update={"note": "edited after signing"})
    o = agents.run(_report_env(service, forged, 1.0))
    assert _kind(o) == "report.rejected"
    t0 = datetime.now(UTC)
    kinds = []
    for i in range(7):
        r = _pin(phone, lon + i * 0.002, lat, at=t0 + timedelta(seconds=i))
        kinds.append(_kind(agents.run(_report_env(service, r, 2.0 + i))))
    assert kinds[:5] == ["report.pending"] * 5 and kinds[5:] == ["report.rejected"] * 2
    cons = agents.consensus
    assert cons is not None and cons.trust.get(phone.reporter_hash) < cons.policy.trust.prior
    evs = service.list_agent_events(agent="consensus")
    rejected = [e for e in evs if e.kind == "report.rejected"]
    assert any(e.data.get("reason") == "signature" for e in rejected)
    assert any(e.data.get("reason") == "rate_limit" for e in rejected)
    assert service.list_hazards(status="active") == []


@pytest.mark.integration
def test_vision_arrival_corroborates_pending_pins(
    agents: Pipeline, service: BlackboardService
) -> None:
    lon, lat, _ = _route_midpoint(agents, service)
    phone = _phone("v-01")
    agents.run(_report_env(service, _pin(phone, lon, lat, cls=FIRE), 1.0))
    cons = agents.consensus
    assert cons is not None and cons.trust.get(phone.reporter_hash) == cons.policy.trust.prior
    vision = make_hazard(
        square(lon, lat, 0.0003),
        hazard_class="fire",
        source="micro_vision",
        stream="micro",
        camera_id="cam-1",
        confidence=0.9,
        velocity_m_s=0.0,
    )
    ev: HazardEvent = service.upsert_hazard(vision, scenario_t=2.0)
    out = agents.run(
        BusEnvelope(topic="hazard", published_at=datetime.now(UTC), publisher="tests", message=ev)
    )
    assert out["cartographer"] is not None  # vision hazard still flows to the cartographer
    assert cons.trust.get(phone.reporter_hash) > cons.policy.trust.prior
    assert list(cons.pending.get(FIRE) or []) == []
    assert "report.corroborated_by_vision" in out_kinds(service)


@pytest.mark.integration
def test_consensus_crash_does_not_stop_cartographer(
    agents: Pipeline, service: BlackboardService, monkeypatch: pytest.MonkeyPatch
) -> None:
    lon, lat, _ = _route_midpoint(agents, service)
    cons = agents.consensus
    assert cons is not None

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("consensus exploded")

    monkeypatch.setattr(cons, "observe_hazard", boom)
    monkeypatch.setattr(cons, "on_report", boom)
    hazard = make_hazard(square(lon, lat, 0.0006), velocity_m_s=0.0)
    ev = service.upsert_hazard(hazard, scenario_t=3.0)
    out = agents.run(
        BusEnvelope(topic="hazard", published_at=datetime.now(UTC), publisher="tests", message=ev)
    )
    assert out["cartographer"] is not None and out["cartographer"]["severed"] >= 1
    out_r = agents.run(_report_env(service, _pin(_phone("z"), lon, lat), 4.0))
    assert out_r["consensus"] is None and any("consensus" in e for e in out_r["errors"])
    assert uuid4() is not None


def out_kinds(service: BlackboardService) -> set[str]:
    return {e.kind for e in service.list_agent_events(agent="consensus")}


def out_has(out: AgentState, key: str) -> bool:
    return out.get(key) is not None


def _hazard_event(out: AgentState) -> HazardEvent:
    ev = out.get("hazard_event")
    assert ev is not None
    return ev


def _kind(out: AgentState) -> str:
    cons = out.get("consensus")
    assert cons is not None, out.get("errors")
    return str(cons["kind"])


def _cons(out: AgentState) -> dict[str, Any]:
    cons = out.get("consensus")
    assert cons is not None, out.get("errors")
    return cons
