"""In-memory NetworkX mirror of `road_edges` for the Navigator.

- Built once from the blackboard, then patched incrementally from `edge.updated`
  (`apply_edges`) or rebuilt fully when `graph.updated(full_rebuild=True)` arrives.
- Severed edges are *removed* from the routing graph (status, not cost, excludes them).
- Parallel edges between the same nodes keep only the cheapest (A* needs a DiGraph);
  the `edge_id` attribute identifies the blackboard row for every graph edge.
- A* heuristic: great-circle distance / `heuristic_speed_m_s` (seconds), admissible as
  long as no road is faster than that speed.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import networkx as nx

from arco_agents.geo import haversine_m
from arco_schemas import RoadEdge

Weight = Callable[[int, int, dict[str, float]], float]


@dataclass
class PathResult:
    nodes: list[int]
    edge_ids: list[int]
    cost: float
    length_m: float


@dataclass
class RoutingGraph:
    heuristic_speed_m_s: float
    g: nx.DiGraph = field(default_factory=nx.DiGraph)
    edges: dict[int, RoadEdge] = field(default_factory=dict)
    version: int = 0

    # ----------------------------------------------------------------- building

    @classmethod
    def from_edges(
        cls, edges: Iterable[RoadEdge], *, heuristic_speed_m_s: float, version: int = 0
    ) -> RoutingGraph:
        rg = cls(heuristic_speed_m_s=heuristic_speed_m_s, version=version)
        rg.rebuild(edges, version=version)
        return rg

    def rebuild(self, edges: Iterable[RoadEdge], *, version: int) -> None:
        self.g = nx.DiGraph()
        self.edges = {}
        for e in edges:
            self.edges[e.id] = e
            self._add_node_coords(e)
            if e.status != "severed":
                self._place(e)
        self.version = version

    def apply_edges(self, changed: Iterable[RoadEdge], *, version: int) -> list[int]:
        """Patch the mirror with edges whose state changed; returns affected edge ids."""
        touched: list[int] = []
        for e in changed:
            self.edges[e.id] = e
            self._add_node_coords(e)
            self._remove_edge_id(e.id, e.u, e.v)
            if e.status != "severed":
                self._place(e)
            else:
                # another parallel edge may still connect u->v; re-place the cheapest
                self._reconnect_parallel(e.u, e.v)
            touched.append(e.id)
        self.version = version
        return touched

    def _add_node_coords(self, e: RoadEdge) -> None:
        first, last = e.geom.coordinates[0], e.geom.coordinates[-1]
        if e.u not in self.g:
            self.g.add_node(e.u, x=first[0], y=first[1])
        if e.v not in self.g:
            self.g.add_node(e.v, x=last[0], y=last[1])

    def _place(self, e: RoadEdge) -> None:
        """Insert u->v if it is the cheapest parallel edge."""
        cur = self.g.get_edge_data(e.u, e.v)
        if cur is None or e.current_cost < cur["cost"]:
            self.g.add_edge(
                e.u,
                e.v,
                edge_id=e.id,
                cost=e.current_cost,
                length_m=e.length_m,
                capacity=e.capacity_estimate * e.capacity_factor,
            )

    def _remove_edge_id(self, edge_id: int, u: int, v: int) -> None:
        cur = self.g.get_edge_data(u, v)
        if cur is not None and cur["edge_id"] == edge_id:
            self.g.remove_edge(u, v)

    def _reconnect_parallel(self, u: int, v: int) -> None:
        alternatives = [
            e for e in self.edges.values() if e.u == u and e.v == v and e.status != "severed"
        ]
        if alternatives and self.g.get_edge_data(u, v) is None:
            self._place(min(alternatives, key=lambda e: e.current_cost))

    # ------------------------------------------------------------------ queries

    @property
    def node_count(self) -> int:
        return int(self.g.number_of_nodes())

    @property
    def edge_count(self) -> int:
        return int(self.g.number_of_edges())

    def node_xy(self, node: int) -> tuple[float, float]:
        d = self.g.nodes[node]
        return float(d["x"]), float(d["y"])

    def nearest_node(self, lon: float, lat: float) -> int:
        """Nearest node by great-circle distance (linear scan; demo graphs are small)."""
        best, best_d = -1, math.inf
        for n, d in self.g.nodes(data=True):
            dist = haversine_m(lon, lat, d["x"], d["y"])
            if dist < best_d:
                best, best_d = n, dist
        if best < 0:
            msg = "routing graph is empty"
            raise ValueError(msg)
        return best

    def heuristic(self, target: int) -> Callable[[int, int], float]:
        tx, ty = self.node_xy(target)

        def h(n: int, _t: int) -> float:
            x, y = self.node_xy(n)
            return haversine_m(x, y, tx, ty) / self.heuristic_speed_m_s

        return h

    def shortest_path(
        self, source: int, target: int, *, weight: Weight | None = None
    ) -> PathResult | None:
        """A* on `cost` (or a custom weight). None when unreachable."""
        w: Weight | str = weight if weight is not None else "cost"
        try:
            nodes = nx.astar_path(
                self.g, source, target, heuristic=self.heuristic(target), weight=w
            )
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None
        return self._result(nodes, weight)

    def k_diverse_paths(
        self, source: int, target: int, k: int, *, penalty: float, weight: Weight | None = None
    ) -> list[PathResult]:
        """Penalized successive shortest paths: after each path, edges on it cost
        `penalty` times more for the next search, so the next path prefers different
        roads. Cheaper than Yen's algorithm and good enough for dispersal candidates.
        Duplicate paths are dropped."""
        base: Weight = weight if weight is not None else (lambda _u, _v, d: d["cost"])
        multipliers: dict[int, float] = {}

        def penalized(u: int, v: int, d: dict[str, float]) -> float:
            return base(u, v, d) * multipliers.get(int(d["edge_id"]), 1.0)

        out: list[PathResult] = []
        seen: set[tuple[int, ...]] = set()
        for _ in range(k):
            p = self.shortest_path(source, target, weight=penalized)
            if p is None:
                break
            key = tuple(p.edge_ids)
            if key not in seen:
                seen.add(key)
                out.append(self._result(p.nodes, base))  # true (unpenalized) cost
            for eid in p.edge_ids:
                multipliers[eid] = multipliers.get(eid, 1.0) * penalty
        return out

    def _result(self, nodes: list[int], weight: Weight | None) -> PathResult:
        edge_ids: list[int] = []
        cost = 0.0
        length = 0.0
        for u, v in itertools.pairwise(nodes):
            d = self.g[u][v]
            edge_ids.append(int(d["edge_id"]))
            cost += weight(u, v, d) if weight is not None else float(d["cost"])
            length += float(d["length_m"])
        return PathResult(nodes=nodes, edge_ids=edge_ids, cost=cost, length_m=length)
