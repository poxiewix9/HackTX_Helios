"""Per-frame, per-stage telemetry. Every number here is measured in this process or
carried in the packet header from the edge (head_ms, serialize_ms); nothing is estimated.

`network` = base receive time - edge capture time - (capture + head + serialize). It
relies on both clocks being NTP-synced to the same wall time; on one machine (demo,
tests) that is exact. Negative values (clock skew) are clamped to 0 and counted."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime

import psutil
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from arco_schemas import TelemetrySample
from arco_schemas.hazard_classes import StreamKind
from arco_schemas.telemetry import STAGES, PipelineKind, Stage

_BUCKETS = (1, 2, 5, 10, 20, 40, 60, 80, 120, 200, 500, 1000)


@dataclass
class _StreamState:
    arrivals: deque[float]
    frames: int = 0
    dropped: int = 0
    last_frame_id: int | None = None
    skew_clamped: int = 0


@dataclass
class TelemetryHub:
    window: int = 30
    registry: CollectorRegistry = field(default_factory=CollectorRegistry)
    _streams: dict[str, _StreamState] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.stage_hist = Histogram(
            "arco_stage_ms",
            "Per-stage latency (ms)",
            ["stage", "stream", "pipeline"],
            buckets=_BUCKETS,
            registry=self.registry,
        )
        self.total_hist = Histogram(
            "arco_frame_total_ms",
            "End-to-end latency per frame (ms)",
            ["stream", "pipeline"],
            buckets=_BUCKETS,
            registry=self.registry,
        )
        self.frames_total = Counter(
            "arco_frames_total", "Frames processed", ["stream", "pipeline"], registry=self.registry
        )
        self.dropped_total = Counter(
            "arco_frames_dropped_total", "Frames dropped", ["stream"], registry=self.registry
        )
        self.bytes_total = Counter(
            "arco_edge_bytes_total",
            "Bytes received over the edge link",
            ["stream"],
            registry=self.registry,
        )
        self.fps_gauge = Gauge("arco_fps", "Rolling FPS", ["stream"], registry=self.registry)
        self._proc = psutil.Process()
        self._proc.cpu_percent(None)  # prime

    def _state(self, key: str) -> _StreamState:
        if key not in self._streams:
            self._streams[key] = _StreamState(arrivals=deque(maxlen=self.window))
        return self._streams[key]

    def record(
        self,
        *,
        stream: StreamKind,
        pipeline: PipelineKind,
        frame_id: int,
        scenario_t: float | None,
        captured_wall_t: float,
        received_wall_t: float,
        stage_ms: dict[str, float],
        bytes_received: int,
        dropped_at_edge: int = 0,
    ) -> TelemetrySample:
        key = f"{stream}"
        st = self._state(key)
        st.frames += 1
        st.arrivals.append(received_wall_t)
        # frame_id gaps = frames dropped by the edge before they reached us
        if st.last_frame_id is not None and frame_id > st.last_frame_id + 1:
            gap = frame_id - st.last_frame_id - 1
            st.dropped += gap
            self.dropped_total.labels(stream).inc(gap)
        st.last_frame_id = max(frame_id, st.last_frame_id or 0)
        stages: dict[Stage, float] = {s: 0.0 for s in STAGES}
        for k, v in stage_ms.items():
            if k in stages:
                stages[k] = float(v)
        edge_side = stages["capture"] + stages["head"] + stages["serialize"]
        network = (received_wall_t - captured_wall_t) * 1000.0 - edge_side
        if network < 0:
            st.skew_clamped += 1
            network = 0.0
        stages["network"] = network
        total = sum(stages.values())
        fps = 0.0
        if len(st.arrivals) >= 2:
            span = st.arrivals[-1] - st.arrivals[0]
            fps = (len(st.arrivals) - 1) / span if span > 0 else 0.0
        for s, v in stages.items():
            self.stage_hist.labels(s, stream, pipeline).observe(v)
        self.total_hist.labels(stream, pipeline).observe(total)
        self.frames_total.labels(stream, pipeline).inc()
        self.bytes_total.labels(stream).inc(bytes_received)
        self.fps_gauge.labels(stream).set(fps)
        mem = self._proc.memory_info().rss / (1024 * 1024)
        return TelemetrySample(
            mode="live",
            pipeline=pipeline,
            stream=stream,
            frame_id=frame_id,
            scenario_t=scenario_t,
            wall_t=datetime.now(UTC),
            stage_ms=stages,
            total_ms=total,
            fps=fps,
            bytes_sent=bytes_received,
            dropped_frames=st.dropped + dropped_at_edge,
            cpu_percent=float(self._proc.cpu_percent(None)),
            gpu_percent=None,
            rss_mb=float(mem),
        )

    def summary(self) -> dict[str, dict[str, float | int]]:
        return {
            k: {"frames": s.frames, "dropped": s.dropped, "skew_clamped": s.skew_clamped}
            for k, s in self._streams.items()
        }


class StageTimer:
    """`with timer("tail"): ...` accumulates measured ms per stage."""

    def __init__(self) -> None:
        self.ms: dict[str, float] = {}
        self._stage = ""
        self._t0 = 0.0

    def __call__(self, stage: str) -> StageTimer:
        self._stage = stage
        return self

    def __enter__(self) -> StageTimer:
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self.ms[self._stage] = (
            self.ms.get(self._stage, 0.0) + (time.perf_counter() - self._t0) * 1e3
        )
