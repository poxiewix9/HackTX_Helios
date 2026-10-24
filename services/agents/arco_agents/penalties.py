"""Penalty policy: `config/penalties.yaml` -> typed, per-class decisions.

The math is documented in the YAML header; this module implements exactly that and
nothing else, so the policy can be audited by reading one file.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from arco_schemas import EdgeHazardLink, Hazard, HazardClass
from arco_schemas.hazard_classes import HAZARD_CLASSES, EdgeAction

Decision = Literal["ignore", "sever", "penalize", "capacity"]

SEVER_PENALTY = math.inf
"""`EdgeHazardLink.penalty_applied` value recorded for a severed edge."""


@dataclass(frozen=True)
class BufferPolicy:
    base_margin_m: float
    horizon_s: float
    max_buffer_m: float
    default_velocity_m_s: float

    def distance_m(self, velocity_m_s: float | None, elapsed_s: float) -> float:
        """buffer = min(max, margin + v * (horizon + elapsed)); see penalties.yaml."""
        v = self.default_velocity_m_s if velocity_m_s is None else velocity_m_s
        raw = self.base_margin_m + v * (self.horizon_s + max(0.0, elapsed_s))
        return min(self.max_buffer_m, raw)


@dataclass(frozen=True)
class ClassPolicy:
    hazard_class: HazardClass
    action: EdgeAction
    min_confidence: float
    sever_fraction: float
    sever_confidence: float
    max_multiplier: float
    capacity_factor: float
    expands: bool
    buffer: BufferPolicy | None

    def decide(self, hazard: Hazard, overlap_fraction: float) -> tuple[Decision, float, float]:
        """Return (decision, cost_multiplier, capacity_factor) for one overlap.

        multiplier = 1 + (max_multiplier - 1) * f * c * (0.5 + 0.5 * s)
        capacity   = capacity_factor ** (f * c)        (1.0 unless action == capacity)
        """
        f = min(1.0, max(0.0, overlap_fraction))
        c = hazard.confidence
        s = hazard.severity
        if f <= 0.0 or c < self.min_confidence:
            return "ignore", 1.0, 1.0
        if self.action == "sever" and f >= self.sever_fraction and c >= self.sever_confidence:
            return "sever", SEVER_PENALTY, 1.0
        w = f * c
        multiplier = 1.0 + (self.max_multiplier - 1.0) * w * (0.5 + 0.5 * s)
        if self.action == "capacity":
            return "capacity", multiplier, self.capacity_factor**w
        return "penalize", multiplier, 1.0


@dataclass(frozen=True)
class PenaltyPolicy:
    classes: dict[HazardClass, ClassPolicy]
    source: Path

    @classmethod
    def load(cls, path: Path) -> PenaltyPolicy:
        raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        defaults: dict[str, Any] = raw.get("defaults", {})
        default_buffer: dict[str, Any] = defaults.get("buffer", {})
        classes: dict[HazardClass, ClassPolicy] = {}
        for hc, info in HAZARD_CLASSES.items():
            entry: dict[str, Any] = raw.get("classes", {}).get(hc.value, {})
            buf_raw = {**default_buffer, **entry.get("buffer", {})}
            buffer = (
                BufferPolicy(
                    base_margin_m=float(buf_raw["base_margin_m"]),
                    horizon_s=float(buf_raw["horizon_s"]),
                    max_buffer_m=float(buf_raw["max_buffer_m"]),
                    default_velocity_m_s=float(buf_raw["default_velocity_m_s"]),
                )
                if info.expands
                else None
            )
            classes[hc] = ClassPolicy(
                hazard_class=hc,
                action=entry.get("action", info.default_action),
                min_confidence=float(entry.get("min_confidence", defaults["min_confidence"])),
                sever_fraction=float(entry.get("sever_fraction", defaults["sever_fraction"])),
                sever_confidence=float(entry.get("sever_confidence", defaults["sever_confidence"])),
                max_multiplier=float(entry.get("max_multiplier", defaults["max_multiplier"])),
                capacity_factor=float(entry.get("capacity_factor", defaults["capacity_factor"])),
                expands=info.expands,
                buffer=buffer,
            )
        return cls(classes=classes, source=path)

    def for_class(self, hazard_class: HazardClass) -> ClassPolicy:
        return self.classes[hazard_class]


@dataclass(frozen=True)
class EdgeState:
    """Combined outcome for one edge across all hazards touching it."""

    severed: bool
    multiplier: float
    capacity_factor: float


def combine(decisions: list[tuple[Decision, float, float]]) -> EdgeState:
    """Severed if ANY decision severs; else cost multiplier is the MAX over decisions (two
    detections of the same fire must not double-penalize); capacity factor is the MIN."""
    active = [d for d in decisions if d[0] != "ignore"]
    severed = any(d[0] == "sever" for d in active)
    multiplier = max((m for _, m, _ in active if math.isfinite(m)), default=1.0)
    capacity = min((cf for _, _, cf in active), default=1.0)
    return EdgeState(severed=severed, multiplier=max(1.0, multiplier), capacity_factor=capacity)


def link_is_sever(link: EdgeHazardLink) -> bool:
    return math.isinf(link.penalty_applied)
