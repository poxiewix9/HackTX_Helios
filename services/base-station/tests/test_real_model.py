"""Runs only when `models/<id>/manifest.json` exists (scripts/export_split.py): the real
INT8 head on the edge -> packet -> real FP32 tail on the base station, plus the unsplit
FP32 baseline in-process. Checks the mechanics and prints MEASURED timings; detections
on synthetic frames are not asserted (COCO weights, synthetic scene)."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import httpx
import numpy as np
import pytest
from websockets.sync.client import connect

from arco_base.api import create_app as create_base_app
from arco_base.detectors import FullDetector, SplitDetector, select_providers
from arco_base.settings import DEFAULT_CLASS_MAP, BaseStationSettings
from arco_edge.client import WebSocketLink
from arco_edge.head import OnnxHead
from arco_edge.main import EdgeNode, load_georef
from arco_edge.packet import decode, encode
from arco_edge.settings import EdgeSettings
from arco_edge.sources import SyntheticSource
from arco_schemas import TelemetryMessage

REPO_ROOT = Path(__file__).resolve().parents[3]
GEOREF_DIR = REPO_ROOT / "data" / "samples" / "vision" / "georef"
MODELS_DIR = Path(os.environ.get("ARCO_MODELS_DIR", REPO_ROOT / "models"))
MODEL_ID = os.environ.get("ARCO_MODEL_ID", "yolov8n-seg")
MODEL_DIR = MODELS_DIR / MODEL_ID

pytestmark = pytest.mark.skipif(
    not (MODEL_DIR / "manifest.json").exists(),
    reason="no exported model; run scripts/export_split.py",
)


def test_int8_head_packet_to_tail_detector() -> None:
    head = OnnxHead(MODEL_DIR, quantized=True)
    assert head.variant == "head_int8"
    det = SplitDetector(
        MODEL_DIR,
        DEFAULT_CLASS_MAP,
        providers=select_providers(None),
        conf_thr=0.25,
        iou_thr=0.5,
    )
    georef = load_georef(GEOREF_DIR / "camera_front_st.json")
    frame = next(iter(SyntheticSource(width=1280, height=720, seed=0).frames())).image
    tensor, _lb, head_ms = head.run(frame)
    assert list(tensor.shape) == head.manifest.cut_shape
    enc = encode(
        tensor,
        header_fields={
            "stream": "micro",
            "frame_id": 0,
            "source_id": "cam-front-st",
            "captured_wall_t": time.time(),
            "scenario_t": None,
            "model_id": head.model_id,
            "split_layer": head.split_layer,
            "head_ms": head_ms,
            "georef": georef,
        },
    )
    pkt = decode(enc.frame)
    # transport quantization of the cut tensor stays within its scale/2 bound
    assert np.abs(pkt.tensor - tensor).max() <= enc.header.scale / 2 + 1e-6
    out = det.detect(pkt)
    assert out.stage_ms["tail"] > 0 and out.image_width == 1280
    raw_bytes = frame.nbytes
    print(
        f"REAL MODEL {MODEL_ID}: head_int8={head_ms:.1f}ms serialize={enc.serialize_ms:.1f}ms "
        f"packet={len(enc.frame)}B ({raw_bytes / len(enc.frame):.0f}x smaller than raw BGR) "
        f"tail={out.stage_ms['tail']:.1f}ms post={out.stage_ms['postprocess']:.2f}ms "
        f"providers={det.providers} detections={len(out.detections)}"
    )
    # baseline: unsplit FP32 in-process (the benchmark's pipeline A)
    full = FullDetector(
        MODEL_DIR, DEFAULT_CLASS_MAP, providers=select_providers(None), conf_thr=0.25, iou_thr=0.5
    )
    _res, full_ms = full.detect_frame(frame, "micro", georef)
    print(f"REAL MODEL {MODEL_ID}: full_fp32={full_ms:.1f}ms (same frame, same process)")
    assert full_ms > 0


@pytest.mark.integration
def test_real_model_through_base_station(served_factory: object) -> None:
    """Edge INT8 head over WebSocket -> base station onnx_split -> telemetry with measured
    head/tail/network. Blackboard writes are pointed at an unused port: with COCO weights
    on a synthetic scene there is nothing to write, and a failed write must not break
    the frame path (it is logged + surfaced on /health)."""
    base = served_factory(  # type: ignore[operator]
        create_base_app(
            BaseStationSettings(
                arco_detector="onnx_split",
                arco_models_dir=MODELS_DIR,
                arco_model_id=MODEL_ID,
                blackboard_url="http://127.0.0.1:9",
            )
        )
    )
    url = base.url.replace("http", "ws")

    samples: list[dict[str, object]] = []
    halt = threading.Event()

    def collect() -> None:
        with connect(url + "/ws/telemetry") as ws:
            while not halt.is_set():
                try:
                    samples.append(json.loads(ws.recv(timeout=0.2)))
                except TimeoutError:
                    continue

    th = threading.Thread(target=collect, daemon=True)
    th.start()
    time.sleep(0.3)
    settings = EdgeSettings(
        arco_detector="onnx_split",
        arco_models_dir=MODELS_DIR,
        arco_model_id=MODEL_ID,
        arco_edge_georef_path=GEOREF_DIR / "camera_front_st.json",
        arco_edge_realtime=False,
        arco_base_station_url=url + "/ws/features",
    )
    n = 6
    node = EdgeNode(
        settings,
        source=SyntheticSource(width=1280, height=720, seed=1),
        link=WebSocketLink(settings.arco_base_station_url, queue_size=16),
    )
    stats = node.run(max_frames=n)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and httpx.get(base.url + "/health").json()["frames"] < n:
        time.sleep(0.1)
    time.sleep(0.3)
    halt.set()
    th.join(timeout=3)
    health = httpx.get(base.url + "/health").json()
    assert health["frames"] == n and health["detector"] == "onnx_split"
    tele = [TelemetryMessage.model_validate(m).sample for m in samples]
    assert len(tele) == n
    s = tele[-1]
    assert s.pipeline == "int8_split" and s.mode == "live"
    assert s.stage_ms["head"] > 0 and s.stage_ms["tail"] > 0 and s.stage_ms["serialize"] > 0
    mean = {k: round(float(np.mean([t.stage_ms[k] for t in tele])), 2) for k in s.stage_ms}
    print(
        f"REAL MODEL E2E {n} frames: mean stage ms={mean} mean total="
        f"{np.mean([t.total_ms for t in tele]):.1f}ms mean packet={stats.bytes_sent // n}B"
    )
