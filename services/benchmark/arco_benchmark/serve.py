"""Telemetry server for the dashboard's FP32-vs-split strip.

- `GET /health`            what is loaded, what is streaming (live run or recorded replay)
- `GET /runs`              recorded runs available (`recorded/*.json`)
- `GET /results`           `BenchmarkResult`s of the current source (mode set honestly)
- `WS  /ws/telemetry`      `TelemetryMessage` / `BenchmarkMessage` frames
- `POST /run`              start a LIVE benchmark (needs model files); its samples stream
                           with `mode="live"` and replace the replay while running
- `POST /replay/{run_id}`  switch the replay source

Replay frames are `mode="recorded"` and carry the original hardware; nothing on this
channel is ever unlabeled.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from arco_base.api import Broadcaster
from arco_benchmark import __version__
from arco_benchmark.bench import BenchConfig, run_benchmark
from arco_benchmark.replay import iter_replay, recorded_results
from arco_benchmark.report import BenchmarkRun, load_run, write_run
from arco_schemas import (
    BenchmarkMessage,
    BenchmarkResult,
    StreamKind,
    TelemetryMessage,
    TelemetrySample,
)

SERVICE_ROOT = Path(__file__).resolve().parent.parent


class BenchmarkSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    benchmark_port: int = Field(default=8005, ge=1, le=65535)
    arco_models_dir: Path = Path("./models")
    arco_model_id: str = "yolov8n-seg"
    arco_bench_recorded_dir: Path = SERVICE_ROOT / "recorded"
    arco_bench_results_dir: Path = SERVICE_ROOT / "results"
    arco_bench_replay: str | None = Field(
        default=None, description="run_id (or file stem) to replay; default = newest recorded"
    )
    arco_bench_replay_speed: float = Field(default=1.0, gt=0)
    arco_bench_replay_loop: bool = True


def _default_streams() -> list[StreamKind]:
    return ["micro", "macro"]


class RunRequest(BaseModel):
    frames: int = Field(default=60, ge=1, le=5000)
    pace_fps: float | None = Field(default=30.0, gt=0)
    streams: list[StreamKind] = Field(default_factory=_default_streams)
    record: bool = Field(default=False, description="Promote the result to recorded/")


@dataclass
class State:
    settings: BenchmarkSettings
    bus: Broadcaster = field(default_factory=lambda: Broadcaster(maxsize=256))
    loop: asyncio.AbstractEventLoop | None = None
    replay_run: BenchmarkRun | None = None
    replay_thread: threading.Thread | None = None
    replay_stop: threading.Event = field(default_factory=threading.Event)
    live_thread: threading.Thread | None = None
    live_run: BenchmarkRun | None = None
    live_error: str | None = None
    live_results: list[BenchmarkResult] = field(default_factory=list)
    seq: int = 0
    frames_sent: int = 0

    # ---- publishing (thread -> loop) ---------------------------------------------------
    def publish(self, text: str) -> None:
        if self.loop is None:
            return
        self.frames_sent += 1
        self.loop.call_soon_threadsafe(self.bus.publish, text)

    def publish_sample(self, sample: TelemetrySample) -> None:
        self.seq += 1
        msg = TelemetryMessage(
            scenario_t=sample.scenario_t if sample.scenario_t is not None else -1.0,
            wall_t=datetime.now(UTC),
            seq=self.seq,
            sample=sample,
        )
        self.publish(msg.model_dump_json())

    def publish_result(self, result: BenchmarkResult) -> None:
        self.seq += 1
        msg = BenchmarkMessage(
            scenario_t=-1.0, wall_t=datetime.now(UTC), seq=self.seq, result=result
        )
        self.publish(msg.model_dump_json())

    # ---- recorded replay ------------------------------------------------------------------
    def recorded_files(self) -> list[Path]:
        d = self.settings.arco_bench_recorded_dir
        return sorted(d.glob("*.json")) if d.exists() else []

    def start_replay(self, run: BenchmarkRun) -> None:
        self.stop_replay()
        self.replay_run = run
        self.replay_stop = threading.Event()
        stop = self.replay_stop

        def worker() -> None:
            for r in recorded_results(run):
                self.publish_result(r)
            for msg in iter_replay(
                run,
                speed=self.settings.arco_bench_replay_speed,
                loop=self.settings.arco_bench_replay_loop,
                stop=stop.is_set,
            ):
                self.seq += 1
                self.publish(msg.model_copy(update={"seq": self.seq}).model_dump_json())

        self.replay_thread = threading.Thread(target=worker, name="bench-replay", daemon=True)
        self.replay_thread.start()

    def stop_replay(self) -> None:
        self.replay_stop.set()
        if self.replay_thread is not None:
            self.replay_thread.join(timeout=2)
        self.replay_thread = None

    # ---- live run ---------------------------------------------------------------------------
    def live_running(self) -> bool:
        return self.live_thread is not None and self.live_thread.is_alive()

    def start_live(self, req: RunRequest) -> str:
        if self.live_running():
            raise HTTPException(409, "a live run is already in progress")
        cfg = BenchConfig(
            models_dir=self.settings.arco_models_dir,
            model_id=self.settings.arco_model_id,
            frames=req.frames,
            pace_fps=req.pace_fps,
            streams=tuple(req.streams),
        )
        manifest = cfg.models_dir / cfg.model_id / "manifest.json"
        if not manifest.exists():
            raise HTTPException(409, f"no model files at {manifest}; live run impossible")
        self.live_error = None
        self.live_results = []
        self.stop_replay()  # live numbers must not interleave with a replay

        def worker() -> None:
            try:
                run = run_benchmark(
                    cfg,
                    on_sample=self.publish_sample,
                    on_result=self._live_result,
                    log=lambda _s: None,
                )
                self.live_run = run
                out = self.settings.arco_bench_results_dir / run.run_id
                write_run(run, out)
                if req.record:
                    self.settings.arco_bench_recorded_dir.mkdir(parents=True, exist_ok=True)
                    (self.settings.arco_bench_recorded_dir / f"{run.run_id}.json").write_text(
                        run.model_dump_json(indent=1) + "\n"
                    )
            except Exception as exc:
                self.live_error = f"{type(exc).__name__}: {exc}"
            finally:
                if self.replay_run is not None:  # resume the labelled replay
                    self.start_replay(self.replay_run)

        self.live_thread = threading.Thread(target=worker, name="bench-live", daemon=True)
        self.live_thread.start()
        return "started"

    def _live_result(self, result: BenchmarkResult) -> None:
        self.live_results.append(result)
        self.publish_result(result)


def _state(request: Request | WebSocket) -> State:
    st: State = request.app.state.bench
    return st


def create_app(settings: BenchmarkSettings | None = None) -> FastAPI:
    settings = settings or BenchmarkSettings()
    state = State(settings=settings)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        state.loop = asyncio.get_running_loop()
        files = state.recorded_files()
        chosen: Path | None = None
        if settings.arco_bench_replay:
            for f in files:
                if f.stem == settings.arco_bench_replay:
                    chosen = f
        elif files:
            chosen = files[-1]
        if chosen is not None:
            with contextlib.suppress(ValueError):
                state.start_replay(load_run(chosen))
        try:
            yield
        finally:
            state.stop_replay()

    app = FastAPI(title="arco-benchmark", version=__version__, lifespan=lifespan)
    app.state.bench = state

    @app.get("/health")
    def health(request: Request) -> dict[str, Any]:
        st = _state(request)
        return {
            "status": "ok",
            "version": __version__,
            "live_running": st.live_running(),
            "live_error": st.live_error,
            "replay_run_id": st.replay_run.run_id if st.replay_run else None,
            "replay_hardware": st.replay_run.hardware.model_dump() if st.replay_run else None,
            "recorded_runs": [f.stem for f in st.recorded_files()],
            "frames_sent": st.frames_sent,
            "ws_clients": st.bus.clients,
            "model_dir": str(st.settings.arco_models_dir / st.settings.arco_model_id),
            "model_present": (
                st.settings.arco_models_dir / st.settings.arco_model_id / "manifest.json"
            ).exists(),
        }

    @app.get("/runs")
    def runs(request: Request) -> list[dict[str, Any]]:
        out = []
        for f in _state(request).recorded_files():
            try:
                run = load_run(f)
            except ValueError:
                continue
            out.append(
                {
                    "run_id": run.run_id,
                    "started_at": run.started_at,
                    "hardware": run.hardware,
                    "model_id": run.model_id,
                    "frames_source": run.frames_source,
                    "pace_fps": run.pace_fps,
                    "results": [r.model_copy(update={"mode": "recorded"}) for r in run.results],
                }
            )
        return out

    @app.get("/results", response_model=list[BenchmarkResult])
    def results(request: Request) -> list[BenchmarkResult]:
        st = _state(request)
        if st.live_running() or (st.live_results and st.live_run is not None):
            return st.live_results
        if st.replay_run is not None:
            return recorded_results(st.replay_run)
        return []

    @app.post("/run")
    def start_run(request: Request, req: RunRequest) -> dict[str, str]:
        return {"status": _state(request).start_live(req)}

    @app.post("/replay/{run_id}")
    def switch_replay(request: Request, run_id: str) -> dict[str, str]:
        st = _state(request)
        for f in st.recorded_files():
            if f.stem == run_id:
                st.start_replay(load_run(f))
                return {"status": "replaying", "run_id": run_id}
        raise HTTPException(404, f"no recorded run {run_id}")

    @app.websocket("/ws/telemetry")
    async def ws_telemetry(ws: WebSocket) -> None:
        with contextlib.suppress(WebSocketDisconnect):
            await _state(ws).bus.serve(ws)

    return app


app: Any = create_app()  # replay thread starts in lifespan, not at import


def main() -> int:
    import uvicorn  # noqa: PLC0415 - server-only dependency path

    settings = BenchmarkSettings()
    uvicorn.run(
        create_app(settings),
        host="0.0.0.0",
        port=settings.benchmark_port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
