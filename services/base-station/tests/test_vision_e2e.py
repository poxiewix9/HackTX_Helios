"""4.2 — one real frame path: edge (synthetic source + head) -> WebSocket -> base station
(synthetic detector) -> georef -> blackboard REST -> hazard row in PostGIS, with per-stage
telemetry on /ws/telemetry and detections on /ws/detections. Both servers run on real
loopback sockets; the database is the isolated test DB."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
from websockets.sync.client import connect

from arco_base.api import create_app as create_base_app
from arco_base.settings import BaseStationSettings
from arco_blackboard.api import create_app as create_blackboard_app
from arco_blackboard.db import get_engine
from arco_blackboard.migrate import upgrade_head
from arco_blackboard.settings import get_settings as blackboard_settings
from arco_blackboard.testing import truncate_all
from arco_edge.client import WebSocketLink
from arco_edge.head import SyntheticHead
from arco_edge.main import EdgeNode, load_georef
from arco_edge.packet import encode
from arco_edge.settings import EdgeSettings
from arco_edge.sources import SyntheticSource
from arco_schemas import DetectionsMessage, TelemetryMessage
from arco_schemas.telemetry import STAGES

GEOREF_DIR = Path(__file__).resolve().parents[3] / "data" / "samples" / "vision" / "georef"


class WsCollector(threading.Thread):
    def __init__(self, url: str) -> None:
        super().__init__(daemon=True)
        self.url = url
        self.messages: list[dict[str, Any]] = []
        self._halt = threading.Event()
        self.ready = threading.Event()

    def run(self) -> None:
        with connect(self.url) as ws:
            self.ready.set()
            while not self._halt.is_set():
                try:
                    self.messages.append(json.loads(ws.recv(timeout=0.2)))
                except TimeoutError:
                    continue
                except Exception:
                    return

    def stop(self) -> None:
        self._halt.set()
        self.join(timeout=3)


@pytest.mark.integration
def test_frame_to_hazard_row_with_telemetry(served_factory: Any) -> None:
    upgrade_head()
    engine = get_engine()
    truncate_all(engine)

    bb = served_factory(create_blackboard_app(blackboard_settings(), publisher_name="vision-e2e"))
    base_settings = BaseStationSettings(
        arco_detector="synthetic",
        blackboard_url=bb.url,
        arco_hazard_repost_s=0.5,
    )
    base = served_factory(create_base_app(base_settings))

    det_ws = WsCollector(base.url.replace("http", "ws") + "/ws/detections")
    tel_ws = WsCollector(base.url.replace("http", "ws") + "/ws/telemetry")
    det_ws.start()
    tel_ws.start()
    assert det_ws.ready.wait(5) and tel_ws.ready.wait(5)

    n_frames = 12
    fps = 2.0  # scenario_t = frame/fps -> 0..5.5 s: the scripted smoke entry starts at t=3
    edge_settings = EdgeSettings(
        arco_detector="synthetic",
        arco_edge_stream="micro",
        arco_edge_georef_path=GEOREF_DIR / "camera_front_st.json",
        arco_edge_width=1280,
        arco_edge_height=720,
        arco_edge_fps=fps,
        arco_edge_realtime=False,
        arco_base_station_url=base.url.replace("http", "ws") + "/ws/features",
    )
    link = WebSocketLink(edge_settings.arco_base_station_url, queue_size=64)
    node = EdgeNode(
        edge_settings,
        source=SyntheticSource(width=1280, height=720, fps=fps, seed=0),
        head=SyntheticHead(),
        link=link,
    )
    t0 = time.perf_counter()
    stats = node.run(max_frames=n_frames)
    assert stats.frames == n_frames
    assert link.stats.sent == n_frames and link.stats.dropped == 0

    # wait for the base station to drain
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        health = httpx.get(base.url + "/health").json()
        if health["frames"] >= n_frames:
            break
        time.sleep(0.1)
    wall_ms = (time.perf_counter() - t0) * 1000
    assert health["frames"] == n_frames, health
    assert health["detector"] == "synthetic" and health["last_error"] is None
    time.sleep(0.3)
    det_ws.stop()
    tel_ws.stop()

    # --- detections channel: typed, labelled synthetic, georeferenced
    dets = [DetectionsMessage.model_validate(m) for m in det_ws.messages]
    assert len(dets) == n_frames
    assert {d.detector for d in dets} == {"synthetic"}
    assert all(d.image_width == 1280 and d.image_height == 720 for d in dets)
    first, last = dets[0], dets[-1]
    assert [x.hazard_class.value for x in first.detections] == ["debris"]
    assert {x.hazard_class.value for x in last.detections} == {"debris", "smoke"}
    assert all(x.footprint is not None and len(x.footprint) >= 4 for x in last.detections)
    assert all(d.latency_ms > 0 for d in dets)
    assert [d.seq for d in dets] == sorted(d.seq for d in dets)

    # --- telemetry channel: measured per-stage, live, every stage key present
    tele = [TelemetryMessage.model_validate(m).sample for m in tel_ws.messages]
    assert len(tele) == n_frames
    s = tele[-1]
    assert s.mode == "live" and s.pipeline == "int8_split" and s.stream == "micro"
    assert set(s.stage_ms) == set(STAGES)
    assert s.stage_ms["deserialize"] > 0 and s.stage_ms["georef"] > 0
    assert s.total_ms == pytest.approx(sum(s.stage_ms.values()))
    assert s.bytes_sent > 0 and s.dropped_frames == 0 and s.fps > 0
    assert any(t.stage_ms["db_write"] > 0 for t in tele), "no frame wrote to the blackboard"
    print(
        f"VISION E2E {n_frames} frames: wall={wall_ms:.0f}ms "
        f"last total={s.total_ms:.1f}ms stages={ {k: round(v, 2) for k, v in s.stage_ms.items()} }"
    )

    # --- prometheus
    metrics = httpx.get(base.url + "/metrics").text
    assert "arco_stage_ms_bucket" in metrics and 'stage="tail"' in metrics
    assert "arco_frames_total" in metrics

    # --- hazard rows in PostGIS (via the blackboard REST, same DB)
    hazards = httpx.get(bb.url + "/hazards").json()
    by_class = {h["hazard_class"]: h for h in hazards}
    assert set(by_class) == {"debris", "smoke"}, hazards
    # 12 frames but ONE row per (camera, class): consecutive frames updated the same id
    assert len(hazards) == 2
    debris = by_class["debris"]
    assert debris["source"] == "simulated" and debris["stream"] == "micro"
    assert debris["camera_id"] == "cam-front-st" and debris["status"] == "active"
    ring = debris["geom"]["coordinates"][0]
    cam = json.loads((GEOREF_DIR / "camera_front_st.json").read_text())
    for lon, lat in ring:
        assert abs(lat - cam["lat"]) < 0.001 and abs(lon - cam["lon"]) < 0.001  # within ~100 m
    # the blackboard emitted hazard events for them (agents consume these in Phase 3)
    events = httpx.get(bb.url + "/events", params={"since_seq": 0, "topics": "hazard"}).json()
    kinds = {e["message"]["type"] for e in events}
    assert "hazard.created" in kinds and "hazard.updated" in kinds


@pytest.mark.integration
def test_post_features_http_path(served_factory: Any) -> None:
    """The HTTP ingress accepts the same binary frame and returns the typed message."""
    upgrade_head()
    truncate_all(get_engine())
    bb = served_factory(create_blackboard_app(blackboard_settings(), publisher_name="vision-http"))
    base_settings = BaseStationSettings(arco_detector="synthetic", blackboard_url=bb.url)
    base = served_factory(create_base_app(base_settings))
    enc = encode(
        np.zeros((1,), dtype=np.float32),
        header_fields={
            "stream": "macro",
            "frame_id": 0,
            "source_id": "tile-lahaina-0",
            "captured_wall_t": time.time(),
            "scenario_t": 10.0,
            "model_id": "synthetic",
            "split_layer": 0,
            "head_ms": 0.0,
            "georef": load_georef(GEOREF_DIR / "tile_lahaina_0.json"),
        },
    )
    r = httpx.post(base.url + "/features", content=enc.frame, timeout=10)
    assert r.status_code == 200, r.text
    msg = DetectionsMessage.model_validate(r.json())
    assert msg.stream == "macro" and msg.detector == "synthetic"
    assert [d.hazard_class.value for d in msg.detections] == ["fire_front"]
    fp = msg.detections[0].footprint
    assert fp is not None
    lon0, lat0, lon1, lat1 = json.loads((GEOREF_DIR / "tile_lahaina_0.json").read_text())["bounds"]
    assert all(lon0 <= p[0] <= lon1 and lat0 <= p[1] <= lat1 for p in fp)
    hz = httpx.get(bb.url + "/hazards").json()
    assert len(hz) == 1 and hz[0]["source"] == "simulated" and hz[0]["hazard_class"] == "fire_front"
