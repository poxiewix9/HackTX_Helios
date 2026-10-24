from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

PKG_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = PKG_ROOT / "schema"
GENERATED_TS = PKG_ROOT / "ts" / "src" / "generated" / "arco.ts"

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)

SQUARE = {
    "type": "Polygon",
    "coordinates": [[[0.0, 0.0], [0.0, 0.001], [0.001, 0.001], [0.001, 0.0], [0.0, 0.0]]],
}


@pytest.fixture
def hazard_dict() -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "geom": SQUARE,
        "hazard_class": "fire_front",
        "confidence": 0.91,
        "source": "macro_vision",
        "stream": "macro",
        "camera_id": "tile_0",
        "first_seen": NOW.isoformat(),
        "last_seen": NOW.isoformat(),
        "ttl_s": 600,
        "status": "active",
        "severity": 1.0,
        "velocity_m_s": 0.4,
    }


@pytest.fixture
def scenario_dict() -> dict[str, Any]:
    camera_calibration = {
        "kind": "camera",
        "camera_id": "cam-1",
        "lat": 0.0005,
        "lon": 0.0005,
        "heading_deg": 90.0,
        "fov_deg": 70.0,
        "image_width": 1280,
        "image_height": 720,
        "image_points": [[0, 720], [1280, 720], [1280, 400], [0, 400]],
        "world_points": [[0.0, 0.0], [0.001, 0.0], [0.001, 0.001], [0.0, 0.001]],
    }
    return {
        "schema_version": "0.1.0",
        "id": "unit_test_scenario",
        "name": "Unit test scenario",
        "event": "wildfire",
        "event_date": "2023-08-08",
        "location": "Test grid",
        "bbox": [0.0, 0.0, 0.01, 0.01],
        "start_time": NOW.isoformat(),
        "duration_s": 600,
        "road_graph_source": "osmnx_bbox",
        "road_graph_ref": "0,0,0.01,0.01",
        "population_cells": [
            {
                "id": "cell-a",
                "geom": {"type": "Point", "coordinates": [0.001, 0.001]},
                "population": 120,
            }
        ],
        "safe_zones": [{"id": "sz-1", "name": "School", "geom": SQUARE, "capacity": 500}],
        "macro_feed": {"tiles": [], "frames": []},
        "micro_feeds": [
            {
                "camera_id": "cam-1",
                "lat": 0.0005,
                "lon": 0.0005,
                "heading_deg": 90.0,
                "fov_deg": 70.0,
                "video_path": None,
                "calibration": camera_calibration,
                "synthetic_overlays": [
                    {"t": 30, "hazard_class": "debris", "bbox_px": [400, 500, 800, 700]}
                ],
            }
        ],
        "timeline": [
            {
                "t": 10,
                "type": "hazard_appear",
                "hazard_id": "h1",
                "hazard_polygon_geojson": SQUARE,
                "hazard_class": "fire_front",
                "confidence": 0.9,
                "stream": "macro",
            },
            {"t": 300, "type": "hazard_resolve", "hazard_id": "h1"},
        ],
        "community_events": [
            {
                "t": 40,
                "type": "hazard_pin",
                "lat": 0.002,
                "lon": 0.002,
                "reporter": "phone-01",
                "hazard_class": "smoke",
            }
        ],
        "expected_blocked_edges": [],
        "data_sources": [{"name": "synthetic grid", "license": "CC0", "note": "unit test only"}],
        "notes": "Unit-test scenario; polygons are synthetic approximations, not a replay.",
    }
