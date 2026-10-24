"""Typed view of `config/routing.yaml` (math documented there)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class RoutingConfig:
    alpha: float
    beta: float
    evacuation_window_min: float
    k_paths: int
    diversity_penalty: float
    candidate_zones: int
    iterations: int
    min_group_fraction: float
    max_load_ratio: float
    heuristic_speed_m_s: float
    max_zone_capacity_overflow: float
    source: Path

    @classmethod
    def load(cls, path: Path) -> RoutingConfig:
        raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        cong, disp, astar, routes = raw["congestion"], raw["dispersal"], raw["astar"], raw["routes"]
        return cls(
            alpha=float(cong["alpha"]),
            beta=float(cong["beta"]),
            evacuation_window_min=float(disp["evacuation_window_min"]),
            k_paths=int(disp["k_paths"]),
            diversity_penalty=float(disp["diversity_penalty"]),
            candidate_zones=int(disp["candidate_zones"]),
            iterations=int(disp["iterations"]),
            min_group_fraction=float(disp["min_group_fraction"]),
            max_load_ratio=float(disp["max_load_ratio"]),
            heuristic_speed_m_s=float(astar["heuristic_speed_m_s"]),
            max_zone_capacity_overflow=float(routes["max_zone_capacity_overflow"]),
            source=path,
        )

    def congested_cost(self, free_cost: float, load: float, capacity: float) -> float:
        """BPR: cost * (1 + alpha * (load / capacity) ** beta). A zero-capacity edge is
        treated as 1 person/min so the ratio stays finite."""
        cap = capacity if capacity > 0 else 1.0
        return float(free_cost * (1.0 + self.alpha * (load / cap) ** self.beta))
