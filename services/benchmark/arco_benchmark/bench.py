"""One full benchmark: both pipelines x both streams on identical frames."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from arco_base.settings import DEFAULT_CLASS_MAP
from arco_benchmark.hardware import read_hardware
from arco_benchmark.pipelines import FramePipeline, build_pipelines
from arco_benchmark.report import BenchmarkRun
from arco_benchmark.runner import compare, run_pipeline, summarize
from arco_edge.sources import FrameSource, make_source
from arco_schemas import BenchmarkResult, Georef, StreamKind, TelemetrySample

_georef_adapter: TypeAdapter[Any] = TypeAdapter(Georef)
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_GEOREFS: dict[str, Path] = {
    "micro": REPO_ROOT / "data" / "samples" / "vision" / "georef" / "camera_front_st.json",
    "macro": REPO_ROOT / "data" / "samples" / "vision" / "georef" / "tile_lahaina_0.json",
}


@dataclass
class BenchConfig:
    models_dir: Path = REPO_ROOT / "models"
    model_id: str = "yolov8n-seg"
    class_map: Path = DEFAULT_CLASS_MAP
    providers: list[str] | None = None
    frames: int = 60
    warmup: int = 3
    pace_fps: float | None = 30.0
    streams: tuple[StreamKind, ...] = ("micro", "macro")
    source: str = "synthetic:0"
    micro_size: tuple[int, int] = (1280, 720)
    macro_size: tuple[int, int] = (1024, 1024)
    georefs: dict[str, Path] | None = None
    target_ms: float = 80.0
    target_fps: float = 30.0


def _load_georef(path: Path) -> Any:
    return _georef_adapter.validate_python(json.loads(path.read_text()))


def _source_for(cfg: BenchConfig, stream: StreamKind) -> FrameSource:
    w, h = cfg.micro_size if stream == "micro" else cfg.macro_size
    # unpaced source: the runner does the pacing so drops are attributable to the pipeline
    return make_source(
        cfg.source, width=w, height=h, fps=1000.0, loop=True, stream=stream, realtime=False
    )


def run_benchmark(
    cfg: BenchConfig,
    *,
    on_sample: Callable[[TelemetrySample], None] | None = None,
    on_result: Callable[[BenchmarkResult], None] | None = None,
    log: Callable[[str], None] = print,
) -> BenchmarkRun:
    from arco_base.detectors import select_providers  # noqa: PLC0415 - loads onnxruntime

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    started = datetime.now(UTC)
    providers = cfg.providers or select_providers(None)
    georefs = {**DEFAULT_GEOREFS, **(cfg.georefs or {})}
    results: list[BenchmarkResult] = []
    samples: list[TelemetrySample] = []
    hardware = None
    model_label = cfg.model_id
    notes = [
        "frames: "
        + (
            "SIMULATED synthetic scenes (seeded, identical for both pipelines); "
            "timings are real, detections on them are meaningless"
            if cfg.source.startswith("synthetic")
            else f"from {cfg.source}"
        ),
        "network stage is 0 for both pipelines: both halves ran in one process so the "
        "numbers are compute + codec only; bytes_sent is what the link would carry "
        "(JPEG q90 for fp32_full, the feature packet for int8_split)",
    ]
    for stream in cfg.streams:
        georef = _load_georef(georefs[stream])
        full, split = build_pipelines(
            models_dir=cfg.models_dir,
            model_id=cfg.model_id,
            class_map=cfg.class_map,
            providers=providers,
            stream=stream,
            georef=georef,
        )
        if hardware is None:
            hardware = read_hardware(split.providers)
            model_label = f"{full.model_id} vs {split.model_id}"
        pipeline: FramePipeline
        for pipeline in (full, split):
            log(f"[bench] {pipeline.kind} on {stream}: {cfg.frames} frames ...")
            stats = run_pipeline(
                pipeline,
                _source_for(cfg, stream).frames(),
                stream=stream,
                max_frames=cfg.frames,
                pace_fps=cfg.pace_fps,
                warmup=cfg.warmup,
                on_sample=on_sample,
            )
            res = summarize(
                stats,
                pipeline=pipeline,
                stream=stream,
                run_id=run_id,
                started_at=started,
                hardware=hardware,
                target_ms=cfg.target_ms,
                target_fps=cfg.target_fps,
            )
            log(
                f"[bench]   mean {res.mean_ms} ms  p95 {res.p95_ms} ms  fps {res.fps}  "
                f"dropped {res.dropped_frames}  "
                + ("target met" if res.meets_target else f"target MISSED: {res.bottleneck_stage}")
            )
            results.append(res)
            samples.extend(stats.samples)
            if on_result is not None:
                on_result(res)
    if hardware is None:
        msg = "no streams configured"
        raise ValueError(msg)
    comparisons = [
        compare(a, b)
        for a, b in zip(results[0::2], results[1::2], strict=True)
        if a.pipeline == "fp32_full" and b.pipeline == "int8_split"
    ]
    return BenchmarkRun(
        run_id=run_id,
        started_at=started,
        finished_at=datetime.now(UTC),
        hardware=hardware,
        model_id=model_label,
        frames_source=cfg.source,
        pace_fps=cfg.pace_fps,
        results=results,
        comparisons=comparisons,
        samples=samples,
        notes=notes,
    )
