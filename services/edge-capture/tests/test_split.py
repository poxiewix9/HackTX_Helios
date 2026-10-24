"""4.1 — split correctness, cut-point analysis (privacy guard), static INT8 quantization,
and the real exported model when `models/<id>/manifest.json` exists."""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import onnx
import pytest
from onnx import TensorProto, helper, numpy_helper

from arco_edge.onnx_tools import (
    SplitError,
    SplitManifest,
    choose_cut,
    compare,
    compare_split,
    quantize_static_int8,
    run,
    session,
    single_cut_points,
    split_model,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
FP32_SPLIT_BOUND = 1e-4  # head->tail is the same arithmetic; only float reassociation differs
INT8_COSINE_BOUND = 0.99  # documented bound for a statically quantized head (see README)


def test_single_cut_points_respect_graph_structure(tiny_model: onnx.ModelProto) -> None:
    cuts = single_cut_points(tiny_model)
    tensors = [c.tensor for c in cuts]
    # after Relu0 and Relu1 exactly one tensor crosses; after Conv1 too (c1)
    assert {"r0", "r1", "c0", "c1"} <= set(tensors)
    # r1 is consumed by Conv2 AND Concat, so cutting after Conv2/Relu2 has two crossing tensors
    assert "c2" not in tensors and "r2" not in tensors
    # the concat output is a valid single cut again
    assert "cat" in tensors
    # never a cut before the image is consumed (index 0 would make the tail need the image)
    assert all(c.index >= 1 for c in cuts)


def test_choose_cut_by_index_and_tensor(tiny_model: onnx.ModelProto) -> None:
    cuts = single_cut_points(tiny_model)
    assert choose_cut(cuts, index=None, tensor="r1").tensor == "r1"
    assert choose_cut(cuts, index=0, tensor=None) == cuts[0]
    with pytest.raises(SplitError):
        choose_cut(cuts, index=99, tensor=None)
    with pytest.raises(SplitError):
        choose_cut(cuts, index=None, tensor="r2")
    with pytest.raises(SplitError):
        choose_cut([], index=None, tensor=None)


def test_tail_never_needs_the_image() -> None:
    """A graph whose last node reads the raw input has no safe cut at all."""
    w = numpy_helper.from_array(np.ones((3, 3, 1, 1), dtype=np.float32), "W")
    nodes = [
        helper.make_node("Conv", ["image", "W"], ["c"], kernel_shape=[1, 1]),
        helper.make_node("Add", ["c", "image"], ["out"]),  # residual on the raw image
    ]
    g = helper.make_graph(
        nodes,
        "leaky",
        [helper.make_tensor_value_info("image", TensorProto.FLOAT, [1, 3, 8, 8])],
        [helper.make_tensor_value_info("out", TensorProto.FLOAT, [1, 3, 8, 8])],
        initializer=[w],
    )
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 17)])
    assert single_cut_points(m) == []


@pytest.mark.parametrize("cut_tensor", ["r0", "r1", "cat"])
def test_split_matches_full_model_fp32(
    tiny_model: onnx.ModelProto, inputs: list[np.ndarray], tmp_path: Path, cut_tensor: str
) -> None:
    cut = choose_cut(single_cut_points(tiny_model), index=None, tensor=cut_tensor)
    head, tail = split_model(tiny_model, cut)
    assert [i.name for i in head.graph.input] == ["image"]
    assert [o.name for o in head.graph.output] == [cut_tensor]
    assert [i.name for i in tail.graph.input] == [cut_tensor]
    onnx.checker.check_model(head)
    onnx.checker.check_model(tail)
    full_s = session(tiny_model.SerializeToString())
    stats = compare_split(
        full_s, session(head.SerializeToString()), session(tail.SerializeToString()), inputs
    )
    worst = max(s.max_abs for s in stats)
    assert worst <= FP32_SPLIT_BOUND, f"split drift {worst}"
    assert all(s.cosine > 0.9999 for s in stats)


def test_quantized_head_within_documented_tolerance(
    tiny_model: onnx.ModelProto, inputs: list[np.ndarray], tmp_path: Path
) -> None:
    cut = choose_cut(single_cut_points(tiny_model), index=None, tensor="r1")
    head, tail = split_model(tiny_model, cut)
    head_p, tail_p = tmp_path / "head.onnx", tmp_path / "tail.onnx"
    onnx.save(head, str(head_p))
    onnx.save(tail, str(tail_p))
    calib = inputs[:4]
    int8_p = quantize_static_int8(head_p, tmp_path / "head_int8.onnx", calib)
    assert int8_p.exists()
    q = onnx.load(str(int8_p))
    ops = {n.op_type for n in q.graph.node}
    assert {"QuantizeLinear", "DequantizeLinear"} <= ops, "QDQ nodes missing: not quantized"

    full_s, tail_s = session(tiny_model.SerializeToString()), session(tail_p)
    fp32 = compare_split(full_s, session(head_p), tail_s, inputs[4:])
    int8 = compare_split(full_s, session(int8_p), tail_s, inputs[4:])
    worst_cos = min(s.cosine for s in int8)
    print(
        f"INT8 head: min cosine={worst_cos:.5f} max_abs={max(s.max_abs for s in int8):.4f} "
        f"(fp32 split max_abs={max(s.max_abs for s in fp32):.2e})"
    )
    assert worst_cos >= INT8_COSINE_BOUND
    # quantization must actually change something (a no-op pass would also "pass" tolerance)
    assert max(s.max_abs for s in int8) > max(s.max_abs for s in fp32)


def test_compare_rejects_shape_mismatch() -> None:
    with pytest.raises(ValueError):
        compare(np.zeros(3), np.zeros(4))
    c = compare(np.array([1.0, 2.0]), np.array([1.0, 2.0]))
    assert c.max_abs == 0.0 and c.cosine == pytest.approx(1.0)


# ----------------------------------------------------------------------------- real model


def _manifest_path() -> Path | None:
    models_dir = Path(os.environ.get("ARCO_MODELS_DIR", REPO_ROOT / "models"))
    model_id = os.environ.get("ARCO_MODEL_ID", "yolov8n-seg")
    p = models_dir / model_id / "manifest.json"
    return p if p.exists() else None


@pytest.mark.skipif(
    _manifest_path() is None,
    reason="no exported model (run scripts/export_split.py); tiny-model tests cover the split",
)
def test_exported_model_split_and_quant_tolerance() -> None:
    mp = _manifest_path()
    assert mp is not None
    m = SplitManifest.load(mp)
    d = mp.parent
    full_s = session(d / m.files["full_fp32"])
    head_s, tail_s = session(d / m.files["head_fp32"]), session(d / m.files["tail_fp32"])
    rng = np.random.default_rng(7)
    xs = [rng.random((1, 3, m.imgsz, m.imgsz), dtype=np.float32) for _ in range(2)]
    feat = run(head_s, {m.input_name: xs[0]})[0]
    assert list(feat.shape) == m.cut_shape
    fp32 = compare_split(full_s, head_s, tail_s, xs)
    assert max(s.max_abs for s in fp32) <= FP32_SPLIT_BOUND
    if "head_int8" in m.files:
        int8 = compare_split(full_s, session(d / m.files["head_int8"]), tail_s, xs)
        worst = min(s.cosine for s in int8)
        print(f"{m.model_id}: INT8 head min cosine={worst:.5f} manifest={json.dumps(m.tolerance)}")
        assert worst >= m.tolerance.get("int8_head_cosine_bound", INT8_COSINE_BOUND)
