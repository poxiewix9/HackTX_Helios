"""Navigator agent: road graph + population -> crowd-dispersed routes.

Deterministic core: NetworkX mirror (graph.py), A* with an admissible great-circle
heuristic, k-diverse candidate paths and a Frank-Wolfe assignment (dispersal.py). The
explainer only turns the result into text. Every recompute writes `routes`, publishes
`routes.updated` (with the *measured* compute time) and an `AgentEvent`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from shapely.geometry import shape

from arco_agents.dispersal import (
    Assignment,
    Commodity,
    Destination,
    assign,
    build_candidates,
    split_groups,
)
from arco_agents.explain import CachedExplainer, Explainer, RouteFacts
from arco_agents.geo import concat_lines
from arco_agents.graph import RoutingGraph
from arco_agents.logging import get_logger
from arco_agents.population import Population
from arco_agents.routing_config import RoutingConfig
from arco_blackboard.service import NO_SCENARIO, BlackboardService
from arco_schemas import (
    AgentEvent,
    EdgeEvent,
    GraphUpdatedEvent,
    Point,
    Polygon,
    Route,
    RoutesUpdatedMessage,
)

log = get_logger("agents.navigator")

Trigger = str  # "graph.updated" | "hazard" | "periodic" | "manual"


@dataclass
class NavigatorResult:
    routes: list[Route]
    message: RoutesUpdatedMessage | None
    assignment: Assignment | None
    compute_ms: float
    trigger: Trigger
    graph_version: int
    agent_event: AgentEvent | None = None
    unreachable_cells: list[str] = field(default_factory=list)


class Navigator:
    def __init__(
        self,
        service: BlackboardService,
        config: RoutingConfig,
        explainer: Explainer,
        population: Population | None = None,
    ) -> None:
        self.service = service
        self.config = config
        self.explainer = CachedExplainer(explainer)
        self.population = population
        self.graph: RoutingGraph | None = None
        self._route_versions: dict[str, int] = {}
        self.last: NavigatorResult | None = None

    # ------------------------------------------------------------------ inputs

    def load_graph(self) -> RoutingGraph:
        edges = self.service.list_edges()
        version = self.service.graph_version()
        self.graph = RoutingGraph.from_edges(
            edges, heuristic_speed_m_s=self.config.heuristic_speed_m_s, version=version
        )
        log.info(
            "routing graph loaded",
            nodes=self.graph.node_count,
            edges=self.graph.edge_count,
            graph_version=version,
        )
        return self.graph

    def set_population(self, population: Population, *, scenario_t: float = NO_SCENARIO) -> None:
        """Adopt new origin cells + safe zones; zones are mirrored to the blackboard so
        the dashboard and other agents see the same destinations."""
        self.population = population
        for zone in population.blackboard_zones():
            self.service.upsert_safe_zone(zone, scenario_t=scenario_t)
        self._route_versions.clear()

    def on_edge_event(self, event: EdgeEvent) -> None:
        if self.graph is None:
            self.load_graph()
            return
        self.graph.apply_edges(event.edges, version=event.graph_version)

    def on_graph_updated(self, event: GraphUpdatedEvent) -> NavigatorResult | None:
        """Rebuild (fully or incrementally) and reroute. Returns None without population."""
        if self.graph is None or event.full_rebuild or event.graph_version > self.graph.version + 1:
            self.load_graph()
        elif event.graph_version > self.graph.version and event.changed_edge_ids:
            changed = self.service.list_edges(ids=event.changed_edge_ids)
            self.graph.apply_edges(changed, version=event.graph_version)
        if self.population is None:
            return None
        return self.recompute(trigger="graph.updated", scenario_t=event.scenario_t)

    # ----------------------------------------------------------------- compute

    def recompute(self, *, trigger: Trigger, scenario_t: float = NO_SCENARIO) -> NavigatorResult:
        t0 = time.perf_counter()
        if self.graph is None:
            self.load_graph()
        assert self.graph is not None
        graph = self.graph
        if self.population is None:
            msg = "Navigator has no population cells; call set_population() first"
            raise RuntimeError(msg)

        commodities = [
            Commodity(
                cell_id=c.id,
                population=c.population,
                origin_node=graph.nearest_node(*_centroid(c.geom)),
            )
            for c in self.population.population_cells
        ]
        destinations = [
            Destination(
                zone_id=z.id, capacity=z.capacity, node=graph.nearest_node(*_centroid(z.geom))
            )
            for z in self.population.safe_zones
        ]
        candidates, unreachable = build_candidates(graph, commodities, destinations, self.config)
        assignment = assign(graph, candidates, self.config, unreachable=unreachable)
        routes = self._routes_from(assignment, graph, scenario_t)
        compute_ms = (time.perf_counter() - t0) * 1000

        message = self.service.publish_routes(
            routes,
            trigger=trigger,
            compute_ms=compute_ms,
            max_edge_load_ratio=assignment.max_edge_load_ratio,
            scenario_t=scenario_t,
        )
        self.service.set_edge_loads(assignment.edge_loads)
        result = NavigatorResult(
            routes=routes,
            message=message,
            assignment=assignment,
            compute_ms=compute_ms,
            trigger=trigger,
            graph_version=graph.version,
            unreachable_cells=unreachable,
        )
        result.agent_event = self._log_event(result, scenario_t)
        self.last = result
        return result

    def _routes_from(self, a: Assignment, graph: RoutingGraph, scenario_t: float) -> list[Route]:
        assert self.population is not None
        zone_names = {z.id: z.name for z in self.population.safe_zones}
        now = datetime.now(UTC)
        severed_total = sum(1 for e in graph.edges.values() if e.status == "severed")
        penalized_total = sum(1 for e in graph.edges.values() if e.status == "penalized")
        routes: list[Route] = []
        for cell_id, splits in split_groups(a, self.config).items():
            for idx, split in enumerate(splits):
                cand = a.candidates[split.candidate_index]
                edges = tuple(graph.edges[eid] for eid in cand.path.edge_ids)
                group_id = f"{cell_id}-{idx}"
                version = self._route_versions.get(group_id, 0) + 1
                self._route_versions[group_id] = version
                load_ratio = max(
                    (
                        a.edge_loads.get(e.id, 0.0) / (e.capacity_estimate * e.capacity_factor)
                        for e in edges
                        if e.capacity_estimate * e.capacity_factor > 0
                    ),
                    default=0.0,
                )
                facts = RouteFacts(
                    cell_id=cell_id,
                    zone_id=cand.destination.zone_id,
                    zone_name=zone_names.get(cand.destination.zone_id, cand.destination.zone_id),
                    group_size=split.group_size,
                    length_m=cand.path.length_m,
                    cost_s=a.path_cost(split.candidate_index, graph, self.config),
                    edges=edges,
                    avoided_severed=severed_total,
                    avoided_penalized=penalized_total,
                    load_ratio=load_ratio,
                    split_fraction=split.fraction,
                )
                text = self.explainer.explain(facts)
                routes.append(
                    Route(
                        id=uuid4(),
                        origin_cell_id=cell_id,
                        safe_zone_id=cand.destination.zone_id,
                        crowd_group_id=group_id,
                        group_size=split.group_size,
                        path=concat_lines([e.geom for e in edges]),
                        edge_ids=list(cand.path.edge_ids),
                        cost=facts.cost_s,
                        length_m=cand.path.length_m,
                        computed_at=now,
                        graph_version=graph.version,
                        version=version,
                        instruction=text.instruction,
                        rationale=text.rationale,
                        explanation_source=text.source,
                    )
                )
        return routes

    def _log_event(self, result: NavigatorResult, scenario_t: float) -> AgentEvent:
        a = result.assignment
        ratio = a.max_edge_load_ratio if a else 0.0
        over = ratio > self.config.max_load_ratio
        n_cells = len({r.origin_cell_id for r in result.routes})
        n_zones = len({r.safe_zone_id for r in result.routes})
        reasoning = (
            f"{result.trigger}: {len(result.routes)} routes for {n_cells} cells to {n_zones} "
            f"zones on graph v{result.graph_version} in {result.compute_ms:.0f} ms; "
            f"Frank-Wolfe {a.iterations if a else 0} iterations over "
            f"{len(a.candidates) if a else 0} candidate paths; "
            f"peak edge load {ratio:.0%} of capacity"
            + (" (ABOVE stampede guard)" if over else "")
            + (
                f"; unreachable cells: {', '.join(result.unreachable_cells)}"
                if result.unreachable_cells
                else ""
            )
        )
        ev = AgentEvent(
            id=uuid4(),
            agent="navigator",
            kind="routes.recomputed" if not over else "routes.recomputed.overload",
            reasoning=reasoning,
            scenario_t=scenario_t,
            wall_t=datetime.now(UTC),
            route_ids=[r.id for r in result.routes],
            edge_ids=sorted({eid for r in result.routes for eid in r.edge_ids})[:200],
            data={
                "routes": len(result.routes),
                "compute_ms": round(result.compute_ms, 2),
                "max_edge_load_ratio": round(ratio, 3),
                "graph_version": result.graph_version,
                "unreachable_cells": len(result.unreachable_cells),
                "explainer_cache_hits": self.explainer.hits,
                **self.explainer.flat_stats(),
            },
        )
        self.service.append_agent_event(ev)
        log.info("navigator", **{k: v for k, v in ev.data.items()}, trigger=result.trigger)
        return ev


def _centroid(geom: Point | Polygon) -> tuple[float, float]:
    c = shape(geom.model_dump(mode="json")).centroid
    return float(c.x), float(c.y)
