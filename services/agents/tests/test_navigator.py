"""3.2 Navigator: routing graph, A*, diverse paths, dispersal (unit, synthetic graphs) and
measured reroute on the Lahaina graph (integration)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from arco_agents.cartographer import Cartographer
from arco_agents.dispersal import Commodity, Destination, assign, build_candidates, split_groups
from arco_agents.explain import (
    CachedExplainer,
    DeterministicExplainer,
    RouteFacts,
    SlmExplainer,
)
from arco_agents.graph import RoutingGraph
from arco_agents.navigator import Navigator
from arco_agents.penalties import PenaltyPolicy
from arco_agents.population import Population
from arco_agents.routing_config import RoutingConfig
from arco_blackboard.service import BlackboardService
from arco_blackboard.testing import make_hazard, square
from arco_schemas import RoadEdge

DEMO_POPULATION = Path(__file__).resolve().parents[3] / "data/samples/lahaina/demo_population.json"

# --- synthetic graph helpers -----------------------------------------------------------
#
#  Two corridors from node 1 to node 4 (coordinates in degrees, ~111 m per 0.001):
#     1 -> 2 -> 4   "Main St"   cheap but low capacity
#     1 -> 3 -> 4   "Bypass"    slower, high capacity
#  plus a dead-end node 5 and a reverse edge 4 -> 1.

M = 0.001


def _edge(
    eid: int, u: int, v: int, p0: tuple[float, float], p1: tuple[float, float], **kw: Any
) -> RoadEdge:
    base: dict[str, Any] = {
        "id": eid,
        "geom": {"type": "LineString", "coordinates": [list(p0), list(p1)]},
        "u": u,
        "v": v,
        "length_m": 111.0,
        "base_cost": 10.0,
        "current_cost": 10.0,
        "status": "open",
        "capacity_estimate": 100.0,
        "name": None,
    }
    base.update(kw)
    return RoadEdge.model_validate(base)


def corridor_edges() -> list[RoadEdge]:
    n = {1: (0.0, 0.0), 2: (M, M / 2), 3: (M, -M / 2), 4: (2 * M, 0.0), 5: (0.0, M)}
    return [
        _edge(
            1,
            1,
            2,
            n[1],
            n[2],
            name="Main St",
            base_cost=50.0,
            current_cost=50.0,
            capacity_estimate=40.0,
        ),
        _edge(
            2,
            2,
            4,
            n[2],
            n[4],
            name="Main St",
            base_cost=50.0,
            current_cost=50.0,
            capacity_estimate=40.0,
        ),
        _edge(
            3,
            1,
            3,
            n[1],
            n[3],
            name="Bypass",
            base_cost=70.0,
            current_cost=70.0,
            capacity_estimate=1000.0,
        ),
        _edge(
            4,
            3,
            4,
            n[3],
            n[4],
            name="Bypass",
            base_cost=70.0,
            current_cost=70.0,
            capacity_estimate=1000.0,
        ),
        _edge(5, 1, 5, n[1], n[5], name="Dead End", base_cost=5.0, current_cost=5.0),
        _edge(6, 4, 1, n[4], n[1], name="Return", base_cost=200.0, current_cost=200.0),
        # parallel, more expensive copy of 1->2 (must lose to edge 1 until 1 is severed)
        _edge(7, 1, 2, n[1], n[2], name="Main St service", base_cost=90.0, current_cost=90.0),
    ]


def graph(cfg: RoutingConfig) -> RoutingGraph:
    return RoutingGraph.from_edges(corridor_edges(), heuristic_speed_m_s=cfg.heuristic_speed_m_s)


# --- unit: graph -----------------------------------------------------------------------


def test_graph_build_and_astar(routing_config: RoutingConfig) -> None:
    g = graph(routing_config)
    assert g.node_count == 5 and g.edge_count == 6  # parallel edge collapsed
    assert g.nearest_node(0.0001, 0.0) == 1
    p = g.shortest_path(1, 4)
    assert p is not None and p.edge_ids == [1, 2] and p.cost == 100.0
    assert g.shortest_path(5, 4) is None  # dead end has no outgoing edges


def test_severed_edges_are_excluded_and_restored_incrementally(
    routing_config: RoutingConfig,
) -> None:
    g = graph(routing_config)
    edges = {e.id: e for e in corridor_edges()}
    severed = edges[2].model_copy(update={"status": "severed"})
    g.apply_edges([severed], version=2)
    p = g.shortest_path(1, 4)
    assert p is not None and p.edge_ids == [3, 4], "must take the bypass when Main St is cut"
    assert g.version == 2
    # severing edge 1 makes the parallel edge 7 the connection 1->2
    g.apply_edges([edges[1].model_copy(update={"status": "severed"})], version=3)
    assert g.g[1][2]["edge_id"] == 7
    # reopen edge 2 with a penalty: still worse than the bypass
    g.apply_edges(
        [edges[2].model_copy(update={"status": "penalized", "current_cost": 500.0})], version=4
    )
    p = g.shortest_path(1, 4)
    assert p is not None and p.edge_ids == [3, 4]
    # full restore
    g.apply_edges([edges[1], edges[2]], version=5)
    p = g.shortest_path(1, 4)
    assert p is not None and p.edge_ids == [1, 2]


def test_k_diverse_paths_prefers_different_roads(routing_config: RoutingConfig) -> None:
    g = graph(routing_config)
    paths = g.k_diverse_paths(1, 4, 3, penalty=routing_config.diversity_penalty)
    assert [p.edge_ids for p in paths] == [[1, 2], [3, 4]]
    assert paths[0].cost == 100.0 and paths[1].cost == 140.0  # true costs, not penalized


# --- unit: dispersal ---------------------------------------------------------------------


def test_dispersal_splits_crowd_when_main_street_is_saturated(
    routing_config: RoutingConfig,
) -> None:
    g = graph(routing_config)
    cfg = routing_config
    com = [Commodity(cell_id="A", population=6000, origin_node=1)]
    dst = [Destination(zone_id="Z", capacity=10_000, node=4)]
    cands, unreachable = build_candidates(g, com, dst, cfg)
    assert unreachable == [] and [c.path.edge_ids for c in cands] == [[1, 2], [3, 4]]
    a = assign(g, cands, cfg)
    demand = 6000 / cfg.evacuation_window_min  # 200 people/min
    assert sum(a.flows) == pytest.approx(demand)
    # All-or-nothing on Main St would be 200/40 = 5x capacity; dispersal must use the bypass.
    assert a.flows[1] > a.flows[0] > 0
    assert a.max_edge_load_ratio < 5.0
    assert a.edge_loads[3] == pytest.approx(a.flows[1]) and a.edge_loads[1] == pytest.approx(
        a.flows[0]
    )
    groups = split_groups(a, cfg)["A"]
    assert sum(s.group_size for s in groups) == 6000
    assert len(groups) == 2 and groups[0].fraction > groups[1].fraction


def test_dispersal_keeps_single_path_when_uncongested(routing_config: RoutingConfig) -> None:
    g = graph(routing_config)
    com = [Commodity(cell_id="A", population=300, origin_node=1)]
    dst = [Destination(zone_id="Z", capacity=10_000, node=4)]
    cands, _ = build_candidates(g, com, dst, routing_config)
    a = assign(g, cands, routing_config)
    groups = split_groups(a, routing_config)["A"]
    assert len(groups) == 1 and groups[0].group_size == 300
    assert a.candidates[groups[0].candidate_index].path.edge_ids == [1, 2]


def test_unreachable_cell_is_reported_not_hidden(routing_config: RoutingConfig) -> None:
    g = graph(routing_config)
    com = [Commodity(cell_id="stuck", population=10, origin_node=5)]
    dst = [Destination(zone_id="Z", capacity=10, node=4)]
    cands, unreachable = build_candidates(g, com, dst, routing_config)
    assert cands == [] and unreachable == ["stuck"]
    a = assign(g, cands, routing_config, unreachable=unreachable)
    assert a.unreachable_cells == ["stuck"] and a.flows == []


# --- unit: explanations ---------------------------------------------------------------------


def _facts(**kw: Any) -> RouteFacts:
    edges = tuple(e for e in corridor_edges() if e.id in (1, 2))
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


def test_deterministic_explainer_mentions_roads_zone_and_reasons() -> None:
    ex = DeterministicExplainer().explain(_facts())
    assert "Main St" in ex.instruction and "Civic Center" in ex.instruction
    assert "220 m" in ex.instruction and "~2 min" in ex.instruction
    assert "2 closed road" in ex.rationale and "60%" in ex.rationale and "40%" in ex.rationale
    assert ex.source == "deterministic"


def test_explainer_cache_is_keyed_by_route_hash() -> None:
    cached = CachedExplainer(DeterministicExplainer())
    cached.explain(_facts())
    cached.explain(_facts())
    assert cached.hits == 1
    cached.explain(_facts(split_fraction=0.3))
    assert cached.hits == 1 and len(cached.cache) == 2


def test_slm_explainer_falls_back_when_unreachable() -> None:
    slm = SlmExplainer("http://127.0.0.1:1", "qwen2.5:1.5b-instruct", timeout_s=0.3)
    ex = slm.explain(_facts())
    assert ex.source == "deterministic" and "Main St" in ex.instruction


def test_demo_population_loads_and_is_labelled() -> None:
    pop = Population.load(DEMO_POPULATION)
    assert pop.label == "Simulated on real event geometry"
    assert pop.notes and "APPROXIMATE" in pop.notes
    assert pop.total_population() > 0 and len(pop.safe_zones) == 2


# --- integration: Lahaina graph --------------------------------------------------------------


@pytest.fixture
def navigator(service: BlackboardService, routing_config: RoutingConfig) -> Navigator:
    nav = Navigator(service, routing_config, DeterministicExplainer())
    nav.set_population(Population.load(DEMO_POPULATION))
    return nav


@pytest.mark.integration
def test_routes_are_computed_persisted_and_published(
    navigator: Navigator, service: BlackboardService
) -> None:
    res = navigator.recompute(trigger="manual", scenario_t=0.0)
    cells = {c.id for c in navigator.population.population_cells}  # type: ignore[union-attr]
    assert {r.origin_cell_id for r in res.routes} == cells
    assert res.unreachable_cells == []
    assert all(r.length_m > 0 and r.cost > 0 and len(r.edge_ids) > 0 for r in res.routes)
    assert all(r.explanation_source == "deterministic" and r.instruction for r in res.routes)
    assert all(len(r.path.coordinates) >= 2 for r in res.routes)
    stored = service.list_routes()
    assert {r.id for r in stored} == {r.id for r in res.routes}
    assert res.message is not None and res.message.compute_ms == pytest.approx(res.compute_ms)
    assert res.message.trigger == "manual"
    zones = {z.id for z in service.list_safe_zones()}
    assert zones == {z.id for z in navigator.population.safe_zones}  # type: ignore[union-attr]
    ev = service.list_agent_events(agent="navigator")[0]
    assert ev.kind.startswith("routes.recomputed") and "Frank-Wolfe" in ev.reasoning
    assert set(ev.route_ids) == {r.id for r in res.routes}
    loads = {e.id: e.current_load for e in service.list_edges(ids=res.routes[0].edge_ids)}
    assert all(v > 0 for v in loads.values())


@pytest.mark.integration
def test_reroute_avoids_severed_edges_in_under_one_second(
    navigator: Navigator, service: BlackboardService, policy: PenaltyPolicy
) -> None:
    first = navigator.recompute(trigger="manual", scenario_t=0.0)
    # Cut the road in the middle of the longest first-pass route.
    victim = max(first.routes, key=lambda r: r.length_m)
    mid_edge_id = victim.edge_ids[len(victim.edge_ids) // 2]
    mid_edge = service.list_edges(ids=[mid_edge_id])[0]
    c = mid_edge.geom.coordinates[len(mid_edge.geom.coordinates) // 2]
    hazard = make_hazard(square(c[0], c[1], 0.0006), velocity_m_s=0.0)
    service.upsert_hazard(hazard)
    carto = Cartographer(service, policy)
    cres = carto.apply(hazard, scenario_t=60.0, elapsed_s=0.0)
    assert cres.graph_event is not None and mid_edge_id in cres.severed

    t0 = time.perf_counter()
    second = navigator.on_graph_updated(cres.graph_event)
    wall_ms = (time.perf_counter() - t0) * 1000
    assert second is not None
    severed = set(cres.severed)
    assert all(not severed & set(r.edge_ids) for r in second.routes), "routes still use cut edges"
    assert {r.origin_cell_id for r in second.routes} == {r.origin_cell_id for r in first.routes}
    # measured, not asserted-by-fiat: the whole reroute incl. DB writes must be < 1 s
    print(f"\nREROUTE compute_ms={second.compute_ms:.1f} wall_ms={wall_ms:.1f}")
    assert second.compute_ms < 1000.0, f"compute {second.compute_ms:.0f} ms"
    assert wall_ms < 1000.0, f"wall {wall_ms:.0f} ms"
    assert second.graph_version == cres.graph_event.graph_version
    assert second.message is not None and second.message.trigger == "graph.updated"
    # route versions are monotonic per crowd group
    v1 = {r.crowd_group_id: r.version for r in first.routes}
    v2 = {r.crowd_group_id: r.version for r in second.routes}
    assert all(v2[g] > v1[g] for g in v1 if g in v2)
    # the incremental mirror matches a full reload
    incremental = navigator.graph.edge_count  # type: ignore[union-attr]
    navigator.load_graph()
    assert navigator.graph.edge_count == incremental  # type: ignore[union-attr]
    # current routes only: the first batch is superseded
    assert {r.id for r in service.list_routes()} == {r.id for r in second.routes}
