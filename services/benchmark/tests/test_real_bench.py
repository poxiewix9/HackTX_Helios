"""Runs only when `models/<id>/manifest.json` exists: a short real benchmark. Prints the
measured numbers; asserts the mechanics (both pipelines ran on the same frames, stages
measured, files written), never a performance target."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from arco_benchmark.bench import BenchConfig, run_benchmark
from arco_benchmark.report import format_table, load_run, write_run

REPO_ROOT = Path(__file__).resolve().parents[3]
MODELS_DIR = Path(os.environ.get("ARCO_MODELS_DIR", REPO_ROOT / "models"))
MODEL_ID = os.environ.get("ARCO_MODEL_ID", "yolov8n-seg")
MANIFEST = MODELS_DIR / MODEL_ID / "manifest.json"

pytestmark = pytest.mark.skipif(
    not MANIFEST.exists(), reason=f"no exported model at {MANIFEST} (make export-split)"
)


def test_real_benchmark_short_run(tmp_path: Path) -> None:
    cfg = BenchConfig(
        models_dir=MODELS_DIR,
        model_id=MODEL_ID,
        frames=6,
        warmup=1,
        pace_fps=None,
        streams=("micro",),
    )
    run = run_benchmark(cfg, log=lambda _s: None)
    assert [r.pipeline for r in run.results] == ["fp32_full", "int8_split"]
    a, b = run.results
    assert a.frames == b.frames == 6 and a.dropped_frames == b.dropped_frames == 0
    assert (
        a.mean_stage_ms["tail"] > 0 and b.mean_stage_ms["tail"] > 0 and b.mean_stage_ms["head"] > 0
    )
    assert a.mean_stage_ms["head"] == 0.0  # no head stage in the unsplit pipeline
    assert a.total_bytes_sent > 0 and b.total_bytes_sent > 0
    assert run.hardware.onnxruntime_providers == b.hardware.onnxruntime_providers
    assert run.hardware.cpu and "synthetic" in run.frames_source
    # identical frames: same frame ids in the same order for both pipelines
    fa = [s.frame_id for s in run.samples if s.pipeline == "fp32_full"]
    fb = [s.frame_id for s in run.samples if s.pipeline == "int8_split"]
    assert fa == fb
    paths = write_run(run, tmp_path / run.run_id)
    assert load_run(paths["run"]).run_id == run.run_id
    print("\n" + format_table(run))
