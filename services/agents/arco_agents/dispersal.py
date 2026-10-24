"""Crowd dispersal: multi-commodity assignment over k-diverse candidate paths.

Math (see config/routing.yaml):

  commodity c = one population cell, demand d_c = population_c / window_min  [people/min]
  candidates P_c = k diverse paths from c to each of its nearest safe zones
  flow x_{c,p} >= 0, sum_p x_{c,p} = d_c
  edge load L_e = sum of x over paths that use e
  edge cost  t_e(L_e) = cost_e * (1 + alpha * (L_e / cap_e) ** beta)          (BPR)
  zone cost  z_s(N_s) = ref_s * alpha * (N_s / capacity_s) ** beta
             with N_s = people arriving at s, ref_s = cheapest free-flow path cost to s
  path cost  C_{c,p} = sum_e t_e + z_{zone(p)}

Frank-Wolfe on the fixed candidate set: at step k, assign each commodity all-or-nothing
to its cheapest path under current loads (y), then x <- (1 - g) x + g y with
g = 2 / (k + 2). Loads converge toward an equilibrium where no candidate path is much
cheaper than the ones in use, which spreads crowds across corridors instead of
funnelling everyone onto the single shortest road.

Everything here is pure (no I/O) so it is unit-testable on synthetic graphs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from arco_agents.graph import PathResult, RoutingGraph
from arco_agents.routing_config import RoutingConfig


@dataclass(frozen=True)
class Commodity:
    cell_id: str
    population: int
    origin_node: int


@dataclass(frozen=True)
class Destination:
    zone_id: str
    capacity: int
    node: int


@dataclass
class Candidate:
    commodity: Commodity
    destination: Destination
    path: PathResult


@dataclass
class Assignment:
    """Result of `assign`. `flows[i]` is people/min on `candidates[i]`."""

    candidates: list[Candidate]
    flows: list[float]
    edge_loads: dict[int, float]
    zone_people: dict[str, float]
    max_edge_load_ratio: float
    unreachable_cells: list[str] = field(default_factory=list)
    iterations: int = 0

    def path_cost(self, i: int, graph: RoutingGraph, cfg: RoutingConfig) -> float:
        """Congested cost of candidate i at the final loads (edges only)."""
        total = 0.0
        for eid in self.candidates[i].path.edge_ids:
            e = graph.edges[eid]
            total += cfg.congested_cost(
                e.current_cost,
                self.edge_loads.get(eid, 0.0),
                e.capacity_estimate * e.capacity_factor,
            )
        return total


def build_candidates(
    graph: RoutingGraph,
    commodities: list[Commodity],
    destinations: list[Destination],
    cfg: RoutingConfig,
) -> tuple[list[Candidate], list[str]]:
    """For each cell: nearest `candidate_zones` reachable zones by free-flow cost, then
    `k_paths` diverse paths to each."""
    out: list[Candidate] = []
    unreachable: list[str] = []
    for c in commodities:
        scored: list[tuple[float, Destination, PathResult]] = []
        for d in destinations:
            p = graph.shortest_path(c.origin_node, d.node)
            if p is not None:
                scored.append((p.cost, d, p))
        if not scored:
            unreachable.append(c.cell_id)
            continue
        scored.sort(key=lambda t: t[0])
        for _cost, d, _p in scored[: cfg.candidate_zones]:
            for path in graph.k_diverse_paths(
                c.origin_node, d.node, cfg.k_paths, penalty=cfg.diversity_penalty
            ):
                out.append(Candidate(commodity=c, destination=d, path=path))
    return out, unreachable


def assign(
    graph: RoutingGraph,
    candidates: list[Candidate],
    cfg: RoutingConfig,
    *,
    unreachable: list[str] | None = None,
) -> Assignment:
    if not candidates:
        return Assignment([], [], {}, {}, 0.0, unreachable or [], 0)

    by_cell: dict[str, list[int]] = {}
    for i, cand in enumerate(candidates):
        by_cell.setdefault(cand.commodity.cell_id, []).append(i)
    demand = {
        cid: candidates[idx[0]].commodity.population / cfg.evacuation_window_min
        for cid, idx in by_cell.items()
    }
    # free-flow reference cost per zone (cheapest candidate into it)
    ref: dict[str, float] = {}
    for cand in candidates:
        z = cand.destination.zone_id
        ref[z] = min(ref.get(z, math.inf), cand.path.cost)

    def loads_for(x: list[float]) -> tuple[dict[int, float], dict[str, float]]:
        edge: dict[int, float] = {}
        zone: dict[str, float] = {}
        for i, flow in enumerate(x):
            if flow <= 0:
                continue
            for eid in candidates[i].path.edge_ids:
                edge[eid] = edge.get(eid, 0.0) + flow
            z = candidates[i].destination.zone_id
            zone[z] = zone.get(z, 0.0) + flow * cfg.evacuation_window_min  # people
        return edge, zone

    def cost_of(i: int, edge: dict[int, float], zone: dict[str, float]) -> float:
        cand = candidates[i]
        total = 0.0
        for eid in cand.path.edge_ids:
            e = graph.edges[eid]
            total += cfg.congested_cost(
                e.current_cost, edge.get(eid, 0.0), e.capacity_estimate * e.capacity_factor
            )
        z = cand.destination
        cap = max(1.0, float(z.capacity))
        total += ref[z.zone_id] * cfg.alpha * (zone.get(z.zone_id, 0.0) / cap) ** cfg.beta
        return float(total)

    def all_or_nothing(edge: dict[int, float], zone: dict[str, float]) -> list[float]:
        y = [0.0] * len(candidates)
        for cid, idx in by_cell.items():
            best = min(idx, key=lambda i: cost_of(i, edge, zone))
            y[best] = demand[cid]
        return y

    x = all_or_nothing({}, {})
    k = 0
    for k in range(1, cfg.iterations + 1):
        edge, zone = loads_for(x)
        y = all_or_nothing(edge, zone)
        gamma = 2.0 / (k + 2.0)
        x = [(1 - gamma) * xi + gamma * yi for xi, yi in zip(x, y, strict=True)]

    edge, zone = loads_for(x)
    ratio = 0.0
    for eid, load in edge.items():
        e = graph.edges[eid]
        cap = e.capacity_estimate * e.capacity_factor
        ratio = max(ratio, load / cap if cap > 0 else math.inf)
    return Assignment(
        candidates=candidates,
        flows=x,
        edge_loads=edge,
        zone_people=zone,
        max_edge_load_ratio=ratio,
        unreachable_cells=unreachable or [],
        iterations=k,
    )


@dataclass(frozen=True)
class GroupSplit:
    candidate_index: int
    group_size: int
    fraction: float


def split_groups(a: Assignment, cfg: RoutingConfig) -> dict[str, list[GroupSplit]]:
    """Turn continuous flows into integer crowd groups per cell. Splits below
    `min_group_fraction` are dropped and the rest renormalised; sizes sum to population."""
    by_cell: dict[str, list[int]] = {}
    for i, cand in enumerate(a.candidates):
        by_cell.setdefault(cand.commodity.cell_id, []).append(i)
    out: dict[str, list[GroupSplit]] = {}
    for cid, idx in by_cell.items():
        population = a.candidates[idx[0]].commodity.population
        total = sum(a.flows[i] for i in idx)
        if total <= 0 or population <= 0:
            continue
        kept = [
            (i, a.flows[i] / total) for i in idx if a.flows[i] / total >= cfg.min_group_fraction
        ]
        if not kept:
            kept = [max(((i, a.flows[i] / total) for i in idx), key=lambda t: t[1])]
        norm = sum(f for _, f in kept)
        sizes = [int(population * f / norm) for _, f in kept]
        sizes[0] += population - sum(sizes)  # remainder to the largest split (sorted below)
        splits = [
            GroupSplit(candidate_index=i, group_size=s, fraction=f / norm)
            for (i, f), s in zip(kept, sizes, strict=True)
        ]
        splits.sort(key=lambda s: -s.fraction)
        out[cid] = splits
    return out
