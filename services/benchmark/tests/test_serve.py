"""Telemetry server: replays a recorded run labelled RECORDED, refuses a live run without
model files, lists runs."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from arco_benchmark.hardware import read_hardware
from arco_benchmark.report import BenchmarkRun, write_run
from arco_benchmark.serve import BenchmarkSettings, create_app
from arco_schemas import (
    STAGES,
    BenchmarkMessage,
    BenchmarkResult,
    TelemetryChannelMessage,
    TelemetryMessage,
    TelemetrySample,
)

_adapter: TypeAdapter[TelemetryChannelMessage] = TypeAdapter(TelemetryChannelMessage)


def _recorded(tmp_path: Path) -> Path:
    hw = read_hardware(["CPUExecutionProvider"])
    t0 = datetime.now(UTC)
    res = [
        BenchmarkResult(
            mode="live",
            pipeline=k,
            stream="micro",
            run_id="rec1",
            started_at=t0,
            hardware=hw,
            frames=2,
            dropped_frames=0,
            mean_ms=50.0,
            p50_ms=50.0,
            p95_ms=50.0,
            fps=20.0,
            mean_stage_ms=dict.fromkeys(STAGES, 1.0),
            total_bytes_sent=10,
            meets_target=False,
            bottleneck_stage="tail",
        )
        for k in ("fp32_full", "int8_split")
    ]
    run = BenchmarkRun(
        run_id="rec1",
        started_at=t0,
        finished_at=t0,
        hardware=hw,
        model_id="fake",
        frames_source="synthetic:0",
        pace_fps=30.0,
        results=res,
        comparisons=[],
        samples=[
            TelemetrySample(
                mode="live",
                pipeline="fp32_full" if i % 2 == 0 else "int8_split",
                stream="micro",
                frame_id=i,
                wall_t=t0 + timedelta(milliseconds=5 * i),
                stage_ms=dict.fromkeys(STAGES, 1.0),
                total_ms=9.0,
                fps=10.0,
                bytes_sent=5,
                dropped_frames=0,
            )
            for i in range(4)
        ],
    )
    rec_dir = tmp_path / "recorded"
    rec_dir.mkdir()
    paths = write_run(run, tmp_path / "results" / "rec1")
    (rec_dir / "rec1.json").write_text(paths["run"].read_text())
    return rec_dir


def test_replay_server_labels_everything_recorded(tmp_path: Path) -> None:
    rec_dir = _recorded(tmp_path)
    settings = BenchmarkSettings(
        arco_bench_recorded_dir=rec_dir,
        arco_bench_results_dir=tmp_path / "results",
        arco_models_dir=tmp_path / "no-models",
        arco_bench_replay_speed=1000.0,
        arco_bench_replay_loop=True,
    )
    with TestClient(create_app(settings)) as c:
        health = c.get("/health").json()
        assert health["replay_run_id"] == "rec1" and health["model_present"] is False
        assert health["replay_hardware"]["cpu"] == read_hardware().cpu
        assert health["recorded_runs"] == ["rec1"]
        results = c.get("/results").json()
        assert len(results) == 2 and all(r["mode"] == "recorded" for r in results)
        runs = c.get("/runs").json()
        assert runs[0]["run_id"] == "rec1" and runs[0]["results"][0]["mode"] == "recorded"
        # no model files -> a live run is refused, not faked
        r = c.post("/run", json={"frames": 5})
        assert r.status_code == 409 and "no model files" in r.json()["detail"]
        assert c.post("/replay/nope").status_code == 404
        with c.websocket_connect("/ws/telemetry") as ws:
            got = [_adapter.validate_python(json.loads(ws.receive_text())) for _ in range(6)]
        kinds = {type(m) for m in got}
        assert kinds <= {BenchmarkMessage, TelemetryMessage}
        tele = [m for m in got if isinstance(m, TelemetryMessage)]
        assert tele and all(m.sample.mode == "recorded" for m in tele)
        bench = [m for m in got if isinstance(m, BenchmarkMessage)]
        assert all(m.result.mode == "recorded" for m in bench)
        seqs = [m.seq for m in got]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
        assert c.get("/health").json()["frames_sent"] >= 6
