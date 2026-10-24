"""Spatial-temporal DBSCAN for community reports.

Two reports are neighbours when they are close in BOTH space and time:

    d(a, b) = max( haversine_m(a, b) / eps_m ,  |t_a - t_b| / eps_s )  <= 1

A *core* report has at least `min_pts` neighbours (itself included); clusters grow from
core reports through their neighbourhoods; non-core reports adjacent to a cluster join it
as border points; the rest is noise. Plain O(n²) — the agent only ever clusters the
pending reports of one hazard class inside one scenario, which is tens, not millions.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from arco_agents.geo import haversine_m
from arco_schemas import CommunityReport


@dataclass(frozen=True)
class Cluster:
    reports: tuple[CommunityReport, ...]

    @property
    def centroid(self) -> tuple[float, float]:
        n = len(self.reports)
        lon = sum(r.geom.coordinates[0] for r in self.reports) / n
        lat = sum(r.geom.coordinates[1] for r in self.reports) / n
        return lon, lat

    @property
    def reporter_hashes(self) -> set[str]:
        return {r.reporter_hash for r in self.reports}


def st_distance(a: CommunityReport, b: CommunityReport, *, eps_m: float, eps_s: float) -> float:
    d_m = haversine_m(
        a.geom.coordinates[0], a.geom.coordinates[1], b.geom.coordinates[0], b.geom.coordinates[1]
    )
    dt = abs((a.created_at - b.created_at).total_seconds())
    return max(d_m / eps_m, dt / eps_s)


def dbscan(
    reports: Sequence[CommunityReport], *, eps_m: float, eps_s: float, min_pts: int
) -> list[Cluster]:
    n = len(reports)
    if n == 0:
        return []
    neigh: list[list[int]] = [[] for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if st_distance(reports[i], reports[j], eps_m=eps_m, eps_s=eps_s) <= 1.0:
                neigh[i].append(j)
                neigh[j].append(i)
    label = [-1] * n  # -1 = unvisited/noise
    cluster_id = 0
    for i in range(n):
        if label[i] != -1 or len(neigh[i]) + 1 < min_pts:
            continue
        label[i] = cluster_id
        frontier = list(neigh[i])
        while frontier:
            j = frontier.pop()
            if label[j] == -1:
                label[j] = cluster_id
                if len(neigh[j]) + 1 >= min_pts:  # core: expand through it
                    frontier.extend(k for k in neigh[j] if label[k] == -1)
        cluster_id += 1
    out: list[Cluster] = []
    for c in range(cluster_id):
        members = tuple(
            sorted((reports[i] for i in range(n) if label[i] == c), key=lambda r: r.created_at)
        )
        out.append(Cluster(members))
    return out
