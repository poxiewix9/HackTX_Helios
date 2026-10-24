"""Recorded-run replay: re-emit a stored run's samples as `mode="recorded"`, paced by the
original wall-clock gaps (divided by `speed`). The hardware block travels with every
result so nobody can mistake a replay for this machine's numbers."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

from arco_benchmark.report import BenchmarkRun
from arco_schemas import BenchmarkResult, TelemetryMessage, TelemetrySample


def recorded_results(run: BenchmarkRun) -> list[BenchmarkResult]:
    return [r.model_copy(update={"mode": "recorded"}) for r in run.results]


def recorded_samples(run: BenchmarkRun) -> list[TelemetrySample]:
    return [s.model_copy(update={"mode": "recorded"}) for s in run.samples]


def iter_replay(
    run: BenchmarkRun,
    *,
    speed: float = 1.0,
    loop: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    stop: Callable[[], bool] | None = None,
) -> Iterator[TelemetryMessage]:
    """Yield `TelemetryMessage`s whose `sample.mode == "recorded"`. Samples of different
    pipelines/streams are interleaved by their original `wall_t` so a dashboard sees the
    FP32 and split traces side by side the way they were measured."""
    if speed <= 0:
        msg = "speed must be > 0"
        raise ValueError(msg)
    ordered = sorted(recorded_samples(run), key=lambda s: s.wall_t)
    if not ordered:
        return
    seq = 0
    while True:
        prev = ordered[0].wall_t
        for s in ordered:
            if stop is not None and stop():
                return
            gap = (s.wall_t - prev).total_seconds() / speed
            if gap > 0:
                sleep(gap)
            prev = s.wall_t
            seq += 1
            yield TelemetryMessage(
                scenario_t=s.scenario_t if s.scenario_t is not None else -1.0,
                wall_t=datetime.now(UTC),
                seq=seq,
                sample=s,
            )
        if not loop:
            return
