from __future__ import annotations

from pathlib import Path

import numpy as np
import onnx
import pytest
from onnx import TensorProto, helper, numpy_helper

REPO_ROOT = Path(__file__).resolve().parents[3]
GEOREF_DIR = REPO_ROOT / "data" / "samples" / "vision" / "georef"


def tiny_convnet(seed: int = 0, size: int = 64) -> onnx.ModelProto:
    """A small conv net with the structures that matter for splitting:

        image -> Conv0 -> Relu0 -> Conv1 -> Relu1 -+-> Conv2 -> Relu2 -> Concat(Relu1, Relu2)
                                                   |                          |
                                                   +--------------------------+ -> Conv3 -> out

    Relu1 feeds both Conv2 and the Concat, so cutting right after Conv2/Relu2 is NOT a
    single-tensor cut (two tensors cross), while cutting after Relu0 or Relu1 is. Weights
    are deterministic random so outputs are non-trivial."""
    rng = np.random.default_rng(seed)

    def w(name: str, cout: int, cin: int) -> onnx.TensorProto:
        return numpy_helper.from_array(
            (rng.standard_normal((cout, cin, 3, 3)) * 0.2).astype(np.float32), name
        )

    def b(name: str, c: int) -> onnx.TensorProto:
        return numpy_helper.from_array((rng.standard_normal(c) * 0.1).astype(np.float32), name)

    inits = [
        w("W0", 8, 3),
        b("B0", 8),
        w("W1", 16, 8),
        b("B1", 16),
        w("W2", 16, 16),
        b("B2", 16),
        w("W3", 4, 32),
        b("B3", 4),
    ]
    k3, p1 = [3, 3], [1, 1, 1, 1]

    def conv(ins: list[str], out: str, stride: int = 1) -> onnx.NodeProto:
        return helper.make_node(
            "Conv", ins, [out], kernel_shape=k3, pads=p1, strides=[stride, stride]
        )

    nodes = [
        conv(["image", "W0", "B0"], "c0", stride=2),
        helper.make_node("Relu", ["c0"], ["r0"]),
        conv(["r0", "W1", "B1"], "c1", stride=2),
        helper.make_node("Relu", ["c1"], ["r1"]),
        conv(["r1", "W2", "B2"], "c2"),
        helper.make_node("Relu", ["c2"], ["r2"]),
        helper.make_node("Concat", ["r1", "r2"], ["cat"], axis=1),
        conv(["cat", "W3", "B3"], "out"),
    ]
    graph = helper.make_graph(
        nodes,
        "tiny",
        [helper.make_tensor_value_info("image", TensorProto.FLOAT, [1, 3, size, size])],
        [helper.make_tensor_value_info("out", TensorProto.FLOAT, [1, 4, size // 4, size // 4])],
        initializer=inits,
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 9
    onnx.checker.check_model(model)
    return model


@pytest.fixture(scope="session")
def tiny_model() -> onnx.ModelProto:
    return tiny_convnet()


@pytest.fixture(scope="session")
def tiny_model_path(tmp_path_factory: pytest.TempPathFactory, tiny_model: onnx.ModelProto) -> Path:
    p = tmp_path_factory.mktemp("models") / "tiny_full.onnx"
    onnx.save(tiny_model, str(p))
    return p


@pytest.fixture(scope="session")
def inputs() -> list[np.ndarray]:
    rng = np.random.default_rng(1)
    return [rng.random((1, 3, 64, 64), dtype=np.float32) for _ in range(6)]
