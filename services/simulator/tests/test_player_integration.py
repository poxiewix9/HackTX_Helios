"""7.1 end to end: the player drives a *real* blackboard (in-process API over the test DB)
through HTTP only, and an in-process agents pipeline consumes the outbox — so hazards
travel scenario file -> POST /hazards -> bus -> Cartographer -> Navigator exactly as they
would in the compose stack. No SQL from the simulator side."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from arco_agents.cartographer import Cartographer
from arco_agents.consensus import Consensus, ConsensusPolicy
from arco_agents.explain import DeterministicExplainer
from arco_agents.navigator import Navigator
from arco_agents.penalties import PenaltyPolicy
from arco_agents.pipeline import Pipeline
from arco_agents.population import Population
from arco_agents.routing_config import RoutingConfig
from arco_agents.settings import DEFAULT_CONSENSUS, DEFAULT_PENALTIES, DEFAULT_ROUTING
from arco_blackboard import bus
from arco_blackboard.bus import make_publisher
from arco_blackboard.service import BlackboardService
from arco_blackboard.settings import get_settings as blackboard_settings
from arco_schemas import ClockEvent, NetworkEvent
from arco_sim.clients import BlackboardClient
from arco_sim.player import Player
from arco_sim.scenario_io import load_scenario

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"
SCENARIOS_DIR = DATA_DIR / "scenarios"


class InProcessAgents:
    """Pulls every new bus envelope out of the outbox and runs the LangGraph pipeline."""

    def __init__(self, engine: Engine, scenario_population: Population) -> None:
        self.engine = engine
        service = BlackboardService(
            engine, make_publisher(blackboard_settings(), "sim-test-agents")
        )
        nav = Navigator(service, RoutingConfig.load(DEFAULT_ROUTING), DeterministicExplainer())
        nav.set_population(scenario_population)
        self.pipeline = Pipeline(
            Cartographer(service, PenaltyPolicy.load(DEFAULT_PENALTIES)),
            nav,
            Consensus(service, ConsensusPolicy.load(DEFAULT_CONSENSUS)),
        )
        self.service = service
        self.seq = 0
        self.runs = 0

    def drain(self) -> int:
        """Run the pipeline until the outbox is quiescent (agents publish events too)."""
        total = 0
        while True:
            n = 0
            for env in bus.read_outbox(
                self.engine,
                since_seq=self.seq,
                topics={"hazard", "graph", "edge", "report", "clock"},
            ):
                self.seq = max(self.seq, env.message.seq)
                if env.topic == "graph" and self.pipeline.navigator.graph is None:
                    self.pipeline.navigator.load_graph()
                self.pipeline.run(env)
                n += 1
            total += n
            if n == 0:
                break
        self.runs += total
        return total


@pytest.mark.integration
def test_lahaina_plays_through_blackboard_and_agents(
    blackboard: TestClient, bb_client: BlackboardClient, engine: Engine
) -> None:
    sc = load_scenario(SCENARIOS_DIR / "lahaina_2023.json")
    player = Player(bb_client, None, clock=time.monotonic)
    player.load(sc)
    assert bb_client.graph_version() >= 1
    edges = bb_client.list_edges()
    assert len(edges) == 637 and all(e.status == "open" for e in edges)

    agents = InProcessAgents(
        engine, Population(population_cells=sc.population_cells, safe_zones=sc.safe_zones)
    )
    agents.drain()  # graph.ingested
    agents.pipeline.navigator.load_graph()
    base = agents.pipeline.navigator.recompute(trigger="manual", scenario_t=0.0)
    assert len(base.routes) >= 1
    base_edges = {eid for r in base.routes for eid in r.edge_ids}

    # --- t=30..240: fire front appears and grows -> edges severed, routes recomputed
    player.advance(250)
    assert agents.drain() >= 3
    severed = bb_client.list_edges(status="severed")
    assert severed, "fire_front polygons must sever road edges through the blackboard path"
    hazards = bb_client.list_hazards(status="active")
    assert {h.source for h in hazards} == {"simulated"}
    assert {h.hazard_class for h in hazards} == {"fire_front", "smoke"}
    assert all(h.stream == "macro" and h.camera_id == "tile-lahaina-0" for h in hazards)
    last = agents.pipeline.navigator.last
    assert last is not None and last.trigger == "graph.updated"

    # --- t=300: cellular down is a bus event (dashboard/mobile consume it)
    player.advance(60)
    agents.drain()
    net = [e for e in bus.read_outbox(engine, since_seq=0, topics={"network"})]
    assert net and net[-1].message.type == "network.cellular"
    assert isinstance(net[-1].message, NetworkEvent) and net[-1].message.cellular_up is False

    # --- t=400/430: the smoke false-negative. Vision is blind at Front St; two phones pin
    # smoke; the Consensus writes a community hazard and the graph reacts.
    player.advance(130)  # t=440
    agents.drain()
    reports = blackboard.get("/reports").json()
    assert len(reports) == 2 and len({r["reporter_hash"] for r in reports}) == 2
    community = [h for h in bb_client.list_hazards(status="active") if h.source == "community"]
    assert len(community) == 1 and community[0].hazard_class == "smoke"
    kinds = [
        e["kind"] for e in blackboard.get("/agent-events", params={"agent": "consensus"}).json()
    ]
    assert "community.hazard.created" in kinds and "report.pending" in kinds
    penal = bb_client.list_edges(status="penalized")
    assert penal, "community smoke hazard must penalize Front St through the Cartographer"

    # --- play the rest: debris appears and is later resolved by the timeline + all_clear
    player.advance(2000)
    assert player.t == sc.duration_s and not player.playing
    agents.drain()
    debris_id = player.hazards["debris-front-st"].blackboard_id
    debris = blackboard.get(f"/hazards/{debris_id}").json()
    assert debris["status"] == "resolved"
    final_severed = {e.id for e in bb_client.list_edges(status="severed")}
    assert final_severed & base_edges, "the fire must cut at least one edge of the original routes"
    routes = bb_client.list_routes()
    assert routes and all(not (set(r.edge_ids) & final_severed) for r in routes)
    # the scenario's recorded expectation (if any) holds
    chk = player.check_expected()
    assert chk.ok, chk.missing
    # the whole trail is explained
    agent_kinds = {
        e["kind"] for e in blackboard.get("/agent-events", params={"limit": 2000}).json()
    }
    assert {"hazard.applied", "routes.recomputed", "community.hazard.created"} <= agent_kinds
    clocks = bus.read_outbox(engine, since_seq=0, topics={"clock"})
    assert clocks and isinstance(clocks[-1].message, ClockEvent)
    assert clocks[-1].message.scenario_t == sc.duration_s
    assert player.last_error is None


@pytest.mark.integration
def test_seek_against_real_blackboard(bb_client: BlackboardClient) -> None:
    sc = load_scenario(SCENARIOS_DIR / "joplin_2011.json")
    player = Player(bb_client, None, clock=time.monotonic)
    player.load(sc)
    assert len(bb_client.list_edges()) == 4926
    player.seek(200)
    active = bb_client.list_hazards(status="active")
    assert {h.hazard_class for h in active} == {"tornado_swath"}
    assert active[0].velocity_m_s == 0.0  # the t=180 update (static) is what t=200 prescribes
    player.seek(50)
    active = bb_client.list_hazards(status="active")
    assert len(active) == 1 and active[0].velocity_m_s == 12.0
    player.seek(0)
    assert bb_client.list_hazards(status="active") == []
    assert player.last_error is None
