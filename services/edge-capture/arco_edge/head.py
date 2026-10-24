"""Edge-side inference: the ONNX head (first N layers) or the synthetic stand-in."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Protocol

import numpy as np

from arco_edge.onnx_tools import SplitManifest, session
from arco_edge.preprocess import Letterbox, letterbox_tensor


class Head(Protocol):
    model_id: str
    split_layer: int
    imgsz: int

    def run(self, frame_bgr: np.ndarray) -> tuple[np.ndarray, Letterbox, float]:
        """-> (cut tensor, letterbox used, measured head_ms)."""
        ...


class OnnxHead:
    def __init__(self, model_dir: Path, *, quantized: bool = True) -> None:
        self.manifest = SplitManifest.load(model_dir / "manifest.json")
        key = "head_int8" if quantized and "head_int8" in self.manifest.files else "head_fp32"
        self.path = model_dir / self.manifest.files[key]
        self._sess = session(self.path)
        self._in = self._sess.get_inputs()[0].name
        self.model_id = f"{self.manifest.model_id}@{key}"
        self.split_layer = self.manifest.split_index
        self.imgsz = self.manifest.imgsz
        self.variant = key

    def run(self, frame_bgr: np.ndarray) -> tuple[np.ndarray, Letterbox, float]:
        x, lb = letterbox_tensor(frame_bgr, self.imgsz)
        t0 = time.perf_counter()
        out = self._sess.run(None, {self._in: x})[0]
        return np.asarray(out), lb, (time.perf_counter() - t0) * 1000.0


class SyntheticHead:
    """No model: emits a 1-element tensor so the packet path is exercised end to end.
    The base station's synthetic detector ignores the tensor and produces scripted
    detections. Never carries pixels."""

    model_id = "synthetic"
    split_layer = 0

    def __init__(self, imgsz: int = 640) -> None:
        self.imgsz = imgsz

    def run(self, frame_bgr: np.ndarray) -> tuple[np.ndarray, Letterbox, float]:
        t0 = time.perf_counter()
        h, w = frame_bgr.shape[:2]
        lb = Letterbox.for_size(w, h, self.imgsz)
        return np.zeros((1,), dtype=np.float32), lb, (time.perf_counter() - t0) * 1000.0
