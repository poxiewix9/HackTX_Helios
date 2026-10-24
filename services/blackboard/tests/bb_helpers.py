"""Test builders shared by the blackboard test modules."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from arco_schemas import Hazard, Polygon


def square(lon: float, lat: float, half: float) -> Polygon:
    return Polygon(
        coordinates=[
            [
                [lon - half, lat - half],
                [lon - half, lat + half],
                [lon + half, lat + half],
                [lon + half, lat - half],
                [lon - half, lat - half],
            ]
        ]
    )


def make_hazard(geom: Polygon, **overrides: Any) -> Hazard:
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "id": uuid4(),
        "geom": geom,
        "hazard_class": "fire_front",
        "confidence": 0.9,
        "source": "simulated",
        "stream": "macro",
        "camera_id": "tile_0",
        "first_seen": now,
        "last_seen": now,
        "ttl_s": 600.0,
        "status": "active",
        "severity": 1.0,
        "velocity_m_s": 0.5,
    }
    base.update(overrides)
    return Hazard(**base)
