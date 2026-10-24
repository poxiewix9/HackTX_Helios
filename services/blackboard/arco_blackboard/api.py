"""Blackboard FastAPI app: thin spatial REST + `/ws/blackboard` event stream.

Run: `uvicorn arco_blackboard.api:app --port 8001` or `arco-blackboard-serve`.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated
from uuid import UUID

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict

from arco_blackboard import __version__, bus
from arco_blackboard.db import db_reachable, extension_versions, get_engine
from arco_blackboard.logging import configure_logging, get_logger
from arco_blackboard.repo import parse_bbox
from arco_blackboard.service import NO_SCENARIO, BlackboardService
from arco_blackboard.settings import Settings, get_settings
from arco_schemas import (
    AgentEvent,
    AgentEventMessage,
    BusEnvelope,
    CommunityReport,
    EdgeOverlap,
    EdgeStatus,
    Hazard,
    HazardEvent,
    HazardStatus,
    ReportEvent,
    RoadEdge,
    Route,
    SafeZone,
    SafeZoneEvent,
)
from arco_schemas.geo import Geometry

log = get_logger("blackboard.api")

# ---------------------------------------------------------------------------
# WebSocket fan-out: one bus subscription shared by all clients
# ---------------------------------------------------------------------------


@dataclass(eq=False)  # identity-hashed so clients can live in a set
class _Client:
    queue: asyncio.Queue[BusEnvelope]
    topics: set[str] | None
    dropped: int = 0


@dataclass
class Broadcaster:
    settings: Settings
    clients: set[_Client] = field(default_factory=set)
    task: asyncio.Task[None] | None = None
    subscriber: bus.Subscriber | None = None
    delivered: int = 0

    async def start(self) -> None:
        if self.task is None:
            self.subscriber = bus.make_subscriber(self.settings)
            self.task = asyncio.create_task(self._pump(), name="blackboard-broadcast")
            log.info("broadcaster started", subscriber=self.subscriber.name)

    async def _pump(self) -> None:
        assert self.subscriber is not None
        backoff = 0.5
        while True:
            try:
                async for env in self.subscriber.events():
                    backoff = 0.5
                    self.delivered += 1
                    for c in list(self.clients):
                        if c.topics and env.topic not in c.topics:
                            continue
                        if c.queue.full():  # slow client: drop oldest, keep latest
                            with contextlib.suppress(asyncio.QueueEmpty):
                                c.queue.get_nowait()
                            c.dropped += 1
                        c.queue.put_nowait(env)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning(
                    "broadcast subscription error; reconnecting", error=str(exc), backoff_s=backoff
                )
                with contextlib.suppress(Exception):
                    await self.subscriber.close()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)

    def attach(self, topics: set[str] | None) -> _Client:
        c = _Client(queue=asyncio.Queue(maxsize=1000), topics=topics)
        self.clients.add(c)
        return c

    def detach(self, c: _Client) -> None:
        self.clients.discard(c)

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.task
            self.task = None
        if self.subscriber is not None:
            await self.subscriber.close()
            self.subscriber = None


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    version: str
    db: bool
    postgis: str | None
    pgrouting: str | None
    bus_mode: str
    ws_clients: int
    tables: dict[str, int]


class EdgeIntersectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    geom: Geometry


def svc(request: Request) -> BlackboardService:
    return request.app.state.service  # type: ignore[no-any-return]


Svc = Annotated[BlackboardService, Depends(svc)]
ScenarioT = Annotated[float, Query(description="Scenario clock seconds; -1 if none")]


def bbox_param(bbox: str | None) -> list[float] | None:
    if bbox is None:
        return None
    try:
        return parse_bbox(bbox)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def create_app(settings: Settings | None = None, *, publisher_name: str = "blackboard") -> FastAPI:
    settings = settings or get_settings()
    configure_logging("blackboard")

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = get_engine(settings.database_url)
        publisher = bus.make_publisher(settings, publisher_name)
        app.state.settings = settings
        app.state.engine = engine
        app.state.service = BlackboardService(engine, publisher)
        app.state.broadcaster = Broadcaster(settings)
        log.info("blackboard api up", bus=publisher.mode, version=__version__)
        try:
            yield
        finally:
            await app.state.broadcaster.stop()
            publisher.close()

    app = FastAPI(title="ARCO Blackboard", version=__version__, lifespan=lifespan)
    _health_routes(app)
    _hazard_routes(app)
    _edge_routes(app)
    _misc_routes(app)
    _ws_routes(app)

    @app.exception_handler(ValueError)
    async def _value_error(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    return app


def _health_routes(app: FastAPI) -> None:
    @app.get("/health", response_model=HealthResponse)
    def health(request: Request) -> HealthResponse:
        engine = request.app.state.engine
        ok = db_reachable(engine)
        ext = extension_versions(engine) if ok else {"postgis": None, "pgrouting": None}
        service: BlackboardService = request.app.state.service
        counts = service.table_counts() if ok else {}
        return HealthResponse(
            status="ok" if ok and ext["postgis"] and ext["pgrouting"] else "degraded",
            version=__version__,
            db=ok,
            postgis=ext["postgis"],
            pgrouting=ext["pgrouting"],
            bus_mode=service.publisher.mode,
            ws_clients=len(request.app.state.broadcaster.clients),
            tables=counts,
        )

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics(request: Request) -> str:
        service: BlackboardService = request.app.state.service
        b: Broadcaster = request.app.state.broadcaster
        lines = [
            "# TYPE arco_blackboard_rows gauge",
            *(
                f'arco_blackboard_rows{{table="{t}"}} {n}'
                for t, n in service.table_counts().items()
            ),
            "# TYPE arco_blackboard_ws_clients gauge",
            f"arco_blackboard_ws_clients {len(b.clients)}",
            "# TYPE arco_blackboard_ws_delivered_total counter",
            f"arco_blackboard_ws_delivered_total {b.delivered}",
            "# TYPE arco_blackboard_ws_dropped_total counter",
            f"arco_blackboard_ws_dropped_total {sum(c.dropped for c in b.clients)}",
        ]
        return "\n".join(lines) + "\n"


def _hazard_routes(app: FastAPI) -> None:

    @app.get("/hazards", response_model=list[Hazard])
    def get_hazards(
        service: Svc,
        bbox: str | None = None,
        status: HazardStatus | None = None,
        limit: int = Query(default=1000, ge=1, le=10000),
    ) -> list[Hazard]:
        return service.list_hazards(bbox=bbox_param(bbox), status=status, limit=limit)

    @app.get("/hazards/{hazard_id}", response_model=Hazard)
    def get_hazard(service: Svc, hazard_id: UUID) -> Hazard:
        h = service.get_hazard(hazard_id)
        if h is None:
            raise HTTPException(status_code=404, detail="hazard not found")
        return h

    @app.post("/hazards", response_model=HazardEvent, status_code=201)
    def post_hazard(
        service: Svc, hazard: Hazard, scenario_t: ScenarioT = NO_SCENARIO
    ) -> HazardEvent:
        """Insert or update a hazard. This is *the* ingestion path for vision, the
        simulator and the Consensus agent; it always emits a `hazard.*` event."""
        return service.upsert_hazard(hazard, scenario_t=scenario_t)

    @app.post("/hazards/{hazard_id}/status", response_model=HazardEvent)
    def post_hazard_status(
        service: Svc, hazard_id: UUID, status: HazardStatus, scenario_t: ScenarioT = NO_SCENARIO
    ) -> HazardEvent:
        ev = service.set_hazard_status(hazard_id, status, scenario_t=scenario_t)
        if ev is None:
            raise HTTPException(status_code=404, detail="hazard not found")
        return ev


def _edge_routes(app: FastAPI) -> None:

    @app.get("/edges", response_model=list[RoadEdge])
    def get_edges(
        service: Svc,
        bbox: str | None = None,
        status: EdgeStatus | None = None,
        limit: int | None = Query(default=None, ge=1, le=200000),
    ) -> list[RoadEdge]:
        return service.list_edges(bbox=bbox_param(bbox), status=status, limit=limit)

    @app.post("/edges/intersecting", response_model=list[EdgeOverlap])
    def post_edges_intersecting(service: Svc, body: EdgeIntersectRequest) -> list[EdgeOverlap]:
        return service.edges_intersecting(body.geom)

    @app.get("/graph/version")
    def get_graph_version(service: Svc) -> dict[str, int]:
        return {"graph_version": service.graph_version()}


def _misc_routes(app: FastAPI) -> None:

    @app.get("/routes", response_model=list[Route])
    def get_routes(service: Svc, crowd_group_id: str | None = None) -> list[Route]:
        return service.list_routes(crowd_group_id=crowd_group_id)

    @app.get("/safe-zones", response_model=list[SafeZone])
    def get_safe_zones(service: Svc) -> list[SafeZone]:
        return service.list_safe_zones()

    @app.put("/safe-zones/{zone_id}", response_model=SafeZoneEvent)
    def put_safe_zone(
        service: Svc, zone_id: str, zone: SafeZone, scenario_t: ScenarioT = NO_SCENARIO
    ) -> SafeZoneEvent:
        if zone.id != zone_id:
            raise HTTPException(status_code=422, detail="path id and body id differ")
        return service.upsert_safe_zone(zone, scenario_t=scenario_t)

    @app.get("/reports", response_model=list[CommunityReport])
    def get_reports(
        service: Svc,
        bbox: str | None = None,
        since: datetime | None = None,
        limit: int = Query(default=1000, ge=1, le=10000),
    ) -> list[CommunityReport]:
        return service.list_reports(bbox=bbox_param(bbox), since=since, limit=limit)

    @app.post("/reports", response_model=ReportEvent, status_code=201)
    def post_report(
        service: Svc, report: CommunityReport, scenario_t: ScenarioT = NO_SCENARIO
    ) -> ReportEvent:
        return service.add_report(report, scenario_t=scenario_t)

    @app.get("/agent-events", response_model=list[AgentEvent])
    def get_agent_events(
        service: Svc, agent: str | None = None, limit: int = Query(default=200, ge=1, le=5000)
    ) -> list[AgentEvent]:
        return service.list_agent_events(agent=agent, limit=limit)

    @app.post("/agent-events", response_model=AgentEventMessage, status_code=201)
    def post_agent_event(service: Svc, ev: AgentEvent) -> AgentEventMessage:
        return service.append_agent_event(ev)

    @app.get("/events", response_model=list[BusEnvelope])
    def get_events(
        request: Request,
        since_seq: int = Query(default=0, ge=0),
        topics: str | None = None,
        limit: int = Query(default=1000, ge=1, le=5000),
    ) -> list[BusEnvelope]:
        """Outbox catch-up: every event with seq > since_seq."""
        topic_set = set(topics.split(",")) if topics else None
        return bus.read_outbox(
            request.app.state.engine, since_seq=since_seq, topics=topic_set, limit=limit
        )


def _ws_routes(app: FastAPI) -> None:

    @app.websocket("/ws/blackboard")
    async def ws_blackboard(ws: WebSocket) -> None:
        """Stream `BlackboardMessage`s (the `message` of each envelope) as JSON.

        Query params: `since_seq` (replay outbox rows with seq > since_seq before going live;
        omit for live-only), `topics` (comma list, e.g. `hazard,graph`). Each frame is a
        single message with its `seq`."""
        since_q = ws.query_params.get("since_seq")
        since_seq = int(since_q) if since_q is not None else None
        topics_q = ws.query_params.get("topics")
        topics = set(topics_q.split(",")) if topics_q else None
        await ws.accept()
        b: Broadcaster = ws.app.state.broadcaster
        await b.start()
        client = b.attach(topics)
        last_seq = since_seq or 0
        try:
            if since_seq is not None:
                backlog = await asyncio.to_thread(
                    bus.read_outbox, ws.app.state.engine, since_seq=since_seq, topics=topics
                )
                for env in backlog:
                    await ws.send_text(env.message.model_dump_json())
                    last_seq = max(last_seq, env.message.seq)
            while True:
                env = await client.queue.get()
                if env.message.seq <= last_seq:
                    continue  # already sent during catch-up
                last_seq = env.message.seq
                await ws.send_text(env.message.model_dump_json())
        except WebSocketDisconnect:
            pass
        finally:
            b.detach(client)


app = create_app()


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "arco_blackboard.api:app",
        host="0.0.0.0",
        port=settings.blackboard_port,
        log_level=settings.arco_log_level.lower(),
    )


if __name__ == "__main__":
    main()
