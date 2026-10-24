"""Detectors: ONNX split tail, ONNX full model (local baseline), synthetic fallback.

Every detector returns `Detected` with *measured* stage timings; `detector` on the
outgoing `DetectionsMessage` names which one produced the result so the UI can label it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import onnxruntime as ort
import yaml

from arco_base import classmap, georef, postprocess
from arco_base.classmap import ClassMap
from arco_edge.logging import get_logger
from arco_edge.onnx_tools import SplitManifest, session
from arco_edge.packet import Decoded
from arco_edge.preprocess import Letterbox, letterbox_tensor
from arco_schemas import Detection, HazardClass
from arco_schemas.hazard_classes import StreamKind
from arco_schemas.vision import DetectorKind, FeaturePacketHeader

log = get_logger("base.detectors")

PROVIDER_PREFERENCE = (
    "TensorrtExecutionProvider",
    "CUDAExecutionProvider",
    "CPUExecutionProvider",
)


def select_providers(override: str | None = None) -> list[str]:
    """TensorRT > CUDA > CPU, filtered by what this onnxruntime build actually has.
    `override` = comma-separated list (ARCO_ORT_PROVIDERS)."""
    available = set(ort.get_available_providers())
    if override:
        wanted = [p.strip() for p in override.split(",") if p.strip()]
        chosen = [p for p in wanted if p in available]
    else:
        chosen = [p for p in PROVIDER_PREFERENCE if p in available]
    if "CPUExecutionProvider" not in chosen:
        chosen.append("CPUExecutionProvider")
    return chosen


@dataclass
class Detected:
    detections: list[Detection]
    stage_ms: dict[str, float] = field(default_factory=dict)
    image_width: int = 0
    image_height: int = 0


class Detector(Protocol):
    kind: DetectorKind
    model_id: str

    def detect(self, packet: Decoded) -> Detected: ...


def _georef_detections(
    header: FeaturePacketHeader, grouped: list[classmap.Grouped]
) -> tuple[list[Detection], float]:
    t0 = time.perf_counter()
    geo = georef.build(header.georef)
    out: list[Detection] = []
    for g in grouped:
        ring = georef.ring_to_lonlat(geo, g.polygon_px)
        out.append(
            Detection(
                hazard_class=g.hazard_class,
                confidence=round(g.confidence, 4),
                bbox_px=[round(v, 1) for v in g.bbox_px],
                mask_polygon_px=[[round(x, 1), round(y, 1)] for x, y in g.polygon_px],
                footprint=ring,
            )
        )
    return out, (time.perf_counter() - t0) * 1000.0


# ----------------------------------------------------------------------------- split tail


class SplitDetector:
    kind: DetectorKind = "onnx_split"

    def __init__(
        self,
        model_dir: Path,
        class_map_path: Path,
        *,
        providers: list[str],
        conf_thr: float,
        iou_thr: float,
    ) -> None:
        self.manifest = SplitManifest.load(model_dir / "manifest.json")
        self.path = model_dir / self.manifest.files["tail_fp32"]
        self._sess = session(self.path, providers)
        self._in = self._sess.get_inputs()[0].name
        self.providers = self._sess.get_providers()
        self.model_id = f"{self.manifest.model_id}@tail_fp32"
        self.class_names = self.manifest.class_names
        self.cmap: ClassMap = ClassMap.for_model(class_map_path, self.class_names)
        self.conf_thr, self.iou_thr = conf_thr, iou_thr
        log.info(
            "tail loaded",
            model=self.model_id,
            providers=self.providers,
            class_map=self.cmap.mapping,
            cut_shape=self.manifest.cut_shape,
        )

    def detect(self, packet: Decoded) -> Detected:
        h = packet.header
        t0 = time.perf_counter()
        outs = [np.asarray(o) for o in self._sess.run(None, {self._in: packet.tensor})]
        tail_ms = (time.perf_counter() - t0) * 1000.0
        g = h.georef
        lb = Letterbox.for_size(g.image_width, g.image_height, self.manifest.imgsz)
        t1 = time.perf_counter()
        inst = postprocess.decode(
            outs,
            lb,
            num_classes=len(self.class_names),
            conf_thr=self.conf_thr,
            iou_thr=self.iou_thr,
        )
        grouped = classmap.apply(
            self.cmap, h.stream, self.class_names, inst, default_min_conf=self.conf_thr
        )
        post_ms = (time.perf_counter() - t1) * 1000.0
        dets, geo_ms = _georef_detections(h, grouped)
        return Detected(
            dets,
            {"tail": tail_ms, "postprocess": post_ms, "georef": geo_ms},
            g.image_width,
            g.image_height,
        )


# ----------------------------------------------------------------------------- full model


class FullDetector:
    """FP32 unsplit model run on a frame IN THIS PROCESS. Baseline for the benchmark
    (Phase 5) — it needs pixels, so it can only ever run where the frames already are."""

    kind: DetectorKind = "onnx_full"

    def __init__(
        self,
        model_dir: Path,
        class_map_path: Path,
        *,
        providers: list[str],
        conf_thr: float,
        iou_thr: float,
    ) -> None:
        self.manifest = SplitManifest.load(model_dir / "manifest.json")
        self.path = model_dir / self.manifest.files["full_fp32"]
        self._sess = session(self.path, providers)
        self._in = self._sess.get_inputs()[0].name
        self.model_id = f"{self.manifest.model_id}@full_fp32"
        self.class_names = self.manifest.class_names
        self.cmap = ClassMap.for_model(class_map_path, self.class_names)
        self.conf_thr, self.iou_thr = conf_thr, iou_thr

    def detect_frame(
        self, frame_bgr: np.ndarray, stream: StreamKind, georef_cfg: Any
    ) -> tuple[Detected, float]:
        """-> (Detected, model_ms). Letterbox counted in `postprocess`."""
        x, lb = letterbox_tensor(frame_bgr, self.manifest.imgsz)
        t0 = time.perf_counter()
        outs = [np.asarray(o) for o in self._sess.run(None, {self._in: x})]
        model_ms = (time.perf_counter() - t0) * 1000.0
        t1 = time.perf_counter()
        inst = postprocess.decode(
            outs,
            lb,
            num_classes=len(self.class_names),
            conf_thr=self.conf_thr,
            iou_thr=self.iou_thr,
        )
        grouped = classmap.apply(
            self.cmap, stream, self.class_names, inst, default_min_conf=self.conf_thr
        )
        post_ms = (time.perf_counter() - t1) * 1000.0
        header = FeaturePacketHeader(
            stream=stream,
            frame_id=0,
            source_id="local",
            captured_wall_t=time.time(),
            model_id=self.model_id,
            split_layer=0,
            shape=[1],
            dtype="float32",
            scale=1.0,
            zero_point=0,
            compression="none",
            payload_bytes=0,
            head_ms=0.0,
            serialize_ms=0.0,
            georef=georef_cfg,
        )
        dets, geo_ms = _georef_detections(header, grouped)
        return (
            Detected(
                dets,
                {"tail": model_ms, "postprocess": post_ms, "georef": geo_ms},
                lb.width,
                lb.height,
            ),
            model_ms,
        )

    def detect(self, packet: Decoded) -> Detected:  # pragma: no cover - not a network detector
        raise RuntimeError("FullDetector needs frames; it cannot run on feature packets")


# ----------------------------------------------------------------------------- synthetic


@dataclass(frozen=True)
class ScriptEntry:
    stream: StreamKind
    source_id: str
    hazard_class: HazardClass
    bbox_px: tuple[float, float, float, float]
    confidence: float
    start_t: float
    end_t: float | None
    grow_px_per_s: float

    def active(self, t: float) -> bool:
        return t >= self.start_t and (self.end_t is None or t <= self.end_t)

    def box_at(self, t: float, width: int, height: int) -> list[float]:
        g = max(0.0, t - self.start_t) * self.grow_px_per_s
        x0, y0, x1, y1 = self.bbox_px
        return [
            max(0.0, x0 - g),
            max(0.0, y0 - g),
            min(float(width - 1), x1 + g),
            min(float(height - 1), y1 + g),
        ]


class SyntheticDetector:
    """SIMULATED detections from `config/synthetic.yaml`; ignores the tensor. Emits
    `detector="synthetic"` so nothing downstream can mistake it for inference."""

    kind: DetectorKind = "synthetic"
    model_id = "synthetic"

    def __init__(self, schedule_path: Path) -> None:
        raw = yaml.safe_load(schedule_path.read_text()) or {}
        self.entries = [
            ScriptEntry(
                stream=e["stream"],
                source_id=str(e.get("source_id", "*")),
                hazard_class=HazardClass(e["hazard_class"]),
                bbox_px=tuple(float(v) for v in e["bbox_px"]),  # type: ignore[arg-type]
                confidence=float(e["confidence"]),
                start_t=float(e.get("start_t", 0.0)),
                end_t=None if e.get("end_t") is None else float(e["end_t"]),
                grow_px_per_s=float(e.get("grow_px_per_s", 0.0)),
            )
            for e in raw.get("entries", [])
        ]
        self._first_seen: dict[str, float] = {}

    def _time(self, h: FeaturePacketHeader) -> float:
        if h.scenario_t is not None and h.scenario_t >= 0:
            return h.scenario_t
        first = self._first_seen.setdefault(h.source_id, h.captured_wall_t)
        return h.captured_wall_t - first

    def detect(self, packet: Decoded) -> Detected:
        h = packet.header
        t0 = time.perf_counter()
        t = self._time(h)
        g = h.georef
        grouped: list[classmap.Grouped] = []
        for e in self.entries:
            if e.stream != h.stream or e.source_id not in ("*", h.source_id) or not e.active(t):
                continue
            x0, y0, x1, y1 = e.box_at(t, g.image_width, g.image_height)
            ring = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
            grouped.append(
                classmap.Grouped(e.hazard_class, e.confidence, [x0, y0, x1, y1], ring, 1)
            )
        post_ms = (time.perf_counter() - t0) * 1000.0
        dets, geo_ms = _georef_detections(h, grouped)
        return Detected(
            dets,
            {"tail": 0.0, "postprocess": post_ms, "georef": geo_ms},
            g.image_width,
            g.image_height,
        )
