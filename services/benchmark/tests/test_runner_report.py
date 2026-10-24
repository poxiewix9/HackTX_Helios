"""Runner pacing/drop semantics, aggregation, report files, recorded replay labelling.
All with a fake clock so the assertions are exact and the suite is fast; the real-model
run lives in `test_real_bench.py`."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from arco_benchmark.hardware import read_hardware
from arco_benchmark.pipelines import StageResult
from arco_benchmark.replay import iter_replay, recorded_results
from arco_benchmark.report import BenchmarkRun, format_table, load_run, write_run
from arco_benchmark.runner import compare, run_pipeline, summarize
from arco_edge.sources import Frame
from arco_schemas import STAGES, BenchmarkResult, PipelineKind, TelemetrySample


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


class FakePipeline:
    """Costs exactly `cost_ms` of fake time per frame."""

    def __init__(self, clock: FakeClock, cost_ms: float, kind: PipelineKind = "int8_split") -> None:
        self.model_id = "fake"
        self.providers = ["CPUExecutionProvider"]
        self.clock = clock
        self.cost_ms = cost_ms
        self.kind = kind
        self.calls = 0

    def process(self, frame: Frame) -> StageResult:
        self.calls += 1
        self.clock.t += self.cost_ms / 1000.0
        return StageResult({"tail": self.cost_ms * 0.8, "head": self.cost_ms * 0.2}, 1000, 1)


def _frames(n: int) -> list[Frame]:
    img = np.zeros((4, 4, 3), dtype=np.uint8)
    return [Frame(img, i, 1000.0 + i / 30, None, 0.05) for i in range(n)]


def test_unpaced_run_has_no_drops_and_throughput_fps() -> None:
    clock = FakeClock()
    pipe = FakePipeline(clock, cost_ms=50.0)
    stats = run_pipeline(
        pipe,
        _frames(100),
        stream="micro",
        max_frames=10,
        pace_fps=None,
        warmup=2,
        sleep=clock.sleep,
        clock=clock.now,
    )
    assert pipe.calls == 12  # 2 warm-up + 10 recorded
    assert len(stats.samples) == 10 and stats.dropped == 0
    assert stats.wall_s == pytest.approx(0.5, abs=1e-6)
    assert all(s.mode == "live" and s.pipeline == "int8_split" for s in stats.samples)
    assert all(set(s.stage_ms) == set(STAGES) for s in stats.samples)
    assert stats.samples[0].total_ms == pytest.approx(50.0, abs=1e-3)
    assert stats.samples[0].frame_id == 2  # warm-up frames are not recorded


def test_paced_run_drops_arrivals_missed_while_busy() -> None:
    # 30 fps input (33.3 ms period), 70 ms per frame, no input queue:
    # each processed frame spans 3 periods -> 2 arrivals dropped per processed frame
    clock = FakeClock()
    pipe = FakePipeline(clock, cost_ms=70.0)
    stats = run_pipeline(
        pipe,
        _frames(1000),
        stream="micro",
        max_frames=10,
        pace_fps=30.0,
        warmup=1,
        sleep=clock.sleep,
        clock=clock.now,
    )
    assert len(stats.samples) == 10
    assert stats.dropped == 2 * 9  # the last processed frame has no successor to drop
    assert stats.samples[-1].dropped_frames == 18
    # the pipeline waited for the next slot instead of starting immediately
    assert all(s >= 0 for s in clock.slept) and sum(clock.slept) > 0
    assert stats.samples[-1].fps == pytest.approx(10.0, rel=0.05)  # 1 frame / 3 periods


def test_paced_fast_pipeline_sleeps_and_drops_nothing() -> None:
    clock = FakeClock()
    pipe = FakePipeline(clock, cost_ms=10.0)
    stats = run_pipeline(
        pipe,
        _frames(100),
        stream="macro",
        max_frames=5,
        pace_fps=30.0,
        warmup=0,
        sleep=clock.sleep,
        clock=clock.now,
    )
    assert stats.dropped == 0
    assert sum(clock.slept) == pytest.approx(4 * (1 / 30 - 0.010), abs=1e-6)


def _result(kind: PipelineKind, cost_ms: float, target_ms: float = 80.0) -> BenchmarkResult:
    clock = FakeClock()
    pipe = FakePipeline(clock, cost_ms=cost_ms, kind=kind)
    stats = run_pipeline(
        pipe,
        _frames(50),
        stream="micro",
        max_frames=20,
        pace_fps=None,
        warmup=0,
        sleep=clock.sleep,
        clock=clock.now,
    )
    return summarize(
        stats,
        pipeline=pipe,
        stream="micro",
        run_id="t",
        started_at=datetime.now(UTC),
        hardware=read_hardware(["CPUExecutionProvider"]),
        target_ms=target_ms,
        target_fps=30.0,
    )


def test_summarize_names_the_bottleneck_when_target_missed() -> None:
    slow = _result("fp32_full", 100.0)
    assert slow.frames == 20 and slow.mean_ms == pytest.approx(100.0, abs=1e-3)
    assert slow.p50_ms == pytest.approx(100.0, abs=1e-3)
    assert slow.p95_ms == pytest.approx(100.0, abs=1e-3)
    assert slow.fps == pytest.approx(10.0, rel=1e-3)
    assert slow.meets_target is False and slow.bottleneck_stage == "tail"
    assert slow.mean_stage_ms["tail"] == pytest.approx(80.0, abs=1e-3)
    assert slow.total_bytes_sent == 20 * 1000 and slow.stream == "micro"
    fast = _result("int8_split", 20.0)
    assert fast.meets_target is True and fast.bottleneck_stage is None  # 50 fps, 20 ms
    assert fast.hardware.cpu and fast.hardware.cpu_count >= 1 and fast.hardware.memory_gb > 0
    assert fast.hardware.onnxruntime_providers == ["CPUExecutionProvider"]
    assert fast.hardware.gpu is None  # CPU-only providers never claim a GPU
    c = compare(slow, fast)
    assert c["b_faster"] is True and c["speedup_mean"] == pytest.approx(5.0, rel=1e-3)
    assert c["bandwidth_ratio"] == 1.0 and c["b_meets_target"] is True


def _run_file(tmp_path: Path) -> tuple[BenchmarkRun, Path]:
    a, b = _result("fp32_full", 90.0), _result("int8_split", 30.0)
    t0 = datetime.now(UTC)
    samples: list[TelemetrySample] = []
    for i in range(6):
        kind: PipelineKind = "fp32_full" if i % 2 == 0 else "int8_split"
        samples.append(
            TelemetrySample(
                mode="live",
                pipeline=kind,
                stream="micro",
                frame_id=i,
                wall_t=t0 + timedelta(milliseconds=100 * i),
                stage_ms=dict.fromkeys(STAGES, 1.0),
                total_ms=9.0,
                fps=10.0,
                bytes_sent=5,
                dropped_frames=0,
            )
        )
    run = BenchmarkRun(
        run_id="unit",
        started_at=t0,
        finished_at=t0,
        hardware=a.hardware,
        model_id="fake",
        frames_source="synthetic:0",
        pace_fps=None,
        results=[a, b],
        comparisons=[compare(a, b)],
        samples=samples,
        notes=["unit test run"],
    )
    paths = write_run(run, tmp_path / "unit")
    return run, paths["run"]


def test_report_files_roundtrip_and_csv_columns(tmp_path: Path) -> None:
    run, run_path = _run_file(tmp_path)
    loaded = load_run(run_path)
    assert loaded.results[0].hardware == run.hardware
    assert [r.pipeline for r in loaded.results] == ["fp32_full", "int8_split"]
    with (tmp_path / "unit" / "summary.csv").open() as fh:
        rows = list(csv.DictReader(fh))
    assert [r["pipeline"] for r in rows] == ["fp32_full", "int8_split"]
    assert rows[0]["meets_target"] == "False" and rows[0]["bottleneck_stage"] == "tail"
    assert rows[1]["meets_target"] == "True" and rows[1]["bottleneck_stage"] == ""
    assert rows[0]["cpu"] == run.hardware.cpu
    assert float(rows[0]["mean_tail_ms"]) == pytest.approx(72.0, abs=1e-3)
    with (tmp_path / "unit" / "samples.csv").open() as fh:
        srows = list(csv.DictReader(fh))
    assert len(srows) == 6 and set(STAGES) <= set(srows[0])
    table = format_table(loaded)
    assert "MISSED" in table and "split FASTER" in table and run.hardware.cpu in table
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"format": "nope"}))
    with pytest.raises(ValueError, match="not a benchmark run"):
        load_run(bad)


def test_replay_relabels_recorded_and_keeps_file_live(tmp_path: Path) -> None:
    run, run_path = _run_file(tmp_path)
    slept: list[float] = []
    msgs = list(iter_replay(run, speed=10.0, sleep=slept.append))
    assert len(msgs) == 6
    assert all(m.sample.mode == "recorded" for m in msgs)
    assert [m.seq for m in msgs] == [1, 2, 3, 4, 5, 6]
    # paced by the original gaps (100 ms) / speed 10 -> 10 ms, first frame immediate
    assert slept == pytest.approx([0.01] * 5, abs=1e-6)
    assert all(r.mode == "recorded" for r in recorded_results(run))
    # the stored file still says how it was measured
    assert all(s["mode"] == "live" for s in json.loads(run_path.read_text())["samples"])
    with pytest.raises(ValueError, match="speed"):
        list(iter_replay(run, speed=0))
    looped = iter_replay(run, loop=True, sleep=lambda _s: None, speed=1e9)
    assert len([next(looped) for _ in range(14)]) == 14  # loops past one pass
