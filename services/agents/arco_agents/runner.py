"""Agents runtime: subscribe to the blackboard bus, feed events through the LangGraph
pipeline, run the periodic tick (TTL expiry, buffer growth). Usable headless
(`arco-agents-run`) or embedded in the FastAPI app (`arco_agents.api`)."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from arco_agents.cartographer import Cartographer
from arco_agents.explain import DeterministicExplainer, Explainer, SlmExplainer
from arco_agents.logging import configure_logging, get_logger
from arco_agents.navigator import Navigator
from arco_agents.penalties import PenaltyPolicy
from arco_agents.pipeline import Pipeline
from arco_agents.population import Population
from arco_agents.routing_config import RoutingConfig
from arco_agents.settings import AgentsSettings, get_settings
from arco_blackboard import bus
from arco_blackboard.db import get_engine
from arco_blackboard.service import NO_SCENARIO, BlackboardService
from arco_blackboard.settings import Settings as BlackboardSettings
from arco_schemas import BusEnvelope, ClockEvent

log = get_logger("agents.runner")

TOPICS = {"hazard", "graph", "edge", "clock"}


def make_explainer(settings: AgentsSettings) -> Explainer:
    if settings.arco_slm_enabled:
        return SlmExplainer(
            settings.ollama_url, settings.arco_slm_model, settings.arco_slm_timeout_s
        )
    return DeterministicExplainer()


@dataclass
class AgentsRuntime:
    settings: AgentsSettings
    service: BlackboardService
    cartographer: Cartographer
    navigator: Navigator
    pipeline: Pipeline
    scenario_t: float = NO_SCENARIO
    bus_mode: str = "unknown"
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    ticks: int = 0
    _tasks: list[asyncio.Task[None]] = field(default_factory=list)
    _subscriber: bus.Subscriber | None = None

    @classmethod
    def build(cls, settings: AgentsSettings | None = None) -> AgentsRuntime:
        settings = settings or get_settings()
        configure_logging("agents")
        bb = BlackboardSettings(database_url=settings.database_url, redis_url=settings.redis_url)
        engine = get_engine(settings.database_url)
        publisher = bus.make_publisher(bb, publisher_name="agents")
        service = BlackboardService(engine, publisher)
        cartographer = Cartographer(service, PenaltyPolicy.load(settings.arco_penalties_path))
        navigator = Navigator(
            service, RoutingConfig.load(settings.arco_routing_path), make_explainer(settings)
        )
        rt = cls(
            settings=settings,
            service=service,
            cartographer=cartographer,
            navigator=navigator,
            pipeline=Pipeline(cartographer, navigator),
            bus_mode=publisher.mode,
        )
        if settings.arco_population_path is not None:
            rt.load_population(Path(settings.arco_population_path))
        return rt

    # ----------------------------------------------------------------- control

    def load_population(self, path: Path) -> None:
        pop = Population.load(path)
        self.navigator.set_population(pop, scenario_t=self.scenario_t)
        log.info(
            "population loaded",
            path=str(path),
            cells=len(pop.population_cells),
            zones=len(pop.safe_zones),
            people=pop.total_population(),
        )

    def initial_routes(self) -> None:
        """Compute a first route set if a population and a road graph are present."""
        if self.navigator.population is None:
            return
        try:
            self.navigator.load_graph()
            if self.navigator.graph is not None and self.navigator.graph.edge_count > 0:
                self.navigator.recompute(trigger="manual", scenario_t=self.scenario_t)
        except Exception as exc:
            log.warning("initial routing skipped", error=repr(exc))

    async def start(self) -> None:
        await asyncio.to_thread(self.initial_routes)
        self._tasks = [
            asyncio.create_task(self._consume(), name="agents-consume"),
            asyncio.create_task(self._tick(), name="agents-tick"),
        ]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._tasks = []
        if self._subscriber is not None:
            await self._subscriber.close()
            self._subscriber = None

    # -------------------------------------------------------------------- loops

    def handle(self, env: BusEnvelope) -> None:
        if isinstance(env.message, ClockEvent):
            self.scenario_t = env.message.scenario_t
            return
        # `graph.updated` emitted by our own Cartographer inside a hazard pipeline run is
        # de-duplicated by graph_version in the pipeline; versions from the tick
        # (expire/regrow) are new and do trigger a reroute.
        self.pipeline.run(env)

    async def _consume(self) -> None:
        bb = BlackboardSettings(
            database_url=self.settings.database_url, redis_url=self.settings.redis_url
        )
        backoff = 0.5
        while True:
            try:
                self._subscriber = bus.make_subscriber(bb)
                log.info(
                    "agents subscribed", subscriber=self._subscriber.name, topics=sorted(TOPICS)
                )
                async for env in self._subscriber.events(TOPICS):
                    backoff = 0.5
                    await asyncio.to_thread(self.handle, env)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning(
                    "bus subscription error; reconnecting", error=repr(exc), backoff_s=backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)

    def tick_once(self) -> None:
        self.ticks += 1
        self.cartographer.expire(scenario_t=self.scenario_t)
        self.cartographer.regrow(scenario_t=self.scenario_t)
        # graph.updated events emitted above flow back through the bus -> navigator

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(self.settings.arco_agents_tick_s)
            try:
                await asyncio.to_thread(self.tick_once)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("tick failed", error=repr(exc))


async def _run_forever() -> None:
    rt = AgentsRuntime.build()
    await rt.start()
    try:
        await asyncio.Event().wait()
    finally:
        await rt.stop()


def main() -> None:
    asyncio.run(_run_forever())


if __name__ == "__main__":
    main()
