"""2.2: spatial REST API + `/ws/blackboard` stream, exercised end to end against PostGIS."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from bb_helpers import make_hazard, square
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import Engine

from arco_blackboard.api import create_app
from arco_blackboard.settings import Settings
from arco_schemas import CommunityReport, Point, SafeZone
from arco_schemas.hazard_classes import HazardClass


def dump(model: BaseModel) -> dict[str, object]:
    out: dict[str, object] = json.loads(model.model_dump_json())
    return out


@pytest.fixture
def client(clean_db: Engine, settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings, publisher_name="api-test")
    with TestClient(app) as c:
        yield c


@pytest.mark.integration
def test_health_reports_extensions_and_bus(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"] is True
    assert body["postgis"] and body["pgrouting"]
    assert body["bus_mode"] in {"redis+notify", "notify"}
    assert body["tables"]["hazards"] == 0


@pytest.mark.integration
def test_metrics_is_prometheus_text(client: TestClient) -> None:
    r = client.get("/metrics")
    assert r.status_code == 200
    assert 'arco_blackboard_rows{table="hazards"} 0' in r.text


@pytest.mark.integration
def test_post_hazard_then_bbox_query(client: TestClient) -> None:
    h = make_hazard(square(0.0, 0.0, 0.001))
    r = client.post("/hazards", params={"scenario_t": 42.0}, json=dump(h))
    assert r.status_code == 201, r.text
    ev = r.json()
    assert ev["type"] == "hazard.created"
    assert ev["scenario_t"] == 42.0
    assert ev["seq"] >= 1

    inside = client.get("/hazards", params={"bbox": "-0.01,-0.01,0.01,0.01"}).json()
    assert [x["id"] for x in inside] == [str(h.id)]
    assert client.get("/hazards", params={"bbox": "10,10,11,11"}).json() == []
    assert client.get(f"/hazards/{h.id}").json()["hazard_class"] == "fire_front"
    assert client.get(f"/hazards/{uuid4()}").status_code == 404

    r2 = client.post(f"/hazards/{h.id}/status", params={"status": "resolved"})
    assert r2.status_code == 200
    assert r2.json()["type"] == "hazard.resolved"
    assert client.get("/hazards", params={"status": "active"}).json() == []


@pytest.mark.integration
def test_bad_bbox_is_422(client: TestClient) -> None:
    assert client.get("/hazards", params={"bbox": "1,2,3"}).status_code == 422
    assert client.get("/hazards", params={"bbox": "2,2,1,1"}).status_code == 422


@pytest.mark.integration
def test_hazard_payload_with_extra_field_is_rejected(client: TestClient) -> None:
    h = json.loads(make_hazard(square(0, 0, 0.001)).model_dump_json())
    h["raw_frame_b64"] = "AAAA"
    assert client.post("/hazards", json=h).status_code == 422


@pytest.mark.integration
def test_reports_safe_zones_and_events_endpoints(client: TestClient) -> None:
    report = CommunityReport(
        id=uuid4(),
        geom=Point(coordinates=[0.001, 0.001]),
        report_type="hazard_pin",
        hazard_class=HazardClass.SMOKE,
        reporter_hash="deadbeefcafebabe",
        device_pubkey="pk",
        signature="sig",
        created_at=datetime.now(UTC),
    )
    r = client.post("/reports", json=dump(report))
    assert r.status_code == 201, r.text
    assert r.json()["type"] == "report.created"
    assert len(client.get("/reports", params={"bbox": "0,0,0.01,0.01"}).json()) == 1

    zone = SafeZone(id="sz-1", name="Civic Center", geom=square(0.02, 0.02, 0.001), capacity=800)
    r = client.put("/safe-zones/sz-1", json=dump(zone))
    assert r.status_code == 200, r.text
    assert client.put("/safe-zones/other", json=dump(zone)).status_code == 422
    assert [z["id"] for z in client.get("/safe-zones").json()] == ["sz-1"]

    assert client.get("/routes").json() == []
    assert client.get("/graph/version").json()["graph_version"] == 0

    events = client.get("/events", params={"since_seq": 0}).json()
    assert [e["topic"] for e in events] == ["report", "safe_zone"]
    only_sz = client.get("/events", params={"since_seq": 0, "topics": "safe_zone"}).json()
    assert len(only_sz) == 1


@pytest.mark.integration
def test_edges_intersecting_endpoint_with_empty_graph(client: TestClient) -> None:
    body = {"geom": square(0, 0, 0.001).model_dump(mode="json")}
    r = client.post("/edges/intersecting", json=body)
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.integration
def test_websocket_streams_live_events_and_catches_up(client: TestClient) -> None:
    first = make_hazard(square(0, 0, 0.001))
    r0 = client.post("/hazards", json=dump(first))
    first_seq = r0.json()["seq"]

    with client.websocket_connect("/ws/blackboard?since_seq=0") as ws:
        # catch-up: the hazard posted before connecting arrives first
        caught = json.loads(ws.receive_text())
        assert caught["type"] == "hazard.created"
        assert caught["seq"] == first_seq

        # live: a new hazard posted while connected is streamed
        second = make_hazard(square(0.5, 0.5, 0.001))
        r1 = client.post("/hazards", params={"scenario_t": 7.5}, json=dump(second))
        live = json.loads(ws.receive_text())
        assert live["type"] == "hazard.created"
        assert live["hazard"]["id"] == str(second.id)
        assert live["seq"] == r1.json()["seq"]
        assert live["scenario_t"] == 7.5

        # a status change is also streamed, with a different type
        client.post(f"/hazards/{second.id}/status", params={"status": "disputed"})
        disputed = json.loads(ws.receive_text())
        assert disputed["type"] == "hazard.disputed"


@pytest.mark.integration
def test_websocket_topic_filter(client: TestClient) -> None:
    with client.websocket_connect("/ws/blackboard?topics=safe_zone") as ws:
        client.post("/hazards", json=dump(make_hazard(square(0, 0, 0.001))))
        zone = SafeZone(id="sz-2", name="Park", geom=square(0.02, 0.02, 0.001), capacity=10)
        client.put("/safe-zones/sz-2", json=dump(zone))
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "safe_zone.updated"  # the hazard event was filtered out
