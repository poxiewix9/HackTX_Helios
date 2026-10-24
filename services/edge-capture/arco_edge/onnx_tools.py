"""Model-agnostic ONNX split, static INT8 quantization and output comparison.

Split inference: `head.onnx` runs on the edge and emits ONE intermediate tensor; `tail.onnx`
consumes it on the base station. A valid split point is therefore a **single-tensor cut**:
a position `k` in the (topologically ordered) node list such that exactly one tensor
produced by nodes `< k` is consumed by nodes `>= k`, and no graph *input* (the image) is
consumed by the tail. The second condition is the privacy guard: if the tail needed the
image, raw pixels would have to cross the network.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort
from onnx import ModelProto, shape_inference
from onnx.utils import Extractor

CPU = ["CPUExecutionProvider"]


@dataclass(frozen=True)
class CutPoint:
    """A valid single-tensor split position."""

    index: int  # nodes[:index] -> head, nodes[index:] -> tail
    tensor: str  # the one tensor crossing the cut
    head_nodes: int
    tail_nodes: int
    producer_op: str


class SplitError(ValueError):
    pass


# ----------------------------------------------------------------------------- analysis


def _graph_inputs(model: ModelProto) -> set[str]:
    inits = {t.name for t in model.graph.initializer}
    return {i.name for i in model.graph.input if i.name not in inits}


def single_cut_points(model: ModelProto) -> list[CutPoint]:
    """Every position where the node list can be cut with exactly one crossing tensor
    and no graph input needed by the tail. Nodes must already be topologically sorted
    (the ONNX spec requires it and every exporter guarantees it)."""
    nodes = list(model.graph.node)
    n = len(nodes)
    producer: dict[str, int] = {}
    max_consumer: dict[str, int] = {}
    for i, node in enumerate(nodes):
        for out in node.output:
            if out:
                producer[out] = i
        for inp in node.input:
            if inp:
                max_consumer[inp] = max(max_consumer.get(inp, -1), i)
    graph_inputs = _graph_inputs(model)
    # last node index that reads a graph input: any cut at or before it is unsafe
    last_input_use = max((max_consumer.get(g, -1) for g in graph_inputs), default=-1)
    graph_outputs = {o.name for o in model.graph.output}

    cuts: list[CutPoint] = []
    for k in range(1, n):
        if k <= last_input_use:
            continue
        crossing = [
            t
            for t, p in producer.items()
            if p < k and (max_consumer.get(t, -1) >= k or t in graph_outputs)
        ]
        if len(crossing) == 1:
            cuts.append(
                CutPoint(
                    index=k,
                    tensor=crossing[0],
                    head_nodes=k,
                    tail_nodes=n - k,
                    producer_op=nodes[producer[crossing[0]]].op_type,
                )
            )
    return cuts


def choose_cut(cuts: Sequence[CutPoint], *, index: int | None, tensor: str | None) -> CutPoint:
    if not cuts:
        raise SplitError("model has no single-tensor cut point after the last image use")
    if tensor is not None:
        for c in cuts:
            if c.tensor == tensor:
                return c
        raise SplitError(f"tensor {tensor!r} is not a valid single-tensor cut")
    if index is None:
        return cuts[len(cuts) // 2]
    if not 0 <= index < len(cuts):
        raise SplitError(f"split index {index} out of range 0..{len(cuts) - 1}")
    return cuts[index]


# ----------------------------------------------------------------------------- split


def split_model(model: ModelProto, cut: CutPoint) -> tuple[ModelProto, ModelProto]:
    """Return (head, tail). Head: graph inputs -> cut tensor. Tail: cut tensor -> outputs."""
    inferred = shape_inference.infer_shapes(model)
    extractor = Extractor(inferred)
    inputs = sorted(_graph_inputs(model))
    outputs = [o.name for o in model.graph.output]
    head = extractor.extract_model(inputs, [cut.tensor])
    tail = extractor.extract_model([cut.tensor], outputs)
    if any(i.name in _graph_inputs(model) for i in tail.graph.input):
        raise SplitError("tail would require a graph input (raw frame); refusing split")
    return head, tail


# ----------------------------------------------------------------------------- run / compare


def session(path: str | Path | bytes, providers: Sequence[str] = CPU) -> ort.InferenceSession:
    so = ort.SessionOptions()
    so.log_severity_level = 3
    src: Any = path if isinstance(path, bytes) else str(path)
    return ort.InferenceSession(src, so, providers=list(providers))


def run(sess: ort.InferenceSession, feed: dict[str, np.ndarray]) -> list[np.ndarray]:
    return [np.asarray(o) for o in sess.run(None, feed)]


@dataclass
class Comparison:
    max_abs: float
    mean_abs: float
    cosine: float
    ref_abs_max: float

    def as_dict(self) -> dict[str, float]:
        return {
            "max_abs": self.max_abs,
            "mean_abs": self.mean_abs,
            "cosine": self.cosine,
            "ref_abs_max": self.ref_abs_max,
        }


def compare(ref: np.ndarray, other: np.ndarray) -> Comparison:
    a = ref.astype(np.float64).ravel()
    b = other.astype(np.float64).ravel()
    if a.shape != b.shape:
        raise ValueError(f"shape mismatch {ref.shape} vs {other.shape}")
    diff = np.abs(a - b)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    cos = float(a @ b / denom) if denom > 0 else 1.0
    return Comparison(
        max_abs=float(diff.max()) if diff.size else 0.0,
        mean_abs=float(diff.mean()) if diff.size else 0.0,
        cosine=cos,
        ref_abs_max=float(np.abs(a).max()) if a.size else 0.0,
    )


def compare_split(
    full: ort.InferenceSession,
    head: ort.InferenceSession,
    tail: ort.InferenceSession,
    inputs: Iterable[np.ndarray],
) -> list[Comparison]:
    """Full-model outputs vs head->tail outputs, per input; first graph output only when
    several (the detection tensor for YOLO)."""
    in_name = full.get_inputs()[0].name
    head_in = head.get_inputs()[0].name
    tail_in = tail.get_inputs()[0].name
    out: list[Comparison] = []
    for x in inputs:
        ref = run(full, {in_name: x})
        feat = run(head, {head_in: x})[0]
        got = run(tail, {tail_in: feat})
        out.append(compare(ref[0], got[0]))
    return out


# ----------------------------------------------------------------------------- quantization


class _CalibrationReader:
    """onnxruntime CalibrationDataReader over an iterable of input tensors."""

    def __init__(self, input_name: str, samples: Iterable[np.ndarray]) -> None:
        self._it: Iterator[np.ndarray] = iter(samples)
        self._name = input_name

    def get_next(self) -> dict[str, np.ndarray] | None:
        try:
            return {self._name: next(self._it)}
        except StopIteration:
            return None

    def rewind(self) -> None:  # pragma: no cover - ORT may call it
        return None


def quantize_static_int8(
    model_in: Path,
    model_out: Path,
    calibration: Sequence[np.ndarray],
    *,
    per_channel: bool = True,
) -> Path:
    """Post-training static INT8 (QDQ format, MinMax calibration).

    Weights int8 per-channel, activations uint8 — the standard CPU/TensorRT-friendly
    recipe. The quantized graph still takes/returns float tensors (QDQ pairs inside), so
    callers do not change; the *transport* quantization of the cut tensor is separate
    (see `arco_edge.packet`)."""
    from onnxruntime.quantization import (  # noqa: PLC0415 - slow import, export-time only
        CalibrationMethod,
        QuantFormat,
        QuantType,
        quantize_static,
    )
    from onnxruntime.quantization.shape_inference import (  # noqa: PLC0415
        quant_pre_process,
    )

    if not calibration:
        raise ValueError("static quantization needs at least one calibration sample")
    pre = model_out.with_suffix(".pre.onnx")
    try:
        quant_pre_process(str(model_in), str(pre), skip_symbolic_shape=False)
        src = pre
    except Exception:  # pre-processing is an optimisation; quantize the raw model instead
        src = model_in
    in_name = session(src).get_inputs()[0].name
    quantize_static(
        str(src),
        str(model_out),
        _CalibrationReader(in_name, calibration),
        quant_format=QuantFormat.QDQ,
        per_channel=per_channel,
        weight_type=QuantType.QInt8,
        activation_type=QuantType.QUInt8,
        calibrate_method=CalibrationMethod.MinMax,
    )
    if pre.exists():
        pre.unlink()
    return model_out


# ----------------------------------------------------------------------------- manifest


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class SplitManifest:
    """What `scripts/export_split.py` writes next to the ONNX files (models/<id>/manifest.json)."""

    model_id: str
    task: str  # "segment" | "detect"
    imgsz: int
    input_name: str
    split_index: int
    split_tensor: str
    cut_shape: list[int]
    class_names: list[str]
    files: dict[str, str]  # role -> filename   (full_fp32, head_fp32, head_int8, tail_fp32, ...)
    sha256: dict[str, str]
    tolerance: dict[str, Any] = field(default_factory=dict)  # measured + documented bound
    notes: str = ""

    def dump(self, path: Path) -> None:
        path.write_text(json.dumps(self.__dict__, indent=2) + "\n")

    @classmethod
    def load(cls, path: Path) -> SplitManifest:
        return cls(**json.loads(path.read_text()))


def tensor_elements(shape: Sequence[int]) -> int:
    return math.prod(int(s) for s in shape)
