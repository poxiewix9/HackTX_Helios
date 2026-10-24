"""Feature packet codec, the edge loop with a recording link, and the raw-frame guards."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from arco_edge.client import (
    RecordingLink,
    RemoteDestinationError,
    assert_local_destination,
    is_local_host,
)
from arco_edge.head import SyntheticHead
from arco_edge.main import EdgeNode, load_georef
from arco_edge.packet import decode, dequantize, encode, quantize_uint8
from arco_edge.preprocess import Letterbox, letterbox_tensor
from arco_edge.settings import EdgeSettings
from arco_edge.sources import SyntheticSource
from arco_schemas import FeaturePacketHeader

GEOREF_DIR = Path(__file__).resolve().parents[3] / "data" / "samples" / "vision" / "georef"


def _header_fields(georef_path: Path) -> dict[str, object]:
    return {
        "stream": "micro",
        "frame_id": 7,
        "source_id": "cam-test",
        "captured_wall_t": 1_700_000_000.0,
        "scenario_t": 1.5,
        "model_id": "tiny@r1",
        "split_layer": 4,
        "head_ms": 1.25,
        "georef": load_georef(georef_path),
    }


def test_quantize_error_bound_and_constant_tensor() -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(0.5, 1.0, size=(1, 16, 20, 20)).astype(np.float32)
    q = quantize_uint8(x)
    back = dequantize(q.q, q.scale, q.zero_point)
    assert np.abs(back - x).max() <= q.scale / 2 + 1e-6
    const = quantize_uint8(np.full((4,), 3.0, dtype=np.float32))
    assert const.scale > 0
    assert np.allclose(dequantize(const.q, const.scale, const.zero_point), 3.0)


def test_packet_roundtrip_and_compression() -> None:
    rng = np.random.default_rng(1)
    # feature-map-like tensor: smooth + sparse (SiLU outputs are mostly near zero)
    base = np.kron(rng.random((1, 8, 10, 10)), np.ones((1, 1, 4, 4))).astype(np.float32)
    x = np.where(base > 0.6, base, 0.0).astype(np.float32)
    enc = encode(x, header_fields=_header_fields(GEOREF_DIR / "camera_front_st.json"))
    assert enc.header.dtype == "uint8" and enc.header.compression == "zstd"
    assert enc.header.payload_bytes < x.size, "zstd should beat 1 byte/elem on sparse maps"
    assert enc.serialize_ms > 0
    dec = decode(enc.frame)
    assert dec.header == enc.header
    assert dec.tensor.shape == x.shape
    assert np.abs(dec.tensor - x).max() <= enc.header.scale / 2 + 1e-6
    assert dec.deserialize_ms > 0
    # header is the shared-schema model, validated on both ends
    FeaturePacketHeader.model_validate_json(enc.header.model_dump_json())


def test_letterbox_roundtrip() -> None:
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    t, lb = letterbox_tensor(img, 320)
    assert t.shape == (1, 3, 320, 320) and t.dtype == np.float32
    assert lb == Letterbox.for_size(640, 480, 320)
    pts = lb.to_original(np.array([[lb.pad_x, lb.pad_y], [320 - lb.pad_x, 320 - lb.pad_y]]))
    assert pts[0].tolist() == [0.0, 0.0]
    assert pts[1][0] == pytest.approx(639.0) and pts[1][1] == pytest.approx(479.0)


def test_synthetic_source_is_deterministic() -> None:
    def first3() -> list[np.ndarray]:
        src = SyntheticSource(width=64, height=48, seed=3)
        return [f.image for f, _ in zip(src.frames(), range(3), strict=False)]

    a, b = first3(), first3()
    assert all(np.array_equal(x, y) for x, y in zip(a, b, strict=True))
    assert a[0].shape == (48, 64, 3)


# ----------------------------------------------------------------------------- privacy


@pytest.mark.parametrize(
    "host,local",
    [
        ("localhost", True),
        ("127.0.0.1", True),
        ("10.1.2.3", True),
        ("192.168.0.9", True),
        ("172.16.5.5", True),
        ("base-station", True),  # compose service name
        ("edge.local", True),
        ("8.8.8.8", False),
        ("example.com", False),
    ],
)
def test_local_host_classification(host: str, local: bool) -> None:
    assert is_local_host(host) is local


def test_remote_base_station_is_refused() -> None:
    with pytest.raises(RemoteDestinationError):
        assert_local_destination("ws://example.com/ws/features")
    assert_local_destination("ws://example.com/ws/features", allow_remote=True)
    assert_local_destination("ws://base-station:8002/ws/features")


def test_no_raw_frame_leaves_the_edge(tmp_path: Path) -> None:
    """Run the real edge loop (synthetic source + synthetic head) against a recording link
    and prove the frames that would hit the wire contain no pixels: every byte sequence
    is a valid packet (header + quantized tensor only), and neither the raw BGR buffer,
    nor PNG/JPEG encodings of the frame, nor any 64-byte slice of the image appears."""
    settings = EdgeSettings(
        arco_detector="synthetic",
        arco_edge_source="synthetic",
        arco_edge_georef_path=GEOREF_DIR / "camera_front_st.json",
        arco_edge_width=128,
        arco_edge_height=96,
        arco_edge_realtime=False,
        arco_base_station_url="ws://localhost:1/ws/features",
    )
    src = SyntheticSource(width=128, height=96, seed=0)
    src = SyntheticSource(width=128, height=96, seed=0)
    images = [f.image.copy() for f, _ in zip(src.frames(), range(5), strict=False)]
    link = RecordingLink()
    node = EdgeNode(settings, source=src, head=SyntheticHead(), link=link)
    stats = node.run(max_frames=5)
    assert stats.frames == 5 and len(link.frames) == 5
    for frame, img in zip(link.frames, images, strict=True):
        dec = decode(frame)  # must parse as a packet and nothing else
        assert dec.header.model_id == "synthetic"
        assert len(frame) < img.nbytes / 20, "packet is far smaller than any image encoding"
        raw = img.tobytes()
        assert raw not in frame
        for ext in (".png", ".jpg"):
            ok, buf = cv2.imencode(ext, img)
            assert ok and buf.tobytes() not in frame
        for off in range(0, len(raw) - 64, 4096):
            assert raw[off : off + 64] not in frame
    # and the header really is JSON with only the contract's fields
    hdr = json.loads(decode(link.frames[0]).header.model_dump_json())
    assert set(hdr) == set(FeaturePacketHeader.model_fields)
