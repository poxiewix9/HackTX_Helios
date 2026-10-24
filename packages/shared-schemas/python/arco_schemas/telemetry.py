"""Telemetry and benchmark contracts.

Every sample carries `mode`: `live` means the numbers were measured in this process
right now; `recorded` means they are being replayed from a stored run. The dashboard
renders a LIVE / RECORDED badge from this field and must never hide it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from arco_schemas.hazard_classes import StreamKind

TelemetryMode = Literal["live", "recorded"]
PipelineKind = Literal["fp32_full", "int8_split", "synthetic"]
"""`synthetic` = the scripted fallback detector ran through the split topology; its timings
are real but say nothing about a model."""

Stage = Literal[
    "capture",
    "head",
    "serialize",
    "network",
    "deserialize",
    "tail",
    "postprocess",
    "georef",
    "db_write",
]
STAGES: tuple[Stage, ...] = (
    "capture",
    "head",
    "serialize",
    "network",
    "deserialize",
    "tail",
    "postprocess",
    "georef",
    "db_write",
)


class HardwareInfo(BaseModel):
    """Read from the machine at runtime; never typed in by hand."""

    model_config = ConfigDict(extra="forbid")

    hostname: str
    platform: str
    cpu: str
    cpu_count: int = Field(ge=1)
    memory_gb: float = Field(ge=0)
    gpu: str | None = None
    onnxruntime_providers: list[str] = Field(default_factory=list)
    python: str


class TelemetrySample(BaseModel):
    """Per-frame timing for one pipeline on one stream."""

    model_config = ConfigDict(extra="forbid")

    mode: TelemetryMode
    pipeline: PipelineKind
    stream: StreamKind
    frame_id: int = Field(ge=0)
    scenario_t: float | None = None
    wall_t: datetime
    stage_ms: dict[Stage, float] = Field(description="Measured per-stage latency")
    total_ms: float = Field(ge=0)
    fps: float = Field(ge=0, description="Rolling throughput")
    bytes_sent: int = Field(ge=0, description="Bytes over the edge link for this frame")
    dropped_frames: int = Field(ge=0, description="Cumulative drops in this run")
    cpu_percent: float | None = Field(default=None, ge=0)
    gpu_percent: float | None = Field(default=None, ge=0)
    rss_mb: float | None = Field(default=None, ge=0)


class BenchmarkResult(BaseModel):
    """Aggregate for one pipeline over a benchmark run."""

    model_config = ConfigDict(extra="forbid")

    mode: TelemetryMode
    pipeline: PipelineKind
    stream: StreamKind | None = Field(
        default=None, description="Stream the run was measured on; None = aggregated"
    )
    run_id: str
    started_at: datetime
    hardware: HardwareInfo
    frames: int = Field(ge=0)
    dropped_frames: int = Field(ge=0)
    mean_ms: float = Field(ge=0)
    p50_ms: float = Field(ge=0)
    p95_ms: float = Field(ge=0)
    fps: float = Field(ge=0)
    mean_stage_ms: dict[Stage, float]
    total_bytes_sent: int = Field(ge=0)
    peak_rss_mb: float | None = Field(default=None, ge=0)
    target_ms: float = Field(default=80.0, gt=0)
    target_fps: float = Field(default=30.0, gt=0)
    meets_target: bool
    bottleneck_stage: Stage | None = Field(
        default=None, description="Slowest stage when the target is missed"
    )
    notes: str | None = None
