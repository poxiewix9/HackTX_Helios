"""Simulator HTTP surface (port SIMULATOR_PORT, default 8004).

GET  /health                      player + downstream reachability
GET  /scenarios                   available scenario files (id, name, event, duration)
GET  /scenario                    the loaded Scenario (full JSON)
GET  /state                       PlayerState (clock, cursors, active hazards, log tail)
POST /load {scenario_id}          reset world + ingest graph + send population
POST /play | /pause               transport
POST /speed {speed}               playback speed multiplier
POST /seek {t}                    scrub (reconciles hazards/network to the timeline at t)
POST /chaos/hazard {...}          ad-hoc simulated hazard through the blackboard path
POST /chaos/cellular {up, reason} simulated cellular outage / restore
GET  /check                       expected_blocked_edges vs severed edges on the blackboard
GET  /feeds/{camera_id}/frame.jpg?t=   synthetic street frame at scenario time t
GET  /feeds/{camera_id}/clip.mp4?t0=&duration= synthetic clip (for edge-capture --source)
"""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import cv2
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from arco_schemas import HazardClass, HazardGeometry, Scenario
from arco_schemas.scenario import SIMULATION_LABEL
from arco_sim import __version__
from arco_sim.clients import AgentsClient, BlackboardClient
from arco_sim.feeds import FeedSpec, render_frame, write_clip
from arco_sim.logging import configure_logging, get_logger
from arco_sim.player import ExpectedCheck, Player, PlayerState
from arco_sim.scenario_io import list_scenarios, load_scenario
from arco_sim.settings import SimulatorSettings, get_settings

log = get_logger("simulator.api")


class ScenarioSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    event: str
    event_date: str
    location: str
    duration_s: float
    label: str


class LoadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str


class SpeedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speed: float = Field(gt=0, le=1000)


class SeekRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    t: float = Field(ge=0)


class ChaosHazardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    geom: HazardGeometry
    hazard_class: HazardClass
    confidence: float = Field(default=0.9, ge=0, le=1)
    velocity_m_s: float | None = Field(default=None, ge=0)
    note: str | None = None


class ChaosCellularRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    up: bool
    reason: str | None = None


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    version: str
    label: str
    blackboard: bool
    agents: bool
    scenario_id: str | None
    playing: bool
    t: float
    speed: float
    last_error: str | None


