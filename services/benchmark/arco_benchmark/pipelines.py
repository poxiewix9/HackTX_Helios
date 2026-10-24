"""The two pipelines under test, behind one small interface.

Both consume the same `Frame` objects and return the nine contract stages in ms plus the
bytes that crossed (or would cross) the edge link. Stages a pipeline does not have are
reported as 0.0 — they are still listed so the two rows line up column by column.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np

from arco_base.detectors import FullDetector, SplitDetector
from arco_edge.head import OnnxHead
from arco_edge.packet import decode, encode
from arco_edge.sources import Frame
from arco_schemas import STAGES, PipelineKind, StreamKind


@dataclass
class StageResult:
    stage_ms: dict[str, float]
    bytes_sent: int
    detections: int

    def total_ms(self) -> float:
        return float(sum(self.stage_ms.values()))


def _zero_stages() -> dict[str, float]:
    return dict.fromkeys(STAGES, 0.0)


class FramePipeline(Protocol):
    kind: PipelineKind
    model_id: str
    providers: list[str]

    def process(self, frame: Frame) -> StageResult: ...


class FullPipeline:
    """Pipeline A. The frame has to reach the model, so we charge what a real deployment
    would pay to ship it: JPEG encode (`serialize`), the JPEG bytes (`bytes_sent`), JPEG
    decode (`deserialize`). `network` is 0 because both halves run here; the same holds
    for pipeline B, so the comparison is still like-for-like."""

    kind: PipelineKind = "fp32_full"

    def __init__(
        self,
        detector: FullDetector,
        *,
        stream: StreamKind,
        georef: Any,
        jpeg_quality: int = 90,
    ) -> None:
        self.detector = detector
        self.stream = stream
        self.georef = georef
        self.jpeg_quality = jpeg_quality
        self.model_id = detector.model_id
        self.providers = list(detector._sess.get_providers())

    def process(self, frame: Frame) -> StageResult:
        stages = _zero_stages()
        stages["capture"] = frame.capture_ms
        t0 = time.perf_counter()
        ok, buf = cv2.imencode(
            ".jpg", frame.image, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality]
        )
        if not ok:  # pragma: no cover - imencode on a valid BGR array does not fail
            raise RuntimeError("JPEG encode failed")
        stages["serialize"] = (time.perf_counter() - t0) * 1000.0
        payload = buf.tobytes()
        t1 = time.perf_counter()
        decoded = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
        if decoded is None:  # pragma: no cover - we just encoded it
            raise RuntimeError("JPEG decode failed")
        image: np.ndarray = decoded
        stages["deserialize"] = (time.perf_counter() - t1) * 1000.0
        det, _model_ms = self.detector.detect_frame(image, self.stream, self.georef)
        stages.update(det.stage_ms)  # tail (= full model), postprocess, georef
        return StageResult(stages, len(payload), len(det.detections))


class SplitPipeline:
    """Pipeline B, the production topology in one process."""

    kind: PipelineKind = "int8_split"

    def __init__(
        self,
        head: OnnxHead,
        detector: SplitDetector,
        *,
        stream: StreamKind,
        georef: Any,
        source_id: str = "bench",
    ) -> None:
        self.head = head
        self.detector = detector
        self.stream = stream
        self.georef = georef
        self.source_id = source_id
        self.model_id = f"{head.model_id} -> {detector.model_id}"
        self.providers = list(detector.providers)

    def process(self, frame: Frame) -> StageResult:
        stages = _zero_stages()
        stages["capture"] = frame.capture_ms
        tensor, _lb, head_ms = self.head.run(frame.image)
        stages["head"] = head_ms
        enc = encode(
            tensor,
            header_fields={
                "stream": self.stream,
                "frame_id": frame.frame_id,
                "source_id": self.source_id,
                "captured_wall_t": frame.captured_wall_t,
                "scenario_t": frame.scenario_t,
                "model_id": self.head.model_id,
                "split_layer": self.head.split_layer,
                "head_ms": head_ms,
                "georef": self.georef,
            },
        )
        stages["serialize"] = enc.serialize_ms
        pkt = decode(enc.frame)
        stages["deserialize"] = pkt.deserialize_ms
        det = self.detector.detect(pkt)
        stages.update(det.stage_ms)  # tail, postprocess, georef
        return StageResult(stages, len(enc.frame), len(det.detections))


def build_pipelines(
    *,
    models_dir: Path,
    model_id: str,
    class_map: Path,
    providers: list[str],
    stream: StreamKind,
    georef: Any,
    conf_thr: float = 0.3,
    iou_thr: float = 0.5,
) -> tuple[FullPipeline, SplitPipeline]:
    """Both pipelines for one stream, sharing the same model directory."""
    model_dir = models_dir / model_id
    if not (model_dir / "manifest.json").exists():
        msg = (
            f"no exported model at {model_dir}/manifest.json — run "
            "`make export-split WEIGHTS=...` first; the benchmark never fakes a model"
        )
        raise FileNotFoundError(msg)
    full = FullDetector(
        model_dir, class_map, providers=providers, conf_thr=conf_thr, iou_thr=iou_thr
    )
    split = SplitDetector(
        model_dir, class_map, providers=providers, conf_thr=conf_thr, iou_thr=iou_thr
    )
    head = OnnxHead(model_dir, quantized=True)
    return (
        FullPipeline(full, stream=stream, georef=georef),
        SplitPipeline(head, split, stream=stream, georef=georef),
    )
