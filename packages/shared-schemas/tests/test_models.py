"""Behavioural tests for the contract models: validation rules and wire round-trips."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from arco_schemas import (
    AnyMessage,
    BusEnvelope,
    Hazard,
    HazardEvent,
    RoutesUpdatedMessage,
    Scenario,
    TelemetrySample,
)

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
SQUARE = {
    "type": "Polygon",
    "coordinates": [[[0.0, 0.0], [0.0, 0.001], [0.001, 0.001], [0.001, 0.0], [0.0, 0.0]]],
}


def test_hazard_round_trip(hazard_dict: dict[str, Any]) -> None:
    h = Hazard.model_validate(hazard_dict)
    again = Hazard.model_validate_json(h.model_dump_json())
    assert again == h
    assert again.hazard_class == "fire_front"


def test_hazard_rejects_bad_confidence(hazard_dict: dict[str, Any]) -> None:
    hazard_dict["confidence"] = 1.5
    with pytest.raises(ValidationError):
        Hazard.model_validate(hazard_dict)


def test_hazard_rejects_unknown_field(hazard_dict: dict[str, Any]) -> None:
    hazard_dict["raw_frame_b64"] = "..."  # raw video must never ride along a hazard
    with pytest.raises(ValidationError):
        Hazard.model_validate(hazard_dict)


def test_polygon_requires_closed_ring_length(hazard_dict: dict[str, Any]) -> None:
    hazard_dict["geom"] = {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1]]]}
    with pytest.raises(ValidationError):
        Hazard.model_validate(hazard_dict)


def test_scenario_valid(scenario_dict: dict[str, Any]) -> None:
    sc = Scenario.model_validate(scenario_dict)
    assert sc.label == "Simulated on real event geometry"
    assert sc.timeline[0].hazard_class == "fire_front"


def test_scenario_rejects_unsorted_timeline(scenario_dict: dict[str, Any]) -> None:
    scenario_dict["timeline"].reverse()
    with pytest.raises(ValidationError, match="sorted"):
        Scenario.model_validate(scenario_dict)


def test_scenario_rejects_event_after_duration(scenario_dict: dict[str, Any]) -> None:
    scenario_dict["timeline"][-1]["t"] = 10_000
    with pytest.raises(ValidationError, match="exceeds duration"):
        Scenario.model_validate(scenario_dict)


def test_scenario_hazard_appear_requires_polygon(scenario_dict: dict[str, Any]) -> None:
    del scenario_dict["timeline"][0]["hazard_polygon_geojson"]
    with pytest.raises(ValidationError, match="hazard_polygon_geojson"):
        Scenario.model_validate(scenario_dict)


def test_scenario_rejects_wrong_label(scenario_dict: dict[str, Any]) -> None:
    scenario_dict["label"] = "Live inference replay"
    with pytest.raises(ValidationError):
        Scenario.model_validate(scenario_dict)


def test_any_message_discriminates_on_type(hazard_dict: dict[str, Any]) -> None:
    raw = {
        "type": "hazard.created",
        "scenario_t": 12.5,
        "wall_t": NOW.isoformat(),
        "seq": 3,
        "hazard": hazard_dict,
    }
    msg = TypeAdapter(AnyMessage).validate_python(raw)
    assert isinstance(msg, HazardEvent)
    assert msg.hazard.severity == 1.0


def test_any_message_rejects_unknown_type() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(AnyMessage).validate_python(
            {"type": "frame.raw", "scenario_t": 0, "wall_t": NOW.isoformat(), "seq": 0}
        )


def test_bus_envelope_json_round_trip() -> None:
    routes_msg = RoutesUpdatedMessage(
        scenario_t=1.0,
        wall_t=NOW,
        seq=1,
        graph_version=4,
        routes=[],
        trigger="graph.updated",
        compute_ms=12.3,
        max_edge_load_ratio=0.6,
    )
    env = BusEnvelope(topic="routes", published_at=NOW, publisher="agents", message=routes_msg)
    decoded = BusEnvelope.model_validate_json(env.model_dump_json())
    assert isinstance(decoded.message, RoutesUpdatedMessage)
    assert decoded.message.graph_version == 4
    # `type` discriminator is always serialised, even though it has a default
    assert json.loads(env.model_dump_json())["message"]["type"] == "routes.updated"


def test_telemetry_sample_mode_is_required() -> None:
    with pytest.raises(ValidationError, match="mode"):
        TelemetrySample.model_validate(
            {
                "pipeline": "int8_split",
                "stream": "macro",
                "frame_id": 1,
                "wall_t": NOW.isoformat(),
                "stage_ms": {"head": 3.0},
                "total_ms": 3.0,
                "fps": 30.0,
                "bytes_sent": 1024,
                "dropped_frames": 0,
            }
        )


def test_telemetry_rejects_unknown_stage() -> None:
    with pytest.raises(ValidationError):
        TelemetrySample.model_validate(
            {
                "mode": "live",
                "pipeline": "int8_split",
                "stream": "macro",
                "frame_id": 1,
                "wall_t": NOW.isoformat(),
                "stage_ms": {"upload_to_cloud": 3.0},
                "total_ms": 3.0,
                "fps": 30.0,
                "bytes_sent": 1024,
                "dropped_frames": 0,
            }
        )


def test_geometry_default_type_is_serialised() -> None:
    h = Hazard(
        id=uuid4(),
        geom={"type": "Polygon", "coordinates": SQUARE["coordinates"]},  # type: ignore[arg-type]
        hazard_class="smoke",  # type: ignore[arg-type]
        confidence=0.5,
        source="simulated",
        first_seen=NOW,
        last_seen=NOW,
        ttl_s=60,
        severity=0.5,
    )
    assert json.loads(h.model_dump_json())["geom"]["type"] == "Polygon"
