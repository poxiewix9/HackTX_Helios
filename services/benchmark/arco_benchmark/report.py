"""Run file (JSON) + CSVs. The run file is also what a recorded replay reads."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from arco_schemas import STAGES, BenchmarkResult, HardwareInfo, TelemetrySample

RUN_FORMAT = "arco-benchmark-run/1"


class BenchmarkRun(BaseModel):
    """Everything one `arco-bench run` produced. Stored under `results/` and, when
    promoted, `recorded/`. `samples` keep `mode="live"` as measured; a replay re-labels
    them `recorded` on the way out (see `replay.py`) — the file itself is the evidence."""

    model_config = ConfigDict(extra="forbid")

    format: str = RUN_FORMAT
    run_id: str
    started_at: datetime
    finished_at: datetime
    hardware: HardwareInfo
    model_id: str
    frames_source: str = Field(description="`synthetic:<seed>` (SIMULATED), a directory, or a file")
    pace_fps: float | None
    results: list[BenchmarkResult]
    comparisons: list[dict[str, Any]]
    samples: list[TelemetrySample]
    notes: list[str] = Field(default_factory=list)


def write_run(run: BenchmarkRun, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    run_path = out_dir / "run.json"
    run_path.write_text(run.model_dump_json(indent=1) + "\n")
    summary_path = out_dir / "summary.csv"
    with summary_path.open("w", newline="") as fh:
        cols = [
            "run_id",
            "pipeline",
            "stream",
            "mode",
            "frames",
            "dropped_frames",
            "mean_ms",
            "p50_ms",
            "p95_ms",
            "fps",
            "total_bytes_sent",
            "bytes_per_frame",
            "peak_rss_mb",
            "meets_target",
            "bottleneck_stage",
            *[f"mean_{s}_ms" for s in STAGES],
            "cpu",
            "gpu",
            "providers",
        ]
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in run.results:
            w.writerow(
                {
                    "run_id": r.run_id,
                    "pipeline": r.pipeline,
                    "stream": r.stream,
                    "mode": r.mode,
                    "frames": r.frames,
                    "dropped_frames": r.dropped_frames,
                    "mean_ms": r.mean_ms,
                    "p50_ms": r.p50_ms,
                    "p95_ms": r.p95_ms,
                    "fps": r.fps,
                    "total_bytes_sent": r.total_bytes_sent,
                    "bytes_per_frame": round(r.total_bytes_sent / r.frames) if r.frames else 0,
                    "peak_rss_mb": r.peak_rss_mb,
                    "meets_target": r.meets_target,
                    "bottleneck_stage": r.bottleneck_stage or "",
                    **{f"mean_{s}_ms": r.mean_stage_ms.get(s, 0.0) for s in STAGES},
                    "cpu": r.hardware.cpu,
                    "gpu": r.hardware.gpu or "",
                    "providers": "|".join(r.hardware.onnxruntime_providers),
                }
            )
    samples_path = out_dir / "samples.csv"
    with samples_path.open("w", newline="") as fh:
        cols = [
            "pipeline",
            "stream",
            "mode",
            "frame_id",
            "wall_t",
            "total_ms",
            "fps",
            "bytes_sent",
            "dropped_frames",
            "cpu_percent",
            "rss_mb",
            *STAGES,
        ]
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for s in run.samples:
            w.writerow(
                {
                    "pipeline": s.pipeline,
                    "stream": s.stream,
                    "mode": s.mode,
                    "frame_id": s.frame_id,
                    "wall_t": s.wall_t.isoformat(),
                    "total_ms": s.total_ms,
                    "fps": s.fps,
                    "bytes_sent": s.bytes_sent,
                    "dropped_frames": s.dropped_frames,
                    "cpu_percent": s.cpu_percent,
                    "rss_mb": s.rss_mb,
                    **{st: s.stage_ms.get(st, 0.0) for st in STAGES},
                }
            )
    return {"run": run_path, "summary": summary_path, "samples": samples_path}


def load_run(path: Path) -> BenchmarkRun:
    data = json.loads(path.read_text())
    if data.get("format") != RUN_FORMAT:
        msg = f"{path}: not a benchmark run file (format={data.get('format')!r})"
        raise ValueError(msg)
    return BenchmarkRun.model_validate(data)


def format_table(run: BenchmarkRun) -> str:
    """Human summary for the terminal. States hardware first, verdict last."""
    hw = run.hardware
    lines = [
        f"run {run.run_id}  model {run.model_id}  frames {run.frames_source}"
        + (f"  paced {run.pace_fps} fps" if run.pace_fps else "  unpaced (throughput)"),
        f"hardware: {hw.cpu} x{hw.cpu_count}, {hw.memory_gb} GB, "
        f"gpu={hw.gpu or 'none used'}, ort={','.join(hw.onnxruntime_providers)}, "
        f"{hw.platform}, py{hw.python}",
        "",
        f"{'pipeline':<11}{'stream':<7}{'n':>4}{'drop':>5}{'mean':>8}{'p50':>8}{'p95':>8}"
        f"{'fps':>7}{'kB/f':>7}{'rss':>7}  target  bottleneck",
    ]
    for r in run.results:
        kb = (r.total_bytes_sent / r.frames / 1024) if r.frames else 0.0
        lines.append(
            f"{r.pipeline:<11}{r.stream or '-':<7}{r.frames:>4}{r.dropped_frames:>5}"
            f"{r.mean_ms:>8.1f}{r.p50_ms:>8.1f}{r.p95_ms:>8.1f}{r.fps:>7.1f}{kb:>7.1f}"
            f"{(r.peak_rss_mb or 0):>7.0f}  {'MET' if r.meets_target else 'MISSED':<7} "
            f"{r.bottleneck_stage or ''}"
        )
    lines.append("")
    lines.append("stage means (ms):")
    head = f"{'pipeline/stream':<19}" + "".join(f"{s[:7]:>9}" for s in STAGES)
    lines.append(head)
    for r in run.results:
        lines.append(
            f"{r.pipeline + '/' + (r.stream or '-'):<19}"
            + "".join(f"{r.mean_stage_ms.get(s, 0.0):>9.2f}" for s in STAGES)
        )
    lines.append("")
    for c in run.comparisons:
        verdict = "split FASTER" if c["b_faster"] else "split NOT faster"
        lines.append(
            f"{c['stream']}: {verdict} (mean {c['mean_ms_a']} -> {c['mean_ms_b']} ms, "
            f"x{c['speedup_mean']}), bandwidth {c['bytes_per_frame_a']} -> "
            f"{c['bytes_per_frame_b']} B/frame (x{c['bandwidth_ratio']}), "
            + (
                "target met"
                if c["b_meets_target"]
                else f"target MISSED, bottleneck={c['b_bottleneck']}"
            )
        )
    lines.extend(run.notes)
    return "\n".join(lines)
