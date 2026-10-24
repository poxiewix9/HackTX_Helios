"""Vision pipeline contracts: edge -> base station feature packets, detections, georef.

The feature tensor itself travels as a binary msgpack frame; `FeaturePacketHeader`
is the typed header that accompanies it. Raw frames are never part of any contract
that crosses the local network boundary.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from arco_schemas.geo import BBox, Position
from arco_schemas.hazard_classes import HazardClass, StreamKind

DetectorKind = Literal["onnx_split", "onnx_full", "synthetic"]
"""Which detector produced the output. `synthetic` is the scripted fallback detector
used when models are absent; the dashboard must label it."""

Compression = Literal["none", "zstd", "lz4"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CameraGeoref(_Model):
    """Street camera pose + ground-plane homography calibration.

    `image_points` (pixels) and `world_points` (lon/lat) are >= 4 correspondences on
    the ground plane used to solve the 3x3 homography H so that
    `[lon, lat, 1]^T ~ H * [u, v, 1]^T`. The base station solves H once per camera.
    """

    kind: Literal["camera"] = "camera"
    camera_id: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    heading_deg: float = Field(ge=0, lt=360, description="Compass heading of optical axis")
    fov_deg: float = Field(gt=0, lt=180)
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    image_points: Annotated[list[list[float]], Field(min_length=4)]
    world_points: Annotated[list[Position], Field(min_length=4)]


class TileGeoref(_Model):
    """Satellite/aerial tile placement: affine pixel -> lon/lat.

    `affine` is the 6-element GDAL-style transform `[a, b, c, d, e, f]` such that
    `lon = a*col + b*row + c` and `lat = d*col + e*row + f`.
    """

    kind: Literal["tile"] = "tile"
    tile_id: str
    bounds: BBox
    affine: Annotated[list[float], Field(min_length=6, max_length=6)]
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)


Georef = Annotated[CameraGeoref | TileGeoref, Field(discriminator="kind")]


class FeaturePacketHeader(_Model):
    """Header for one intermediate-activation tensor streamed from edge to base."""

    stream: StreamKind
    frame_id: int = Field(ge=0)
    source_id: str = Field(description="camera_id or tile_id")
    captured_wall_t: float = Field(description="Unix seconds at capture")
    scenario_t: float | None = Field(default=None, description="Scenario clock, if replaying")
    model_id: str = Field(description="e.g. yolov8n-seg@split6")
    split_layer: int = Field(ge=0)
    shape: list[int] = Field(min_length=1, description="Tensor shape, NCHW")
    dtype: Literal["int8", "uint8", "float16", "float32"]
    scale: float = Field(gt=0, description="Dequant: x = scale * (q - zero_point)")
    zero_point: int
    compression: Compression
    payload_bytes: int = Field(ge=0, description="Compressed byte length that follows")
    head_ms: float = Field(ge=0, description="Measured edge head inference time")
    serialize_ms: float = Field(ge=0, description="Measured quantize+compress time")
    georef: Georef


class Detection(_Model):
    """One detection/segment in image space plus its georeferenced footprint."""

    hazard_class: HazardClass
    confidence: float = Field(ge=0.0, le=1.0)
    bbox_px: Annotated[list[float], Field(min_length=4, max_length=4)] = Field(
        description="[x_min, y_min, x_max, y_max] in pixels"
    )
    mask_polygon_px: list[list[float]] | None = Field(
        default=None, description="Simplified mask contour in pixels, [[u, v], ...]"
    )
    footprint: list[Position] | None = Field(
        default=None, description="Georeferenced ring (lon/lat); None if georef failed"
    )
