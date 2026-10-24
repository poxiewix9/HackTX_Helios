"""LangGraph pipeline: hazard -> cartographer -> navigator in one pass, de-duplication of
the Cartographer's own graph.updated, and fault isolation between agents."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from shapely.geometry import LineString as ShapelyLine

from arco_agents.cartographer import Cartographer
from arco_agents.explain import DeterministicExplainer
from arco_agents.navigator import Navigator, NavigatorResult
from arco_agents.penalties import PenaltyPolicy
from arco_agents.pipeline import Pipeline, build_pipeline
from arco_agents.population import Population
from arco_agents.routing_config import RoutingConfig
from arco_blackboard.bus import read_outbox
from arco_blackboard.service import BlackboardService
from arco_blackboard.testing import make_hazard, square
from arco_schemas import BusEnvelope, GraphUpdatedEvent, HazardEvent

DEMO_POPULATION = Path(__file__).resolve().parents[3] / "data/samples/lahaina/demo_population.json"


def _env(message: HazardEvent | GraphUpdatedEvent, topic: str) -> BusEnvelope:
    return BusEnvelope(
        topic=topic, published_at=datetime.now(UTC), publisher="tests", message=message
    )


@pytest.fixture
def pipeline(
    service: BlackboardService, policy: PenaltyPolicy, routing_config: RoutingConfig
) -> Pipeline:
    nav = Navigator(service, routing_config, DeterministicExplainer())
    nav.set_population(Population.load(DEMO_POPULATION))
    nav.load_graph()
    nav.recompute(trigger="manual", scenario_t=0.0)
    return Pipeline(Cartographer(service, policy), nav)


@pytest.mark.integration
def test_hazard_event_runs_both_agents_once(pipeline: Pipeline, service: BlackboardService) -> None:
    first = pipeline.navigator.last
    assert first is not None
    victim = max(first.routes, key=lambda r: r.length_m)
    edge = service.list_edges(ids=[victim.edge_ids[len(victim.edge_ids) // 2]])[0]
    mid = ShapelyLine(edge.geom.coordinates).interpolate(0.5, normalized=True)
    hazard = make_hazard(square(mid.x, mid.y, 0.0006), velocity_m_s=0.0)
    created = service.upsert_hazard(hazard, scenario_t=30.0)

    out = pipeline.run(_env(created, "hazard"))
    assert out["errors"] == []
    assert out["cartographer"] is not None and out["cartographer"]["severed"] >= 1
    assert out["navigator"] is not None and out["navigator"]["routes"] >= 1
    assert out["navigator"]["graph_version"] == out["cartographer"]["graph_version"]
    assert pipeline.navigator.last is not first  # rerouted
    assert all(edge.id not in r.edge_ids for r in pipeline.navigator.last.routes)  # type: ignore[union-attr]

    # The Cartographer's own graph.updated now arrives from the bus: must NOT reroute again.
    graph_env = next(
        e
        for e in read_outbox(service.engine, since_seq=0, topics={"graph"})
        if isinstance(e.message, GraphUpdatedEvent)
        and e.message.graph_version == out["cartographer"]["graph_version"]
    )
    before = pipeline.navigator.last
    out2 = pipeline.run(graph_env)
    assert out2["navigator"] == {"skipped": "already rerouted for this graph version"}
    assert pipeline.navigator.last is before
    assert pipeline.processed == 2


@pytest.mark.integration
def test_resolve_event_restores_and_reroutes(
    pipeline: Pipeline, service: BlackboardService
) -> None:
    first = pipeline.navigator.last
    assert first is not None
    victim = max(first.routes, key=lambda r: r.length_m)
    edge = service.list_edges(ids=[victim.edge_ids[len(victim.edge_ids) // 2]])[0]
    mid = ShapelyLine(edge.geom.coordinates).interpolate(0.5, normalized=True)
    hazard = make_hazard(square(mid.x, mid.y, 0.0006), velocity_m_s=0.0)
    pipeline.run(_env(service.upsert_hazard(hazard, scenario_t=1.0), "hazard"))
    assert service.list_edges(ids=[edge.id])[0].status == "severed"
    resolved = service.set_hazard_status(hazard.id, "resolved", scenario_t=2.0)
    assert resolved is not None
    out = pipeline.run(_env(resolved, "hazard"))
    assert out["cartographer"]["restored"] >= 1 and out["navigator"]["routes"] >= 1  # type: ignore[index]
    assert service.list_edges(ids=[edge.id])[0].status == "open"


@pytest.mark.integration
def test_crashing_navigator_does_not_stop_cartographer(
    pipeline: Pipeline, service: BlackboardService, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_ev: GraphUpdatedEvent) -> NavigatorResult | None:
        raise RuntimeError("navigator exploded")

    monkeypatch.setattr(pipeline.navigator, "on_graph_updated", boom)
    pipeline.compiled = build_pipeline(pipeline.cartographer, pipeline.navigator)
    edge = max(service.list_edges(), key=lambda e: e.length_m)
    mid = ShapelyLine(edge.geom.coordinates).interpolate(0.5, normalized=True)
    hazard = make_hazard(square(mid.x, mid.y, 0.0008), velocity_m_s=0.0)
    out = pipeline.run(_env(service.upsert_hazard(hazard), "hazard"))
    assert out["cartographer"] is not None and out["cartographer"]["severed"] >= 1
    assert out["navigator"] is None
    assert out["errors"] and "navigator exploded" in out["errors"][0]
    assert service.list_edges(ids=[edge.id])[0].status == "severed"  # cartographer's work stuck
    assert pipeline.errors  # surfaced on /health
