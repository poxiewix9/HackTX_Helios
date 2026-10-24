"""Feature packet codec: cut tensor -> uint8 quantization -> zstd -> msgpack frame.

Wire frame = msgpack map `{"h": <FeaturePacketHeader JSON bytes>, "p": <compressed bytes>}`.
The header is the shared-schema model, so both ends validate it; the payload is the
quantized tensor. Nothing else is ever put in a frame — in particular no raw pixels.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import msgpack
import numpy as np
import zstandard as zstd

from arco_schemas import FeaturePacketHeader

ZSTD_LEVEL = 3  # fast; feature maps compress 2-4x at this level, higher levels cost ms


@dataclass(frozen=True)
class Quantized:
    q: np.ndarray  # uint8
    scale: float
    zero_point: int


def quantize_uint8(x: np.ndarray) -> Quantized:
    """Asymmetric per-tensor affine quantization: q = round(x/scale) + zp.

    Post-activation feature maps are mostly >= 0 with a small negative tail (SiLU), so an
    asymmetric uint8 grid uses the range better than symmetric int8. Error bound is
    scale/2 per element (tested)."""
    x = np.ascontiguousarray(x, dtype=np.float32)
    lo = float(x.min()) if x.size else 0.0
    hi = float(x.max()) if x.size else 0.0
    lo = min(lo, 0.0)
    hi = max(hi, 0.0)
    scale = (hi - lo) / 255.0
    if scale <= 0.0:  # constant tensor
        scale = 1.0
    zp = round(-lo / scale)
    zp = max(0, min(255, zp))
    q = np.clip(np.rint(x / scale) + zp, 0, 255).astype(np.uint8)
    return Quantized(q=q, scale=scale, zero_point=zp)


def dequantize(q: np.ndarray, scale: float, zero_point: int) -> np.ndarray:
    return (q.astype(np.float32) - np.float32(zero_point)) * np.float32(scale)


_cctx = zstd.ZstdCompressor(level=ZSTD_LEVEL)
_dctx = zstd.ZstdDecompressor()


@dataclass(frozen=True)
class Encoded:
    frame: bytes
    header: FeaturePacketHeader
    serialize_ms: float


def encode(
    tensor: np.ndarray,
    *,
    header_fields: dict[str, object],
) -> Encoded:
    """Quantize + compress `tensor`, fill the measured fields of the header, pack."""
    t0 = time.perf_counter()
    qz = quantize_uint8(tensor)
    payload = _cctx.compress(qz.q.tobytes())
    serialize_ms = (time.perf_counter() - t0) * 1000.0
    header = FeaturePacketHeader(
        **header_fields,  # type: ignore[arg-type]
        shape=[int(s) for s in tensor.shape],
        dtype="uint8",
        scale=qz.scale,
        zero_point=qz.zero_point,
        compression="zstd",
        payload_bytes=len(payload),
        serialize_ms=serialize_ms,
    )
    frame = msgpack.packb({"h": header.model_dump_json().encode(), "p": payload}, use_bin_type=True)
    return Encoded(frame=frame, header=header, serialize_ms=serialize_ms)


@dataclass(frozen=True)
class Decoded:
    header: FeaturePacketHeader
    tensor: np.ndarray  # float32, dequantized
    deserialize_ms: float
    frame_bytes: int


def decode(frame: bytes) -> Decoded:
    t0 = time.perf_counter()
    obj = msgpack.unpackb(frame, raw=False)
    header = FeaturePacketHeader.model_validate_json(obj["h"])
    payload: bytes = obj["p"]
    if header.payload_bytes != len(payload):
        raise ValueError("payload length mismatch")
    raw = _dctx.decompress(payload) if header.compression == "zstd" else payload
    dt = {"uint8": np.uint8, "int8": np.int8, "float16": np.float16, "float32": np.float32}[
        header.dtype
    ]
    arr = np.frombuffer(raw, dtype=dt).reshape(header.shape)
    tensor = (
        dequantize(arr, header.scale, header.zero_point)
        if header.dtype in ("uint8", "int8")
        else arr.astype(np.float32)
    )
    return Decoded(
        header=header,
        tensor=tensor,
        deserialize_ms=(time.perf_counter() - t0) * 1000.0,
        frame_bytes=len(frame),
    )
