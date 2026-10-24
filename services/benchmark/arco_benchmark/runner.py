"""Runs one pipeline over a frame stream and produces contract rows.

Pacing model (`pace_fps`): frames "arrive" at fixed instants `t0 + i / pace_fps`. The
pipeline is single-threaded with no input queue — like an edge node that grabs the latest
frame. Any frame whose arrival instant passed while the previous one was still being
processed is **dropped** (counted, never processed); if the pipeline finishes early it
sleeps until the next arrival. With `pace_fps=None` frames are processed back to back
and the FPS figure is pure throughput.
"""

from __future__ import annotations

import math
import statistics
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import psutil

from arco_benchmark.pipelines import FramePipeline
from arco_edge.sources import Frame
from arco_schemas import STAGES, BenchmarkResult, HardwareInfo, Stage, StreamKind, TelemetrySample

SampleSink = Callable[[TelemetrySample], None]


@dataclass
class RunStats:
    samples: list[TelemetrySample] = field(default_factory=list)
    dropped: int = 0
    wall_s: float = 0.0
    peak_rss_mb: float = 0.0
    detections: int = 0


def _rss_mb(proc: psutil.Process) -> float:
    return float(proc.memory_info().rss) / 2**20


def run_pipeline(
    pipeline: FramePipeline,
    frames: Iterable[Frame],
    *,
    stream: StreamKind,
    max_frames: int,
    pace_fps: float | None = None,
    warmup: int = 2,
    on_sample: SampleSink | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.perf_counter,
) -> RunStats:
    """`warmup` frames are processed but not recorded (session warm-up, allocator)."""
    proc = psutil.Process()
    proc.cpu_percent(interval=None)  # prime the counter
    stats = RunStats(peak_rss_mb=_rss_mb(proc))
    it: Iterator[Frame] = iter(frames)
    recorded = 0
    fps_window: list[float] = []
    start = clock()
    next_arrival = start
    index = 0
    # warm-up frames do not count towards arrivals either
    while recorded < max_frames:
        try:
            frame = next(it)
        except StopIteration:
            break
        if index >= warmup and pace_fps is not None:
            period = 1.0 / pace_fps
            now = clock()
            if now > next_arrival:
                # every arrival instant that passed while we were busy is a frame nobody
                # grabbed: dropped. This frame stands in for the next arrival >= now.
                missed = math.ceil((now - next_arrival) / period)
                stats.dropped += missed
                next_arrival += missed * period
            if next_arrival > now:
                sleep(next_arrival - now)
            next_arrival += period
        t_frame = clock()
        res = pipeline.process(frame)
        done = clock()
        if index < warmup:
            index += 1
            start = clock()  # measured window starts after warm-up
            next_arrival = start
            continue
        index += 1
        recorded += 1
        fps_window.append(done)
        fps_window = [t for t in fps_window if done - t <= 2.0]
        span = fps_window[-1] - fps_window[0] if len(fps_window) > 1 else 0.0
        fps = (len(fps_window) - 1) / span if span > 0 else 0.0
        stages: dict[Stage, float] = {s: round(res.stage_ms.get(s, 0.0), 3) for s in STAGES}
        rss = _rss_mb(proc)
        stats.peak_rss_mb = max(stats.peak_rss_mb, rss)
        stats.detections += res.detections
        sample = TelemetrySample(
            mode="live",
            pipeline=pipeline.kind,
            stream=stream,
            frame_id=frame.frame_id,
            scenario_t=frame.scenario_t,
            wall_t=datetime.now(UTC),
            stage_ms=stages,
            total_ms=round((done - t_frame) * 1000.0, 3),
            fps=round(fps, 2),
            bytes_sent=res.bytes_sent,
            dropped_frames=stats.dropped,
            cpu_percent=round(proc.cpu_percent(interval=None), 1),
            rss_mb=round(rss, 1),
        )
        stats.samples.append(sample)
        if on_sample is not None:
            on_sample(sample)
    stats.wall_s = clock() - start
    return stats


def _percentile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    ordered = sorted(xs)
    k = (len(ordered) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def summarize(
    stats: RunStats,
    *,
    pipeline: FramePipeline,
    stream: StreamKind,
    run_id: str,
    started_at: datetime,
    hardware: HardwareInfo,
    target_ms: float = 80.0,
    target_fps: float = 30.0,
    notes: str | None = None,
) -> BenchmarkResult:
    """Aggregate measured samples into one contract row. `meets_target` means the mean
    frame time is under `target_ms` AND throughput reached `target_fps`; when it is
    missed the slowest mean stage is named, never hidden."""
    totals = [s.total_ms for s in stats.samples]
    n = len(totals)
    mean_stage: dict[Stage, float] = {
        s: round(statistics.fmean([x.stage_ms[s] for x in stats.samples]), 3) if n else 0.0
        for s in STAGES
    }
    fps = n / stats.wall_s if stats.wall_s > 0 else 0.0
    mean_ms = statistics.fmean(totals) if n else 0.0
    meets = n > 0 and mean_ms <= target_ms and fps >= target_fps
    bottleneck: Stage | None = None
    if not meets and n:
        slowest: Stage = max(STAGES, key=lambda stage: mean_stage[stage])
        bottleneck = slowest
    return BenchmarkResult(
        mode="live",
        pipeline=pipeline.kind,
        stream=stream,
        run_id=run_id,
        started_at=started_at,
        hardware=hardware,
        frames=n,
        dropped_frames=stats.dropped,
        mean_ms=round(mean_ms, 3),
        p50_ms=round(_percentile(totals, 0.5), 3),
        p95_ms=round(_percentile(totals, 0.95), 3),
        fps=round(fps, 2),
        mean_stage_ms=mean_stage,
        total_bytes_sent=sum(s.bytes_sent for s in stats.samples),
        peak_rss_mb=round(stats.peak_rss_mb, 1),
        target_ms=target_ms,
        target_fps=target_fps,
        meets_target=meets,
        bottleneck_stage=bottleneck,
        notes=notes,
    )


def compare(a: BenchmarkResult, b: BenchmarkResult) -> dict[str, Any]:
    """Plain-language comparison of two results on the same stream. Ratios > 1 favour `b`."""
    return {
        "stream": a.stream,
        "a": a.pipeline,
        "b": b.pipeline,
        "mean_ms_a": a.mean_ms,
        "mean_ms_b": b.mean_ms,
        "speedup_mean": round(a.mean_ms / b.mean_ms, 3) if b.mean_ms else None,
        "fps_a": a.fps,
        "fps_b": b.fps,
        "bytes_per_frame_a": round(a.total_bytes_sent / a.frames) if a.frames else 0,
        "bytes_per_frame_b": round(b.total_bytes_sent / b.frames) if b.frames else 0,
        "bandwidth_ratio": (
            round(a.total_bytes_sent / b.total_bytes_sent, 2) if b.total_bytes_sent else None
        ),
        "dropped_a": a.dropped_frames,
        "dropped_b": b.dropped_frames,
        "b_faster": b.mean_ms < a.mean_ms,
        "b_meets_target": b.meets_target,
        "b_bottleneck": b.bottleneck_stage,
    }