class Runtime:
    def __init__(self, settings: SimulatorSettings) -> None:
        self.settings = settings
        self.bb = BlackboardClient(
            settings.arco_blackboard_url, timeout_s=settings.arco_sim_http_timeout_s
        )
        self.agents = AgentsClient(
            settings.arco_agents_url, timeout_s=settings.arco_sim_http_timeout_s
        )
        self.player = Player(self.bb, self.agents)
        self.player.config.clock.hz = settings.arco_sim_clock_hz
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self.player.run, args=(self._stop,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self.bb.close()
        self.agents.close()

    def scenario_path(self, scenario_id: str) -> Path:
        p = self.settings.arco_scenarios_dir / f"{scenario_id}.json"
        if not p.exists():
            raise HTTPException(status_code=404, detail=f"unknown scenario {scenario_id!r}")
        return p

    def load(self, scenario_id: str) -> Scenario:
        sc = load_scenario(self.scenario_path(scenario_id))
        self.player.load(sc)
        return sc


def runtime(request: Request) -> Runtime:
    return request.app.state.runtime  # type: ignore[no-any-return]


def create_app(settings: SimulatorSettings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging("simulator")

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        rt = Runtime(settings)
        app.state.runtime = rt
        if settings.arco_sim_autoload:
            try:
                await asyncio.to_thread(rt.load, settings.arco_default_scenario)
                if settings.arco_sim_autoplay_speed > 0:
                    rt.player.set_speed(settings.arco_sim_autoplay_speed)
                    rt.player.play()
            except Exception as exc:  # the API must come up even if the blackboard is late
                rt.player.last_error = f"autoload: {exc!r}"
                log.error("autoload failed", error=repr(exc))
        rt.start()
        log.info("simulator api up", scenario=settings.arco_default_scenario)
        try:
            yield
        finally:
            await asyncio.to_thread(rt.stop)

    app = FastAPI(title="ARCO simulator", version=__version__, lifespan=lifespan)
    _info_routes(app)
    _transport_routes(app)
    _chaos_routes(app)
    _feed_routes(app)
    return app


def _info_routes(app: FastAPI) -> None:
    @app.get("/health", response_model=HealthResponse)
    def health(request: Request) -> HealthResponse:
        rt = runtime(request)
        bb_ok = False
        with contextlib.suppress(Exception):
            bb_ok = rt.bb.health().get("status") in ("ok", "degraded")
        ag_ok = rt.agents.health() is not None
        p = rt.player
        return HealthResponse(
            status="ok" if bb_ok else "degraded",
            version=__version__,
            label=SIMULATION_LABEL,
            blackboard=bb_ok,
            agents=ag_ok,
            scenario_id=p.scenario.id if p.scenario else None,
            playing=p.playing,
            t=round(p.t, 3),
            speed=p.speed,
            last_error=p.last_error,
        )

    @app.get("/scenarios", response_model=list[ScenarioSummary])
    def scenarios(request: Request) -> list[ScenarioSummary]:
        out: list[ScenarioSummary] = []
        for p in list_scenarios(runtime(request).settings.arco_scenarios_dir):
            sc = load_scenario(p)
            out.append(
                ScenarioSummary(
                    id=sc.id,
                    name=sc.name,
                    event=sc.event,
                    event_date=sc.event_date,
                    location=sc.location,
                    duration_s=sc.duration_s,
                    label=sc.label,
                )
            )
        return out

    @app.get("/scenario", response_model=Scenario)
    def scenario(request: Request) -> Scenario:
        sc = runtime(request).player.scenario
        if sc is None:
            raise HTTPException(status_code=404, detail="no scenario loaded")
        return sc

    @app.get("/state", response_model=PlayerState)
    def state(request: Request) -> PlayerState:
        return runtime(request).player.state()

    @app.get("/check", response_model=ExpectedCheck)
    def check(request: Request) -> ExpectedCheck:
        return runtime(request).player.check_expected()


def _transport_routes(app: FastAPI) -> None:
    @app.post("/load", response_model=PlayerState)
    async def load(request: Request, body: LoadRequest) -> PlayerState:
        rt = runtime(request)
        await asyncio.to_thread(rt.load, body.scenario_id)
        return rt.player.state()

    @app.post("/play", response_model=PlayerState)
    def play(request: Request) -> PlayerState:
        p = runtime(request).player
        _require_loaded(p)
        p.play()
        return p.state()

    @app.post("/pause", response_model=PlayerState)
    def pause(request: Request) -> PlayerState:
        p = runtime(request).player
        _require_loaded(p)
        p.pause()
        return p.state()

    @app.post("/speed", response_model=PlayerState)
    def speed(request: Request, body: SpeedRequest) -> PlayerState:
        p = runtime(request).player
        p.set_speed(body.speed)
        return p.state()

    @app.post("/seek", response_model=PlayerState)
    async def seek(request: Request, body: SeekRequest) -> PlayerState:
        p = runtime(request).player
        _require_loaded(p)
        await asyncio.to_thread(p.seek, body.t)
        return p.state()


def _chaos_routes(app: FastAPI) -> None:
    @app.post("/chaos/hazard", status_code=201)
    def chaos_hazard(request: Request, body: ChaosHazardRequest) -> dict[str, Any]:
        p = runtime(request).player
        hz = p.inject_hazard(
            geom=body.geom,
            hazard_class=body.hazard_class,
            confidence=body.confidence,
            velocity_m_s=body.velocity_m_s,
            note=body.note,
        )
        return {"hazard_id": str(hz.id), "scenario_t": p.t, "label": SIMULATION_LABEL}

    @app.post("/chaos/cellular", response_model=PlayerState)
    def chaos_cellular(request: Request, body: ChaosCellularRequest) -> PlayerState:
        p = runtime(request).player
        p.set_cellular(body.up, reason=body.reason or "chaos")
        return p.state()


def _feed_routes(app: FastAPI) -> None:
    @app.get("/feeds/{camera_id}/frame.jpg")
    def feed_frame(request: Request, camera_id: str, t: float | None = None) -> Response:
        p = runtime(request).player
        feed = _feed(p, camera_id)
        img = render_frame(feed, p.t if t is None else t)
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            raise HTTPException(status_code=500, detail="encode failed")
        return Response(content=buf.tobytes(), media_type="image/jpeg")

    @app.get("/feeds/{camera_id}/clip.mp4")
    def feed_clip(
        request: Request,
        camera_id: str,
        t0: float = Query(default=0.0, ge=0),
        duration: float = Query(default=10.0, gt=0, le=120),
        fps: float = Query(default=10.0, gt=0, le=30),
    ) -> FileResponse:
        p = runtime(request).player
        feed = _feed(p, camera_id)
        out = Path(tempfile.mkdtemp(prefix="arco-sim-")) / f"{camera_id}.mp4"
        write_clip(feed, out, t_start=t0, duration_s=duration, spec=FeedSpec(fps=fps))
        return FileResponse(out, media_type="video/mp4", filename=out.name)


def _require_loaded(p: Player) -> None:
    if p.scenario is None:
        raise HTTPException(status_code=409, detail="no scenario loaded; POST /load first")


def _feed(p: Player, camera_id: str) -> Any:
    if p.scenario is None:
        raise HTTPException(status_code=409, detail="no scenario loaded")
    for f in p.scenario.micro_feeds:
        if f.camera_id == camera_id:
            return f
    raise HTTPException(status_code=404, detail=f"unknown camera {camera_id!r}")


app: Any = create_app()


def main() -> None:
    settings = get_settings()
    uvicorn.run("arco_sim.api:app", host="0.0.0.0", port=settings.simulator_port, log_level="info")
