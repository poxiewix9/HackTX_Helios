"""3.1 Cartographer: policy math (unit) and sever/penalize/restore on the Lahaina graph."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from shapely.geometry import LineString as ShapelyLine

from arco_agents.cartographer import Cartographer, plan_edge_update
from arco_agents.geo import buffer_m, haversine_m, to_shape
from arco_agents.penalties import SEVER_PENALTY, EdgeState, PenaltyPolicy, combine
from arco_blackboard.service import BlackboardService
from arco_blackboard.testing import make_hazard, square
from arco_schemas import Hazard, HazardClass, Polygon, RoadEdge
from arco_schemas.hazard_classes import HAZARD_CLASSES

# --- unit: policy ---------------------------------------------------------------


def test_policy_covers_every_hazard_class(policy: PenaltyPolicy) -> None:
    assert set(policy.classes) == set(HAZARD_CLASSES)
    for hc, info in HAZARD_CLASSES.items():
        assert (policy.for_class(hc).buffer is not None) == info.expands


def test_sever_requires_overlap_and_confidence(policy: PenaltyPolicy) -> None:
    fire = policy.for_class(HazardClass.FIRE_FRONT)
    h = make_hazard(square(0, 0, 0.001), confidence=0.9, severity=1.0)
    assert fire.decide(h, 0.5)[0] == "sever"
    assert fire.decide(h, 0.5)[1] == SEVER_PENALTY
    # below the sever fraction -> penalize instead of cutting the whole edge
    decision, mult, cap = fire.decide(h, 0.02)
    assert decision == "penalize" and 1.0 < mult <= fire.max_multiplier and cap == 1.0
    # below min confidence -> ignore entirely
    assert fire.decide(h.model_copy(update={"confidence": 0.2}), 0.9)[0] == "ignore"
    # confident enough to penalize but not to sever
    assert fire.decide(h.model_copy(update={"confidence": 0.45}), 0.9)[0] == "penalize"


def test_penalize_formula_matches_yaml_comment(policy: PenaltyPolicy) -> None:
    smoke = policy.for_class(HazardClass.SMOKE)
    h = make_hazard(square(0, 0, 0.001), hazard_class="smoke", confidence=0.8, severity=0.5)
    f = 0.5
    decision, mult, _ = smoke.decide(h, f)
    assert decision == "penalize"
    expected = 1 + (smoke.max_multiplier - 1) * (f * 0.8) * (0.5 + 0.5 * 0.5)
    assert mult == pytest.approx(expected)
    # full overlap, full confidence, max severity -> exactly max_multiplier
    full = make_hazard(square(0, 0, 0.001), hazard_class="smoke", confidence=1.0, severity=1.0)
    assert smoke.decide(full, 1.0)[1] == pytest.approx(smoke.max_multiplier)


def test_capacity_action_scales_capacity(policy: PenaltyPolicy) -> None:
    crowd = policy.for_class(HazardClass.CROWD_BOTTLENECK)
    h = make_hazard(square(0, 0, 0.001), hazard_class="crowd_bottleneck", confidence=1.0)
    decision, mult, cap = crowd.decide(h, 1.0)
    assert decision == "capacity"
    assert cap == pytest.approx(crowd.capacity_factor)
    assert mult > 1.0
    assert crowd.decide(h, 0.5)[2] == pytest.approx(crowd.capacity_factor**0.5)


def test_combine_is_any_sever_max_multiplier_min_capacity() -> None:
    st = combine([("penalize", 2.0, 1.0), ("penalize", 3.0, 1.0), ("capacity", 1.5, 0.4)])
    assert st == EdgeState(severed=False, multiplier=3.0, capacity_factor=0.4)
    assert combine([("penalize", 2.0, 1.0), ("sever", math.inf, 1.0)]).severed
    assert combine([("ignore", 1.0, 1.0)]) == EdgeState(False, 1.0, 1.0)
    assert combine([]) == EdgeState(False, 1.0, 1.0)


def test_buffer_grows_with_velocity_and_elapsed_and_is_capped(policy: PenaltyPolicy) -> None:
    b = policy.for_class(HazardClass.FIRE_FRONT).buffer
    assert b is not None
    fresh = b.distance_m(0.5, 0.0)
    assert fresh == pytest.approx(b.base_margin_m + 0.5 * b.horizon_s)
    assert b.distance_m(0.5, 120.0) > fresh
    assert b.distance_m(5.0, 10_000.0) == b.max_buffer_m
    assert b.distance_m(None, 0.0) == pytest.approx(
        b.base_margin_m + b.default_velocity_m_s * b.horizon_s
    )
    assert policy.for_class(HazardClass.DEBRIS).buffer is None


def test_buffer_m_is_metric() -> None:
    poly = to_shape(square(-156.67, 20.88, 0.0005))
    grown = buffer_m(poly, 200.0)
    minx, _miny, maxx, _maxy = grown.bounds
    width_m = haversine_m(minx, 20.88, maxx, 20.88)
    # original ~111 m wide + 2 x 200 m buffer
    assert width_m == pytest.approx(111.3 * 1.0 + 400.0, rel=0.03)
    assert grown.contains(poly)


def _edge(**kw: object) -> RoadEdge:
    base = {
        "id": 1,
        "geom": {"type": "LineString", "coordinates": [[0, 0], [0.001, 0]]},
        "u": 1,
        "v": 2,
        "length_m": 111.0,
        "base_cost": 10.0,
        "current_cost": 10.0,
        "status": "open",
        "capacity_estimate": 100.0,
    }
    base.update(kw)
    return RoadEdge.model_validate(base)


def test_plan_edge_update_is_a_pure_diff() -> None:
    e = _edge()
    assert plan_edge_update(e, EdgeState(False, 1.0, 1.0)) is None
    assert plan_edge_update(e, EdgeState(True, 1.0, 1.0)) == ("severed", 10.0, None)
    assert plan_edge_update(e, EdgeState(False, 2.5, 1.0)) == ("penalized", 25.0, None)
    assert plan_edge_update(e, EdgeState(False, 1.0, 0.4)) == ("open", 10.0, 0.4)
    sev = _edge(status="severed")
    assert plan_edge_update(sev, EdgeState(False, 1.0, 1.0)) == ("open", 10.0, None)
    assert plan_edge_update(sev, EdgeState(True, 1.0, 1.0)) is None


# --- integration: Lahaina graph -----------------------------------------------------


def _edge_midpoint_square(edge: RoadEdge, half_deg: float) -> Polygon:
    line = ShapelyLine(edge.geom.coordinates)
    mid = line.interpolate(0.5, normalized=True)
    return square(mid.x, mid.y, half_deg)


def _longest_edge(service: BlackboardService) -> RoadEdge:
    return max(service.list_edges(), key=lambda e: e.length_m)


def _post(service: BlackboardService, geom: Polygon, **overrides: Any) -> Hazard:
    """Hazards reach the Cartographer as blackboard events, so they exist on the
    blackboard before the Cartographer links edges to them."""
    hazard = make_hazard(geom, **overrides)
    service.upsert_hazard(hazard)
    return hazard


def _resolved(h: Hazard) -> Hazard:
    return h.model_copy(update={"status": "resolved"})


@pytest.mark.integration
def test_fire_severs_edges_and_resolve_restores_them(
    service: BlackboardService, policy: PenaltyPolicy
) -> None:
    carto = Cartographer(service, policy)
    target = _longest_edge(service)
    hazard = _post(service, _edge_midpoint_square(target, 0.0008), velocity_m_s=0.0)
    v0 = service.graph_version()

    res = carto.apply(hazard, scenario_t=10.0, elapsed_s=0.0)
    assert res.graph_event is not None and res.graph_event.graph_version == v0 + 1
    assert target.id in res.severed
    assert res.graph_event.severed_count == len(res.severed) >= 1
    assert res.compute_ms < 1000
    stored = {e.id: e for e in service.list_edges(ids=res.severed)}
    assert all(e.status == "severed" and math.isfinite(e.current_cost) for e in stored.values())
    links = service.links_for_hazard(hazard.id)
    assert {lk.edge_id for lk in links} >= set(res.severed)
    assert all(math.isinf(lk.penalty_applied) for lk in links if lk.edge_id in res.severed)
    ev = service.list_agent_events(agent="cartographer")[0]
    assert ev.kind == "hazard.applied" and "severed" in ev.reasoning and target.id in ev.edge_ids

    # resolve -> links dropped, edges reopened, version bumped again
    res2 = carto.release(_resolved(hazard), scenario_t=20.0, reason="hazard.resolved")
    assert res2.graph_event is not None and res2.graph_event.graph_version == v0 + 2
    assert set(res2.restored) == set(res.severed) | set(res.penalized)
    assert service.links_for_hazard(hazard.id) == []
    reopened = service.list_edges(ids=res.severed)
    assert all(e.status == "open" and e.current_cost == e.base_cost for e in reopened)


@pytest.mark.integration
def test_smoke_penalizes_not_severs(service: BlackboardService, policy: PenaltyPolicy) -> None:
    carto = Cartographer(service, policy)
    target = _longest_edge(service)
    hazard = _post(
        service,
        _edge_midpoint_square(target, 0.0008),
        hazard_class="smoke",
        confidence=0.9,
        severity=0.6,
        velocity_m_s=0.0,
    )
    res = carto.apply(hazard, scenario_t=0.0, elapsed_s=0.0)
    assert res.severed == [] and target.id in res.penalized
    e = service.list_edges(ids=[target.id])[0]
    assert e.status == "penalized"
    max_mult = policy.for_class(HazardClass.SMOKE).max_multiplier
    assert e.base_cost < e.current_cost <= e.base_cost * max_mult


@pytest.mark.integration
def test_low_confidence_hazard_is_ignored_with_an_explanation(
    service: BlackboardService, policy: PenaltyPolicy
) -> None:
    carto = Cartographer(service, policy)
    target = _longest_edge(service)
    hazard = _post(service, _edge_midpoint_square(target, 0.0008), confidence=0.2, velocity_m_s=0.0)
    v0 = service.graph_version()
    res = carto.apply(hazard, scenario_t=0.0, elapsed_s=0.0)
    assert res.graph_event is None and res.ignored >= 1
    assert service.graph_version() == v0
    ev = service.list_agent_events(agent="cartographer")[0]
    assert ev.kind.endswith(".noop") and "ignored" in ev.reasoning


@pytest.mark.integration
def test_overlapping_hazards_combine_and_release_independently(
    service: BlackboardService, policy: PenaltyPolicy
) -> None:
    carto = Cartographer(service, policy)
    target = _longest_edge(service)
    geom = _edge_midpoint_square(target, 0.0008)
    smoke = _post(service, geom, hazard_class="smoke", confidence=0.9, velocity_m_s=0.0)
    fire = _post(service, geom, hazard_class="fire_front", confidence=0.9, velocity_m_s=0.0)
    carto.apply(smoke, scenario_t=0.0, elapsed_s=0.0)
    carto.apply(fire, scenario_t=1.0, elapsed_s=0.0)
    assert service.list_edges(ids=[target.id])[0].status == "severed"
    # fire goes away: edge falls back to the smoke penalty, not to open
    carto.release(_resolved(fire), scenario_t=2.0, reason="hazard.resolved")
    e = service.list_edges(ids=[target.id])[0]
    assert e.status == "penalized" and e.current_cost > e.base_cost
    carto.release(_resolved(smoke), scenario_t=3.0, reason="hazard.resolved")
    assert service.list_edges(ids=[target.id])[0].status == "open"


@pytest.mark.integration
def test_ttl_expiry_restores_edges(service: BlackboardService, policy: PenaltyPolicy) -> None:
    carto = Cartographer(service, policy)
    target = _longest_edge(service)
    hazard = _post(service, _edge_midpoint_square(target, 0.0008), ttl_s=60.0, velocity_m_s=0.0)
    carto.apply(hazard, scenario_t=0.0, elapsed_s=0.0)
    assert service.list_edges(ids=[target.id])[0].status == "severed"
    assert carto.expire(now=datetime.now(UTC), scenario_t=30.0) == []  # not yet
    results = carto.expire(now=datetime.now(UTC) + timedelta(seconds=120), scenario_t=130.0)
    assert len(results) == 1 and results[0].kind == "hazard.expired"
    assert service.list_edges(ids=[target.id])[0].status == "open"
    stored = service.get_hazard(hazard.id)
    assert stored is not None and stored.status == "resolved"


@pytest.mark.integration
def test_expanding_buffer_reaches_more_edges_over_time(
    service: BlackboardService, policy: PenaltyPolicy
) -> None:
    carto = Cartographer(service, policy)
    target = _longest_edge(service)
    hazard = _post(service, _edge_midpoint_square(target, 0.0003), velocity_m_s=1.0)
    r0 = carto.apply(hazard, scenario_t=0.0, elapsed_s=0.0)
    n0 = len(service.links_for_hazard(hazard.id))
    assert r0.buffer_m > 0
    r1 = carto.apply(hazard, scenario_t=200.0, elapsed_s=200.0)
    n1 = len(service.links_for_hazard(hazard.id))
    assert r1.buffer_m > r0.buffer_m
    assert n1 >= n0 and n1 > 1
    # regrow() is the periodic form of the same thing
    regrown = carto.regrow(scenario_t=400.0, now=datetime.now(UTC) + timedelta(seconds=400))
    assert all(r.hazard_id == hazard.id for r in regrown)


@pytest.mark.integration
def test_capacity_hazard_reduces_capacity_factor(
    service: BlackboardService, policy: PenaltyPolicy
) -> None:
    carto = Cartographer(service, policy)
    target = _longest_edge(service)
    hazard = _post(
        service,
        _edge_midpoint_square(target, 0.0008),
        hazard_class="crowd_bottleneck",
        confidence=1.0,
        stream="micro",
    )
    res = carto.apply(hazard, scenario_t=0.0, elapsed_s=0.0)
    assert target.id in res.capacity_reduced
    e = service.list_edges(ids=[target.id])[0]
    assert e.capacity_factor < 1.0 and e.status == "penalized"
    carto.release(_resolved(hazard), scenario_t=1.0, reason="hazard.resolved")
    e = service.list_edges(ids=[target.id])[0]
    assert e.capacity_factor == 1.0 and e.status == "open"
