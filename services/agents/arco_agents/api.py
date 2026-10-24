"""Agents service HTTP/WS surface (port AGENTS_PORT, default 8003).

GET  /health                 runtime state (graph version, population, bus mode, errors)
GET  /routes                 current routes from the blackboard
GET  /agent-events           recent reasoning trail
POST /navigator/population   load PopulationCells + SafeZoneSpecs and recompute
POST /navigator/recompute    force a reroute (trigger=manual)
WS   /ws/routes              streams `routes.updated` and `agent.event` messages
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from arco_agents import __version__
from arco_agents.logging import get_logger
from arco_agents.population import Population
from arco_agents.runner import AgentsRuntime
from arco_agents.settings import AgentsSettings, get_settings
from arco_blackboard import bus
from arco_blackboard.settings import Settings as BlackboardSettings
from arco_schemas import AgentEvent, Route

log = get_logger("agents.api")

WS_TOPICS = {"routes", "agent"}


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    version: str
    bus_mode: str
    slm_enabled: bool
    graph_version: int | None
    graph_nodes: int | None
    graph_edges: int | None
    population_cells: int
    safe_zones: int
    routes: int
    last_compute_ms: float | None
    last_max_edge_load_ratio: float | None
    processed_events: int
    ticks: int
    scenario_t: float
    recent_errors: list[str]


def runtime(request: Request) -> AgentsRuntime:
    return request.app.state.runtime  # type: ignore[no-any-return]


def create_app(settings: AgentsSettings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        rt = AgentsRuntime.build(settings)
        app.state.runtime = rt
        app.state.settings = settings
        await rt.start()
        log.info("agents api up", bus=rt.bus_mode, slm=settings.arco_slm_enabled)
        try:
            yield
        finally:
            await rt.stop()

    app = FastAPI(title="ARCO agents", version=__version__, lifespan=lifespan)

    @app.get("/health", response_model=HealthResponse)
    def health(request: Request) -> HealthResponse:
        rt = runtime(request)
        g = rt.navigator.graph
        last = rt.navigator.last
        pop = rt.navigator.population
        return HealthResponse(
            status="ok" if not rt.pipeline.errors[-5:] else "degraded",
            version=__version__,
            bus_mode=rt.bus_mode,
            slm_enabled=rt.settings.arco_slm_enabled,
            graph_version=g.version if g else None,
            graph_nodes=g.node_count if g else None,
            graph_edges=g.edge_count if g else None,
            population_cells=len(pop.population_cells) if pop else 0,
            safe_zones=len(pop.safe_zones) if pop else 0,
            routes=len(last.routes) if last else 0,
            last_compute_ms=round(last.compute_ms, 2) if last else None,
            last_max_edge_load_ratio=(
                round(last.assignment.max_edge_load_ratio, 3) if last and last.assignment else None
            ),
            processed_events=rt.pipeline.processed,
            ticks=rt.ticks,
            scenario_t=rt.scenario_t,
            recent_errors=rt.pipeline.errors[-5:],
        )

    @app.get("/routes", response_model=list[Route])
    def routes(request: Request, crowd_group_id: str | None = None) -> list[Route]:
        return runtime(request).service.list_routes(crowd_group_id=crowd_group_id)

    @app.get("/agent-events", response_model=list[AgentEvent])
    def agent_events(
        request: Request, agent: str | None = None, limit: int = 100
    ) -> list[AgentEvent]:
        return runtime(request).service.list_agent_events(agent=agent, limit=limit)

    @app.post("/navigator/population")
    async def set_population(request: Request, population: Population) -> dict[str, Any]:
        rt = runtime(request)

        def apply() -> dict[str, Any]:
            rt.navigator.set_population(population, scenario_t=rt.scenario_t)
            rt.navigator.load_graph()
            res = rt.navigator.recompute(trigger="manual", scenario_t=rt.scenario_t)
            return {"routes": len(res.routes), "compute_ms": round(res.compute_ms, 2)}

        return await asyncio.to_thread(apply)

    @app.post("/navigator/recompute")
    async def recompute(request: Request) -> dict[str, Any]:
        rt = runtime(request)
        if rt.navigator.population is None:
            raise HTTPException(status_code=409, detail="no population loaded")

        def apply() -> dict[str, Any]:
            res = rt.navigator.recompute(trigger="manual", scenario_t=rt.scenario_t)
            return {
                "routes": len(res.routes),
                "compute_ms": round(res.compute_ms, 2),
                "max_edge_load_ratio": (
                    round(res.assignment.max_edge_load_ratio, 3) if res.assignment else None
                ),
            }

        return await asyncio.to_thread(apply)

    @app.websocket("/ws/routes")
    async def ws_routes(ws: WebSocket) -> None:
        """One `routes.updated` or `agent.event` message per frame, straight from the bus."""
        await ws.accept()
        settings_: AgentsSettings = ws.app.state.settings
        bb = BlackboardSettings(database_url=settings_.database_url, redis_url=settings_.redis_url)
        sub = bus.make_subscriber(bb)
        try:
            async for env in sub.events(WS_TOPICS):
                await ws.send_text(env.message.model_dump_json())
        except WebSocketDisconnect:
            pass
        finally:
            await sub.close()

    @app.exception_handler(ValueError)
    async def _value_error(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    return app


app = create_app()


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "arco_agents.api:app",
        host="0.0.0.0",
        port=settings.agents_port,
        log_level=settings.arco_log_level.lower(),
    )


if __name__ == "__main__":
    main()
