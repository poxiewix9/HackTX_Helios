"""Cartographer agent: hazards -> road_edges state.

Deterministic. For every hazard event it:
  1. buffers expanding hazards by `velocity x (horizon + elapsed)` (penalties.yaml),
  2. asks the blackboard which edges the (buffered) geometry touches and by how much,
  3. decides sever / penalize / capacity / ignore per edge from the class policy,
  4. recomputes the *combined* state of every affected edge from ALL hazards still
     linked to it (so updates, resolves and TTL expiry restore edges correctly),
  5. writes edge states + links + a `graph.updated` in one blackboard transaction and
  6. appends an `AgentEvent` whose `reasoning` says what it did and why.

It never writes `road_edges` directly; everything goes through `BlackboardService`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from arco_agents.geo import buffer_m, to_hazard_geometry, to_shape
from arco_agents.logging import get_logger
from arco_agents.penalties import (
    SEVER_PENALTY,
    Decision,
    EdgeState,
    PenaltyPolicy,
    combine,
)
from arco_blackboard.service import NO_SCENARIO, BlackboardService
from arco_schemas import (
    AgentEvent,
    EdgeHazardLink,
    EdgeStatus,
    GraphUpdatedEvent,
    Hazard,
    HazardEvent,
    RoadEdge,
)
from arco_schemas.geo import HazardGeometry

log = get_logger("agents.cartographer")


@dataclass
class CartographerResult:
    """What one hazard event did. `graph_event` is None when nothing changed."""

    hazard_id: UUID
    kind: str
    severed: list[int] = field(default_factory=list)
    penalized: list[int] = field(default_factory=list)
    restored: list[int] = field(default_factory=list)
    capacity_reduced: list[int] = field(default_factory=list)
    ignored: int = 0
    buffer_m: float = 0.0
    graph_event: GraphUpdatedEvent | None = None
    agent_event: AgentEvent | None = None
    compute_ms: float = 0.0


class Cartographer:
    def __init__(self, service: BlackboardService, policy: PenaltyPolicy) -> None:
        self.service = service
        self.policy = policy

    # ------------------------------------------------------------------ public

    def on_hazard_event(self, event: HazardEvent) -> CartographerResult:
        hazard = event.hazard
        if event.type in ("hazard.resolved", "hazard.disputed") or hazard.status != "active":
            return self.release(hazard, scenario_t=event.scenario_t, reason=event.type)
        return self.apply(hazard, scenario_t=event.scenario_t, elapsed_s=0.0)

    def apply(self, hazard: Hazard, *, scenario_t: float, elapsed_s: float) -> CartographerResult:
        """(Re)apply one active hazard: compute its links, then recompute affected edges."""
        t0 = time.perf_counter()
        policy = self.policy.for_class(hazard.hazard_class)
        buffer = 0.0
        geom: HazardGeometry = hazard.geom
        if policy.buffer is not None:
            buffer = policy.buffer.distance_m(hazard.velocity_m_s, elapsed_s)
            geom = to_hazard_geometry(buffer_m(to_shape(hazard.geom), buffer))

        overlaps = self.service.edges_intersecting(geom)
        now = datetime.now(UTC)
        new_links: list[EdgeHazardLink] = []
        ignored = 0
        for ov in overlaps:
            decision, multiplier, _cap = policy.decide(hazard, ov.overlap_fraction)
            if decision == "ignore":
                ignored += 1
                continue
            new_links.append(
                EdgeHazardLink(
                    edge_id=ov.edge_id,
                    hazard_id=hazard.id,
                    overlap_fraction=ov.overlap_fraction,
                    penalty_applied=SEVER_PENALTY if decision == "sever" else multiplier,
                    applied_at=now,
                )
            )

        previous = self.service.links_for_hazard(hazard.id)
        affected = sorted(
            {link.edge_id for link in previous} | {link.edge_id for link in new_links}
        )
        result = self._recompute(
            hazard,
            affected,
            replacing_links=new_links,
            scenario_t=scenario_t,
            kind="hazard.applied",
        )
        result.ignored = ignored
        result.buffer_m = buffer
        result.compute_ms = (time.perf_counter() - t0) * 1000
        self._log_event(result, hazard, scenario_t, detail=f"buffer={buffer:.0f}m")
        return result

    def release(self, hazard: Hazard, *, scenario_t: float, reason: str) -> CartographerResult:
        """Hazard resolved/disputed/expired: drop its links and restore its edges."""
        t0 = time.perf_counter()
        previous = self.service.links_for_hazard(hazard.id)
        affected = sorted({link.edge_id for link in previous})
        result = self._recompute(
            hazard, affected, replacing_links=[], scenario_t=scenario_t, kind=reason
        )
        result.compute_ms = (time.perf_counter() - t0) * 1000
        self._log_event(result, hazard, scenario_t, detail=f"released because {reason}")
        return result

    def expire(
        self, *, now: datetime | None = None, scenario_t: float = NO_SCENARIO
    ) -> list[CartographerResult]:
        """Resolve hazards past their TTL (blackboard emits `hazard.resolved`) and restore
        their edges. Returns one result per expired hazard."""
        results: list[CartographerResult] = []
        for ev in self.service.expire_hazards(now=now, scenario_t=scenario_t):
            results.append(self.release(ev.hazard, scenario_t=scenario_t, reason="hazard.expired"))
        return results

    def regrow(self, *, scenario_t: float, now: datetime | None = None) -> list[CartographerResult]:
        """Re-apply active *expanding* hazards so their buffers grow with elapsed time."""
        now = now or datetime.now(UTC)
        out: list[CartographerResult] = []
        for hazard in self.service.list_hazards(status="active"):
            policy = self.policy.for_class(hazard.hazard_class)
            if policy.buffer is None:
                continue
            elapsed = max(0.0, (now - hazard.last_seen).total_seconds())
            # A hazard with zero spread velocity has a constant buffer: nothing to regrow
            # (avoids a no-op recompute on every tick).
            if policy.buffer.distance_m(hazard.velocity_m_s, 0.0) >= policy.buffer.distance_m(
                hazard.velocity_m_s, elapsed
            ):
                continue
            r = self.apply(hazard, scenario_t=scenario_t, elapsed_s=elapsed)
            if r.graph_event is not None:
                out.append(r)
        return out

    # ----------------------------------------------------------------- internals

    def _recompute(
        self,
        hazard: Hazard,
        affected: list[int],
        *,
        replacing_links: list[EdgeHazardLink],
        scenario_t: float,
        kind: str,
    ) -> CartographerResult:
        result = CartographerResult(hazard_id=hazard.id, kind=kind)
        if not affected:
            return result

        edges = {e.id: e for e in self.service.list_edges(ids=affected)}
        # Links from OTHER hazards still active on these edges + this hazard's new links.
        other_links = [
            link for link in self.service.links_for_edges(affected) if link.hazard_id != hazard.id
        ]
        hazards: dict[UUID, Hazard] = {hazard.id: hazard}
        for link in other_links:
            if link.hazard_id not in hazards:
                h = self.service.get_hazard(link.hazard_id)
                if h is not None:
                    hazards[link.hazard_id] = h

        per_edge_links: dict[int, list[EdgeHazardLink]] = {eid: [] for eid in affected}
        for link in other_links + replacing_links:
            per_edge_links.setdefault(link.edge_id, []).append(link)

        updates: list[tuple[int, EdgeStatus, float, float | None]] = []
        capacity_factors: dict[int, float] = {}
        for eid in affected:
            edge = edges.get(eid)
            if edge is None:
                continue
            state = self._edge_state(per_edge_links[eid], hazards)
            plan = plan_edge_update(edge, state)
            if plan is None:
                continue
            status, cost, cap = plan
            updates.append((eid, status, cost, None))
            if cap is not None:
                capacity_factors[eid] = cap
                result.capacity_reduced.append(eid)
            {"severed": result.severed, "penalized": result.penalized, "open": result.restored}[
                status
            ].append(eid)

        events = self.service.apply_edge_updates(
            updates,
            links=replacing_links,
            unlink_hazard_ids=[hazard.id],
            capacity_factors=capacity_factors or None,
            scenario_t=scenario_t,
        )
        if events is not None:
            result.graph_event = events[1]
        return result

    def _edge_state(self, links: list[EdgeHazardLink], hazards: dict[UUID, Hazard]) -> EdgeState:
        decisions: list[tuple[Decision, float, float]] = []
        for link in links:
            h = hazards.get(link.hazard_id)
            if h is None or h.status != "active":
                continue
            decisions.append(self.policy.for_class(h.hazard_class).decide(h, link.overlap_fraction))
        return combine(decisions)

    def _log_event(
        self, result: CartographerResult, hazard: Hazard, scenario_t: float, *, detail: str
    ) -> None:
        reasoning = (
            f"{hazard.hazard_class.value} (conf {hazard.confidence:.2f}, sev "
            f"{hazard.severity:.2f}) {result.kind}: severed {len(result.severed)}, "
            f"penalized {len(result.penalized)}, restored {len(result.restored)}, "
            f"capacity-reduced {len(result.capacity_reduced)}, ignored {result.ignored} "
            f"low-overlap/low-confidence edges; {detail}; policy {self.policy.source.name}"
        )
        ev = AgentEvent(
            id=uuid4(),
            agent="cartographer",
            kind=result.kind if result.graph_event else f"{result.kind}.noop",
            reasoning=reasoning,
            scenario_t=scenario_t,
            wall_t=datetime.now(UTC),
            hazard_ids=[hazard.id],
            edge_ids=result.severed + result.penalized + result.restored,
            data={
                "severed": len(result.severed),
                "penalized": len(result.penalized),
                "restored": len(result.restored),
                "capacity_reduced": len(result.capacity_reduced),
                "ignored": result.ignored,
                "buffer_m": round(result.buffer_m, 1),
                "compute_ms": round(result.compute_ms, 2),
                "graph_version": result.graph_event.graph_version if result.graph_event else None,
            },
        )
        self.service.append_agent_event(ev)
        result.agent_event = ev
        log.info("cartographer", **{k: v for k, v in ev.data.items()}, hazard=str(hazard.id))


def plan_edge_update(
    edge: RoadEdge, state: EdgeState
) -> tuple[EdgeStatus, float, float | None] | None:
    """Pure: new (status, current_cost, capacity_factor|None) for an edge given its combined
    hazard state, or None when nothing changes. current_cost stays finite when severed
    (the edge is excluded from routing by status, not by cost)."""
    status: EdgeStatus = (
        "severed" if state.severed else "penalized" if state.multiplier > 1.0 else "open"
    )
    cost = edge.base_cost * (1.0 if state.severed else state.multiplier)
    cap_changed = abs(state.capacity_factor - edge.capacity_factor) > 1e-6
    if status == edge.status and abs(cost - edge.current_cost) <= 1e-6 and not cap_changed:
        return None
    return status, cost, (state.capacity_factor if cap_changed else None)


def edge_cost_for_routing(edge: RoadEdge) -> float | None:
    """Navigator helper: None when the edge must be excluded, else its current cost."""
    return None if edge.status == "severed" else edge.current_cost
