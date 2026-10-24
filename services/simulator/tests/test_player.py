"""Player unit tests against an in-memory fake of the blackboard HTTP client: ordering,
speed, scrubbing, signed community events, clock publication, chaos."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from arco_agents.reports import verify_report
from arco_schemas import (
    HAZARD_CLASSES,
    ClockEvent,
    CommunityReport,
    Hazard,
    HazardClass,
    HazardEvent,
    HazardStatus,
    NetworkEvent,
    ReportEvent,
    RoadEdge,
    Route,
)
from arco_sim.player import SIM_NS, Player, PlayerConfig
from arco_sim.scenario_io import load_scenario

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"
SCENARIOS_DIR = DATA_DIR / "scenarios"


@dataclass
class FakeBlackboard:
    """Records what the player sends. Implements the subset of BlackboardClient used."""

    hazards: dict[UUID, Hazard] = field(default_factory=dict)
    reports: list[CommunityReport] = field(default_factory=list)
    clocks: list[dict[str, Any]] = field(default_factory=list)
    network: list[tuple[bool, str | None, float]] = field(default_factory=list)
    ingests: list[tuple[str, str]] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    seq: int = 0

    def _next(self) -> int:
        self.seq += 1
        return self.seq

    def ingest_graph(
        self, source: str, ref: str, *, network_type: str | None = None
    ) -> dict[str, Any]:
        self.ingests.append((source, ref))
        self.calls.append("ingest")
        return {
            "source": source,
            "ref": ref,
            "nodes": 10,
            "edges": 20,
            "bbox": [0, 0, 1, 1],
            "graph_version": len(self.ingests),
        }

    def upsert_hazard(self, hazard: Hazard, *, scenario_t: float) -> HazardEvent:
        created = hazard.id not in self.hazards
        self.hazards[hazard.id] = hazard
        self.calls.append(f"upsert:{hazard.hazard_class}@{scenario_t:g}")
        return HazardEvent(
            type="hazard.created" if created else "hazard.updated",
            scenario_t=scenario_t,
            wall_t=datetime.now(UTC),
            seq=self._next(),
            hazard=hazard,
        )

    def set_hazard_status(
        self, hazard_id: UUID, status: HazardStatus, *, scenario_t: float
    ) -> HazardEvent | None:
        h = self.hazards.get(hazard_id)
        if h is None:
            return None
        h = self.hazards[hazard_id] = h.model_copy(update={"status": status})
        self.calls.append(f"status:{status}@{scenario_t:g}")
        return HazardEvent(
            type="hazard.resolved",
            scenario_t=scenario_t,
            wall_t=datetime.now(UTC),
            seq=self._next(),
            hazard=h,
        )

    def list_hazards(self, *, status: HazardStatus | None = None) -> list[Hazard]:
        return [h for h in self.hazards.values() if status is None or h.status == status]

    def post_report(self, report: CommunityReport, *, scenario_t: float) -> ReportEvent:
        self.reports.append(report)
        self.calls.append(f"report:{report.report_type}@{scenario_t:g}")
        return ReportEvent(
            type="report.created",
            scenario_t=scenario_t,
            wall_t=datetime.now(UTC),
            seq=self._next(),
            report=report,
        )

    def post_clock(self, **kw: Any) -> ClockEvent:
        self.clocks.append(kw)
        return ClockEvent(type="clock.tick", wall_t=datetime.now(UTC), seq=self._next(), **kw)

    def post_network(
        self, *, cellular_up: bool, reason: str | None, scenario_t: float
    ) -> NetworkEvent:
        self.network.append((cellular_up, reason, scenario_t))
        return NetworkEvent(
            type="network.cellular",
            scenario_t=scenario_t,
            wall_t=datetime.now(UTC),
            seq=self._next(),
            cellular_up=cellular_up,
            reason=reason,
        )

    def list_edges(self, *, status: str | None = None) -> list[RoadEdge]:
        return []

    def list_routes(self) -> list[Route]:
        return []


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def world() -> tuple[Player, FakeBlackboard, FakeClock]:
    bb = FakeBlackboard()
    clk = FakeClock()
    player = Player(bb, None, config=PlayerConfig.load(), clock=clk)  # type: ignore[arg-type]
    player.load(load_scenario(SCENARIOS_DIR / "lahaina_2023.json"))
    return player, bb, clk


def test_load_resets_and_ingests(world: tuple[Player, FakeBlackboard, FakeClock]) -> None:
    player, bb, _ = world
    assert bb.ingests[0][0] == "graphml_file" and bb.ingests[0][1].endswith("lahaina_drive.graphml")
    st = player.state()
    assert st.loaded and not st.playing and st.t == 0 and st.timeline_total == 13
    assert st.label == "Simulated on real event geometry"
    assert bb.clocks[-1]["playing"] is False and bb.clocks[-1]["scenario_id"] == "lahaina_2023"
    # reload: leftover active hazards are resolved through the API first
    player.advance(100)
    assert bb.list_hazards(status="active")
    player.load(load_scenario(SCENARIOS_DIR / "lahaina_2023.json"))
    assert bb.list_hazards(status="active") == [] and len(bb.ingests) == 2


def test_advance_fires_in_order_exactly_once(
    world: tuple[Player, FakeBlackboard, FakeClock],
) -> None:
    player, bb, _ = world
    assert player.advance(29.9) == 1 and not bb.hazards  # only the t=0 note fired
    assert player.advance(0.1) == 1  # fire-east appears at t=30
    (hz,) = bb.hazards.values()
    assert hz.hazard_class == "fire_front" and hz.source == "simulated" and hz.velocity_m_s == 1.5
    assert hz.id == uuid.uuid5(SIM_NS, "lahaina_2023:fire-east")
    assert hz.severity == 1.0  # HAZARD_CLASSES default, not invented
    fired = player.advance(
        500
    )  # 120 update, 240 smoke, 300 cell down, 360 cam, 400/430 pins, 480 debris
    assert fired == 7
    assert [c for c in bb.calls if not c.startswith("ingest")] == [
        "upsert:fire_front@30",
        "upsert:fire_front@120",
        "upsert:smoke@240",
        "report:hazard_pin@400",
        "report:hazard_pin@430",
        "upsert:debris@480",
    ]
    assert bb.network == [
        (
            False,
            "Cell sites lose power / burn; 911 and alerts degrade (MPD after-action report).",
            300.0,
        )
    ]
    assert player.cellular_up is False
    assert len(bb.hazards) == 3  # fire-east (updated in place), smoke, debris
    assert player.advance(2000) > 0 and player.t == 1800 and not player.playing
    assert player.hazards["debris-front-st"].active is False  # resolved at 1500
    assert bb.hazards[player.hazards["debris-front-st"].blackboard_id].status == "resolved"


def test_wall_clock_speed_and_pause(world: tuple[Player, FakeBlackboard, FakeClock]) -> None:
    player, bb, clk = world
    player.set_speed(10.0)
    player.play()
    assert (
        player.state().playing
        and bb.clocks[-1]["playing"] is True
        and bb.clocks[-1]["speed"] == 10.0
    )
    clk.now += 2.0  # 2 wall seconds = 20 scenario seconds
    player.tick()
    assert player.t == pytest.approx(20.0)
    clk.now += 1.5
    player.tick()
    assert player.t == pytest.approx(35.0) and len(bb.hazards) == 1
    player.pause()
    clk.now += 100
    player.tick()
    assert player.t == pytest.approx(35.0) and not player.state().playing
    # clock ticks are rate limited while playing (2 Hz) but always sent on state changes
    n = len(bb.clocks)
    player.play()
    for _ in range(10):
        clk.now += 0.1
        player.tick()
    assert 1 <= len(bb.clocks) - n <= 4
    assert all(c["scenario_t"] <= player.t for c in bb.clocks)


def test_seek_reconciles_hazards_and_network(
    world: tuple[Player, FakeBlackboard, FakeClock],
) -> None:
    player, bb, _ = world
    player.seek(700)
    active = {h.hazard_class for h in bb.list_hazards(status="active")}
    assert active == {"fire_front", "smoke", "debris"}
    assert player.state().cellular_up is False and bb.network[-1][0] is False
    assert len(bb.reports) == 2  # skipped pins were fired on the forward seek
    fire = bb.hazards[player.hazards["fire-east"].blackboard_id]
    assert fire.geom.coordinates[0][0][0] == pytest.approx(-156.678)  # the t=600 geometry
    # backward: hazards that should not exist yet are resolved, network restored
    player.seek(100)
    assert {h.hazard_class for h in bb.list_hazards(status="active")} == {"fire_front"}
    restored_up, _reason, _t = bb.network[-1]
    assert player.state().cellular_up is True and restored_up is True
    assert player.timeline_cursor == 2 and player.community_cursor == 0
    # forward again re-fires the pins as new reports (ids differ)
    player.advance(400)
    assert len(bb.reports) == 4 and len({r.id for r in bb.reports}) == 4
    # end of scenario: resolved by timeline, replay restarts from 0
    player.seek(1800)
    assert bb.hazards[player.hazards["debris-front-st"].blackboard_id].status == "resolved"
    player.play()
    assert player.t == 0 and player.playing


def test_community_events_are_signed_by_stable_distinct_keys(
    world: tuple[Player, FakeBlackboard, FakeClock],
) -> None:
    player, bb, _ = world
    player.advance(1500)
    reps = bb.reports
    assert [r.report_type for r in reps] == [
        "hazard_pin",
        "hazard_pin",
        "confirm",
        "all_clear",
        "all_clear",
    ]
    assert all(verify_report(r) == (True, "ok") for r in reps)
    by_reporter: dict[str, set[str]] = {}
    for ce, r in zip(player.scenario.community_events, reps, strict=True):  # type: ignore[union-attr]
        by_reporter.setdefault(ce.reporter, set()).add(r.reporter_hash)
    assert all(len(v) == 1 for v in by_reporter.values())  # one key per phone
    assert len({next(iter(v)) for v in by_reporter.values()}) == len(by_reporter)  # distinct phones
    assert reps[2].target_hazard_id == player.hazards["debris-front-st"].blackboard_id
    # deterministic across loads (reruns reproduce the same reporter_hash)
    bb2 = FakeBlackboard()
    p2 = Player(bb2, None, clock=FakeClock())  # type: ignore[arg-type]
    p2.load(load_scenario(SCENARIOS_DIR / "lahaina_2023.json"))
    p2.advance(500)
    assert bb2.reports[0].reporter_hash == reps[0].reporter_hash
    assert bb2.reports[0].id != reps[0].id


def test_chaos_hazard_and_cellular(world: tuple[Player, FakeBlackboard, FakeClock]) -> None:
    player, bb, _ = world
    from arco_schemas.geo import Polygon  # noqa: PLC0415

    geom = Polygon(
        type="Polygon",
        coordinates=[
            [
                [-156.68, 20.87],
                [-156.679, 20.87],
                [-156.679, 20.871],
                [-156.68, 20.871],
                [-156.68, 20.87],
            ]
        ],
    )
    hz = player.inject_hazard(
        geom=geom, hazard_class=HazardClass.FLOODED_ROAD, confidence=0.8, note="chaos test"
    )
    assert bb.hazards[hz.id].source == "simulated"
    assert bb.hazards[hz.id].severity == HAZARD_CLASSES[HazardClass.FLOODED_ROAD].default_severity
    player.set_cellular(False, reason="chaos button")
    assert bb.network[-1] == (False, "chaos button", 0.0) and player.cellular_up is False
    assert player.state().log[-1].kind == "network.cellular"
    # The fake blackboard never severs edges, so every recorded expectation is "missing".
    check = player.check_expected()
    assert check.expected and not check.ok and check.missing == check.expected
