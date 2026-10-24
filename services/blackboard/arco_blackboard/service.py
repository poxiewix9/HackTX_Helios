"""BlackboardService: the one write path. Every mutation persists rows *and* publishes
the corresponding typed event in the same transaction (outbox) so nothing bypasses the
agents. Vision, simulator and agents all go through this class (or its HTTP mirror)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Engine

from arco_blackboard import repo
from arco_blackboard.bus import Publisher
from arco_blackboard.db import session_scope
from arco_schemas import (
    AgentEvent,
    AgentEventMessage,
    ClockEvent,
    CommunityReport,
    EdgeEvent,
    EdgeHazardLink,
    EdgeOverlap,
    EdgeStatus,
    GraphUpdatedEvent,
    Hazard,
    HazardEvent,
    HazardStatus,
    NetworkEvent,
    ReportEvent,
    RoadEdge,
    Route,
    RoutesUpdatedMessage,
    SafeZone,
    SafeZoneEvent,
)
from arco_schemas.geo import BBox, Geometry

NO_SCENARIO = -1.0


def _now() -> datetime:
    return datetime.now(UTC)


class BlackboardService:
    def __init__(self, engine: Engine, publisher: Publisher) -> None:
        self.engine = engine
        self.publisher = publisher

    # ------------------------------------------------------------------ hazards

    def upsert_hazard(self, hazard: Hazard, *, scenario_t: float = NO_SCENARIO) -> HazardEvent:
        with session_scope(self.engine) as s:
            stored, created = repo.upsert_hazard(s, hazard)
            if hazard.status == "resolved":
                kind = "hazard.resolved"
            elif hazard.status == "disputed":
                kind = "hazard.disputed"
            else:
                kind = "hazard.created" if created else "hazard.updated"
            msg = HazardEvent(type=kind, scenario_t=scenario_t, wall_t=_now(), seq=0, hazard=stored)  # type: ignore[arg-type]
            env = self.publisher.publish(s, msg)
        assert isinstance(env.message, HazardEvent)
        return env.message

    def set_hazard_status(
        self, hazard_id: UUID, status: HazardStatus, *, scenario_t: float = NO_SCENARIO
    ) -> HazardEvent | None:
        with session_scope(self.engine) as s:
            hazard = repo.set_hazard_status(s, hazard_id, status)
            if hazard is None:
                return None
            kind = {
                "resolved": "hazard.resolved",
                "disputed": "hazard.disputed",
                "active": "hazard.updated",
            }[status]
            msg = HazardEvent(type=kind, scenario_t=scenario_t, wall_t=_now(), seq=0, hazard=hazard)  # type: ignore[arg-type]
            env = self.publisher.publish(s, msg)
        assert isinstance(env.message, HazardEvent)
        return env.message

    def expire_hazards(
        self, *, now: datetime | None = None, scenario_t: float = NO_SCENARIO
    ) -> list[HazardEvent]:
        """Resolve every active hazard past `last_seen + ttl_s`."""
        now = now or _now()
        events: list[HazardEvent] = []
        with session_scope(self.engine) as s:
            for hazard in repo.expired_hazards(s, now):
                updated = repo.set_hazard_status(s, hazard.id, "resolved")
                if updated is None:
                    continue
                msg = HazardEvent(
                    type="hazard.resolved", scenario_t=scenario_t, wall_t=now, seq=0, hazard=updated
                )
                env = self.publisher.publish(s, msg)
                assert isinstance(env.message, HazardEvent)
                events.append(env.message)
        return events

    def get_hazard(self, hazard_id: UUID) -> Hazard | None:
        with session_scope(self.engine) as s:
            return repo.get_hazard(s, hazard_id)

    def list_hazards(
        self, *, bbox: BBox | None = None, status: HazardStatus | None = None, limit: int = 1000
    ) -> list[Hazard]:
        with session_scope(self.engine) as s:
            return repo.list_hazards(s, bbox=bbox, status=status, limit=limit)

    # -------------------------------------------------------------------- edges

    def list_edges(
        self,
        *,
        bbox: BBox | None = None,
        status: EdgeStatus | None = None,
        ids: Sequence[int] | None = None,
        limit: int | None = None,
    ) -> list[RoadEdge]:
        with session_scope(self.engine) as s:
            return repo.list_edges(s, bbox=bbox, status=status, ids=ids, limit=limit)

    def edges_intersecting(self, geom: Geometry) -> list[EdgeOverlap]:
        with session_scope(self.engine) as s:
            return repo.edges_intersecting(s, geom)

    def graph_version(self) -> int:
        with session_scope(self.engine) as s:
            return repo.graph_version(s)

    def apply_edge_updates(
        self,
        updates: Iterable[tuple[int, EdgeStatus, float, float | None]],
        *,
        links: Iterable[EdgeHazardLink] = (),
        unlink_hazard_ids: Iterable[UUID] = (),
        capacity_factors: Mapping[int, float] | None = None,
        scenario_t: float = NO_SCENARIO,
    ) -> tuple[EdgeEvent, GraphUpdatedEvent] | None:
        """Cartographer entry point: change edge states (and optionally capacity
        factors), record links, publish `edge.updated` + `graph.updated` in one
        transaction. Returns None if nothing changed."""
        with session_scope(self.engine) as s:
            for hid in unlink_hazard_ids:
                repo.delete_links_for_hazard(s, hid)
            repo.upsert_edge_hazard_links(s, links)
            if capacity_factors:
                repo.set_edge_capacity_factors(s, capacity_factors)
            changed = repo.update_edge_states(s, updates)
            if not changed:
                return None
            version = repo.graph_version(s)
            now = _now()
            edge_env = self.publisher.publish(
                s,
                EdgeEvent(
                    type="edge.updated",
                    scenario_t=scenario_t,
                    wall_t=now,
                    seq=0,
                    edges=changed,
                    graph_version=version,
                ),
            )
            graph_env = self.publisher.publish(
                s,
                GraphUpdatedEvent(
                    type="graph.updated",
                    scenario_t=scenario_t,
                    wall_t=now,
                    seq=0,
                    graph_version=version,
                    changed_edge_ids=[e.id for e in changed],
                    severed_count=sum(1 for e in changed if e.status == "severed"),
                    penalized_count=sum(1 for e in changed if e.status == "penalized"),
                ),
            )
        assert isinstance(edge_env.message, EdgeEvent)
        assert isinstance(graph_env.message, GraphUpdatedEvent)
        return edge_env.message, graph_env.message

    def links_for_hazard(self, hazard_id: UUID) -> list[EdgeHazardLink]:
        with session_scope(self.engine) as s:
            return repo.links_for_hazard(s, hazard_id)

    def links_for_edges(self, edge_ids: Sequence[int]) -> list[EdgeHazardLink]:
        with session_scope(self.engine) as s:
            return repo.links_for_edges(s, edge_ids)

    def set_edge_loads(self, loads: dict[int, float]) -> None:
        with session_scope(self.engine) as s:
            repo.set_edge_loads(s, loads)

    # ------------------------------------------------------------------ reports

    def add_report(
        self, report: CommunityReport, *, scenario_t: float = NO_SCENARIO
    ) -> ReportEvent:
        with session_scope(self.engine) as s:
            repo.insert_report(s, report)
            env = self.publisher.publish(
                s,
                ReportEvent(
                    type="report.created",
                    scenario_t=scenario_t,
                    wall_t=_now(),
                    seq=0,
                    report=report,
                ),
            )
        assert isinstance(env.message, ReportEvent)
        return env.message

    def update_report(
        self,
        report_id: UUID,
        *,
        trust_score: float | None = None,
        confirms: int | None = None,
        denies: int | None = None,
    ) -> CommunityReport | None:
        """Consensus writes its opinion back onto the report row (no event: reports are
        inputs, the resulting hazard changes are what the bus carries)."""
        with session_scope(self.engine) as s:
            return repo.update_report_counts(
                s, report_id, confirms=confirms, denies=denies, trust_score=trust_score
            )

    def reports_near(
        self, lon: float, lat: float, radius_m: float, *, since: datetime | None = None
    ) -> list[CommunityReport]:
        with session_scope(self.engine) as s:
            return repo.reports_near(s, lon, lat, radius_m, since=since)

    def list_reports(
        self, *, bbox: BBox | None = None, since: datetime | None = None, limit: int = 1000
    ) -> list[CommunityReport]:
        with session_scope(self.engine) as s:
            return repo.list_reports(s, bbox=bbox, since=since, limit=limit)

    # --------------------------------------------------------------- safe zones

    def upsert_safe_zone(self, zone: SafeZone, *, scenario_t: float = NO_SCENARIO) -> SafeZoneEvent:
        with session_scope(self.engine) as s:
            repo.upsert_safe_zone(s, zone)
            env = self.publisher.publish(
                s,
                SafeZoneEvent(
                    type="safe_zone.updated",
                    scenario_t=scenario_t,
                    wall_t=_now(),
                    seq=0,
                    safe_zone=zone,
                ),
            )
        assert isinstance(env.message, SafeZoneEvent)
        return env.message

    def list_safe_zones(self) -> list[SafeZone]:
        with session_scope(self.engine) as s:
            return repo.list_safe_zones(s)

    # ------------------------------------------------------------------- routes

    def publish_routes(
        self,
        routes: Sequence[Route],
        *,
        trigger: str,
        compute_ms: float,
        max_edge_load_ratio: float,
        scenario_t: float = NO_SCENARIO,
    ) -> RoutesUpdatedMessage:
        with session_scope(self.engine) as s:
            repo.replace_routes(s, routes)
            version = repo.graph_version(s)
            env = self.publisher.publish(
                s,
                RoutesUpdatedMessage(
                    scenario_t=scenario_t,
                    wall_t=_now(),
                    seq=0,
                    graph_version=version,
                    routes=list(routes),
                    trigger=trigger,  # type: ignore[arg-type]
                    compute_ms=compute_ms,
                    max_edge_load_ratio=max_edge_load_ratio,
                ),
            )
        assert isinstance(env.message, RoutesUpdatedMessage)
        return env.message

    def list_routes(self, *, crowd_group_id: str | None = None) -> list[Route]:
        with session_scope(self.engine) as s:
            return repo.list_routes(s, crowd_group_id=crowd_group_id)

    # ------------------------------------------------------------- agent events

    def append_agent_event(self, ev: AgentEvent) -> AgentEventMessage:
        with session_scope(self.engine) as s:
            repo.append_agent_event(s, ev)
            env = self.publisher.publish(
                s,
                AgentEventMessage(
                    type="agent.event", scenario_t=ev.scenario_t, wall_t=ev.wall_t, seq=0, event=ev
                ),
            )
        assert isinstance(env.message, AgentEventMessage)
        return env.message

    def list_agent_events(self, *, agent: str | None = None, limit: int = 200) -> list[AgentEvent]:
        with session_scope(self.engine) as s:
            return repo.list_agent_events(s, agent=agent, limit=limit)

    # ------------------------------------------------------- simulator signals

    def publish_clock(
        self,
        *,
        scenario_id: str | None,
        scenario_t: float,
        playing: bool,
        speed: float,
        duration_s: float | None,
    ) -> ClockEvent:
        with session_scope(self.engine) as s:
            env = self.publisher.publish(
                s,
                ClockEvent(
                    type="clock.tick",
                    scenario_t=scenario_t,
                    wall_t=_now(),
                    seq=0,
                    scenario_id=scenario_id,
                    playing=playing,
                    speed=speed,
                    duration_s=duration_s,
                ),
            )
        assert isinstance(env.message, ClockEvent)
        return env.message

    def publish_network(
        self, *, cellular_up: bool, reason: str | None, scenario_t: float = NO_SCENARIO
    ) -> NetworkEvent:
        with session_scope(self.engine) as s:
            env = self.publisher.publish(
                s,
                NetworkEvent(
                    type="network.cellular",
                    scenario_t=scenario_t,
                    wall_t=_now(),
                    seq=0,
                    cellular_up=cellular_up,
                    reason=reason,
                ),
            )
        assert isinstance(env.message, NetworkEvent)
        return env.message

    # -------------------------------------------------------------------- misc

    def table_counts(self) -> dict[str, int]:
        with session_scope(self.engine) as s:
            return repo.table_counts(s)
