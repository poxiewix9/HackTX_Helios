"""Repository: all SQL against the blackboard, returning wire models.

Pure data access; no event publishing here (see `service.py`). Agents in the same
Python workspace may import this directly; everything else goes through the API.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from geoalchemy2 import Geography
from geoalchemy2 import functions as gf
from sqlalchemy import ColumnElement, cast, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from arco_blackboard import convert
from arco_blackboard.models import (
    AgentEventRow,
    CommunityReportRow,
    EdgeHazardLinkRow,
    GraphStateRow,
    HazardRow,
    RoadEdgeRow,
    RouteRow,
    SafeZoneRow,
)
from arco_schemas import (
    AgentEvent,
    CommunityReport,
    EdgeHazardLink,
    EdgeOverlap,
    EdgeStatus,
    Hazard,
    HazardStatus,
    RoadEdge,
    Route,
    SafeZone,
)
from arco_schemas.geo import BBox, Geometry

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _bbox_envelope(bbox: Sequence[float]) -> ColumnElement[Any]:
    """`ST_MakeEnvelope(min_lon, min_lat, max_lon, max_lat, 4326)`."""
    if len(bbox) != 4:
        msg = "bbox must be [min_lon, min_lat, max_lon, max_lat]"
        raise ValueError(msg)
    return gf.ST_MakeEnvelope(bbox[0], bbox[1], bbox[2], bbox[3], 4326)


def parse_bbox(value: str) -> BBox:
    """'min_lon,min_lat,max_lon,max_lat' -> list[float]."""
    parts = [float(p) for p in value.split(",")]
    if len(parts) != 4:
        msg = "bbox must have 4 comma-separated numbers"
        raise ValueError(msg)
    if parts[0] >= parts[2] or parts[1] >= parts[3]:
        msg = "bbox min must be < max"
        raise ValueError(msg)
    return parts


# ---------------------------------------------------------------------------
# hazards
# ---------------------------------------------------------------------------


def upsert_hazard(session: Session, hazard: Hazard) -> tuple[Hazard, bool]:
    """Insert or update by id. Returns (hazard, created)."""
    existing = session.get(HazardRow, hazard.id)
    row = convert.hazard_to_row(hazard)
    if existing is None:
        session.add(row)
        session.flush()
        return hazard, True
    for col in (
        "geom",
        "hazard_class",
        "confidence",
        "source",
        "stream",
        "camera_id",
        "first_seen",
        "last_seen",
        "ttl_s",
        "status",
        "severity",
        "velocity_m_s",
    ):
        setattr(existing, col, getattr(row, col))
    session.flush()
    return hazard, False


def get_hazard(session: Session, hazard_id: UUID) -> Hazard | None:
    row = session.get(HazardRow, hazard_id)
    return convert.row_to_hazard(row) if row else None


def list_hazards(
    session: Session,
    *,
    bbox: Sequence[float] | None = None,
    status: HazardStatus | None = None,
    limit: int = 1000,
) -> list[Hazard]:
    stmt = select(HazardRow).order_by(HazardRow.last_seen.desc()).limit(limit)
    if bbox is not None:
        stmt = stmt.where(gf.ST_Intersects(HazardRow.geom, _bbox_envelope(bbox)))
    if status is not None:
        stmt = stmt.where(HazardRow.status == status)
    return [convert.row_to_hazard(r) for r in session.scalars(stmt)]


def set_hazard_status(
    session: Session, hazard_id: UUID, status: HazardStatus, *, last_seen: datetime | None = None
) -> Hazard | None:
    row = session.get(HazardRow, hazard_id)
    if row is None:
        return None
    row.status = status
    if last_seen is not None:
        row.last_seen = last_seen
    session.flush()
    return convert.row_to_hazard(row)


def expired_hazards(session: Session, now: datetime) -> list[Hazard]:
    """Active hazards whose `last_seen + ttl_s` is in the past (Cartographer restores edges)."""
    stmt = select(HazardRow).where(
        HazardRow.status == "active",
        HazardRow.last_seen + func.make_interval(0, 0, 0, 0, 0, 0, HazardRow.ttl_s) < now,
    )
    return [convert.row_to_hazard(r) for r in session.scalars(stmt)]


# ---------------------------------------------------------------------------
# road edges
# ---------------------------------------------------------------------------


def replace_edges(session: Session, edges: Iterable[RoadEdge]) -> int:
    """Wipe and reload `road_edges` (ingestion). Returns row count."""
    session.execute(text("DELETE FROM edge_hazard_links"))
    session.execute(text("DELETE FROM road_edges"))
    rows = [convert.edge_to_row(e) for e in edges]
    session.add_all(rows)
    session.flush()
    bump_graph_version(session)
    return len(rows)


def list_edges(
    session: Session,
    *,
    bbox: Sequence[float] | None = None,
    status: EdgeStatus | None = None,
    ids: Sequence[int] | None = None,
    limit: int | None = None,
) -> list[RoadEdge]:
    stmt = select(RoadEdgeRow).order_by(RoadEdgeRow.id)
    if bbox is not None:
        stmt = stmt.where(gf.ST_Intersects(RoadEdgeRow.geom, _bbox_envelope(bbox)))
    if status is not None:
        stmt = stmt.where(RoadEdgeRow.status == status)
    if ids is not None:
        stmt = stmt.where(RoadEdgeRow.id.in_(list(ids)))
    if limit is not None:
        stmt = stmt.limit(limit)
    return [convert.row_to_edge(r) for r in session.scalars(stmt)]


def count_edges(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(RoadEdgeRow)) or 0)


def edges_intersecting(session: Session, geom: Geometry) -> list[EdgeOverlap]:
    """Edges touched by `geom`, with the fraction of each edge's length inside it.

    overlap_fraction = ST_Length(ST_Intersection(edge, geom)::geography)
                       / ST_Length(edge::geography)
    Lengths are computed on the geography (metres) so the fraction is metric, not in
    degrees. Edges with zero length are reported with fraction 0.
    """
    g = convert.geojson_to_wkb(geom)
    inter_len = gf.ST_Length(cast(gf.ST_Intersection(RoadEdgeRow.geom, g), Geography))
    edge_len = gf.ST_Length(cast(RoadEdgeRow.geom, Geography))
    stmt = (
        select(RoadEdgeRow.id, inter_len.label("inter_len"), edge_len.label("edge_len"))
        .where(gf.ST_Intersects(RoadEdgeRow.geom, g))
        .order_by(RoadEdgeRow.id)
    )
    out: list[EdgeOverlap] = []
    for edge_id, inter, total in session.execute(stmt):
        total_f = float(total or 0.0)
        inter_f = float(inter or 0.0)
        frac = min(1.0, inter_f / total_f) if total_f > 0 else 0.0
        out.append(
            EdgeOverlap(
                edge_id=edge_id,
                overlap_fraction=frac,
                overlap_length_m=inter_f,
                edge_length_m=total_f,
            )
        )
    return out


def update_edge_states(
    session: Session,
    updates: Iterable[tuple[int, EdgeStatus, float, float | None]],
) -> list[RoadEdge]:
    """Apply (edge_id, status, current_cost, current_load|None) and bump graph version."""
    changed_ids: list[int] = []
    for edge_id, status, cost, load in updates:
        values: dict[str, object] = {"status": status, "current_cost": cost}
        if load is not None:
            values["current_load"] = load
        res = session.execute(update(RoadEdgeRow).where(RoadEdgeRow.id == edge_id).values(**values))
        if isinstance(res, CursorResult) and res.rowcount:
            changed_ids.append(edge_id)
    session.flush()
    if changed_ids:
        bump_graph_version(session)
    return list_edges(session, ids=changed_ids) if changed_ids else []


def set_edge_capacity_factors(session: Session, factors: Mapping[int, float]) -> None:
    """Cartographer sets effective-capacity factors (crowd bottlenecks). Callers pair this
    with `update_edge_states` so the graph version bumps once."""
    for edge_id, factor in factors.items():
        session.execute(
            update(RoadEdgeRow).where(RoadEdgeRow.id == edge_id).values(capacity_factor=factor)
        )
    session.flush()


def set_edge_loads(session: Session, loads: dict[int, float]) -> None:
    """Navigator writes assigned flow per edge (does not bump the graph version)."""
    for edge_id, load in loads.items():
        session.execute(
            update(RoadEdgeRow).where(RoadEdgeRow.id == edge_id).values(current_load=load)
        )
    session.flush()


def upsert_edge_hazard_links(session: Session, links: Iterable[EdgeHazardLink]) -> None:
    rows = [
        {
            "edge_id": link.edge_id,
            "hazard_id": link.hazard_id,
            "overlap_fraction": link.overlap_fraction,
            "penalty_applied": link.penalty_applied,
            "applied_at": link.applied_at,
        }
        for link in links
    ]
    if not rows:
        return
    stmt = pg_insert(EdgeHazardLinkRow).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["edge_id", "hazard_id"],
        set_={
            "overlap_fraction": stmt.excluded.overlap_fraction,
            "penalty_applied": stmt.excluded.penalty_applied,
            "applied_at": stmt.excluded.applied_at,
        },
    )
    session.execute(stmt)


def links_for_hazard(session: Session, hazard_id: UUID) -> list[EdgeHazardLink]:
    stmt = select(EdgeHazardLinkRow).where(EdgeHazardLinkRow.hazard_id == hazard_id)
    return [
        EdgeHazardLink(
            edge_id=r.edge_id,
            hazard_id=r.hazard_id,
            overlap_fraction=r.overlap_fraction,
            penalty_applied=r.penalty_applied,
            applied_at=r.applied_at,
        )
        for r in session.scalars(stmt)
    ]


def links_for_edges(session: Session, edge_ids: Sequence[int]) -> list[EdgeHazardLink]:
    if not edge_ids:
        return []
    stmt = select(EdgeHazardLinkRow).where(EdgeHazardLinkRow.edge_id.in_(list(edge_ids)))
    return [
        EdgeHazardLink(
            edge_id=r.edge_id,
            hazard_id=r.hazard_id,
            overlap_fraction=r.overlap_fraction,
            penalty_applied=r.penalty_applied,
            applied_at=r.applied_at,
        )
        for r in session.scalars(stmt)
    ]


def delete_links_for_hazard(session: Session, hazard_id: UUID) -> list[int]:
    """Remove links for a resolved hazard; returns affected edge ids."""
    edge_ids = [
        r.edge_id
        for r in session.scalars(
            select(EdgeHazardLinkRow).where(EdgeHazardLinkRow.hazard_id == hazard_id)
        )
    ]
    session.execute(delete(EdgeHazardLinkRow).where(EdgeHazardLinkRow.hazard_id == hazard_id))
    return edge_ids


# ---------------------------------------------------------------------------
# graph version
# ---------------------------------------------------------------------------


def graph_version(session: Session) -> int:
    v = session.scalar(select(GraphStateRow.version).where(GraphStateRow.id == 1))
    return int(v or 0)


def bump_graph_version(session: Session) -> int:
    session.execute(
        update(GraphStateRow)
        .where(GraphStateRow.id == 1)
        .values(version=GraphStateRow.version + 1, updated_at=func.now())
    )
    return graph_version(session)


# ---------------------------------------------------------------------------
# community reports
# ---------------------------------------------------------------------------


def insert_report(session: Session, report: CommunityReport) -> CommunityReport:
    session.add(convert.report_to_row(report))
    session.flush()
    return report


def list_reports(
    session: Session,
    *,
    bbox: Sequence[float] | None = None,
    since: datetime | None = None,
    reporter_hash: str | None = None,
    limit: int = 1000,
) -> list[CommunityReport]:
    stmt = select(CommunityReportRow).order_by(CommunityReportRow.created_at.desc()).limit(limit)
    if bbox is not None:
        stmt = stmt.where(gf.ST_Intersects(CommunityReportRow.geom, _bbox_envelope(bbox)))
    if since is not None:
        stmt = stmt.where(CommunityReportRow.created_at >= since)
    if reporter_hash is not None:
        stmt = stmt.where(CommunityReportRow.reporter_hash == reporter_hash)
    return [convert.row_to_report(r) for r in session.scalars(stmt)]


def reports_near(
    session: Session, lon: float, lat: float, radius_m: float, *, since: datetime | None = None
) -> list[CommunityReport]:
    """Reports within `radius_m` of a point (geography distance)."""
    pt = gf.ST_SetSRID(gf.ST_MakePoint(lon, lat), 4326)
    stmt = select(CommunityReportRow).where(
        gf.ST_DWithin(cast(CommunityReportRow.geom, Geography), cast(pt, Geography), radius_m)
    )
    if since is not None:
        stmt = stmt.where(CommunityReportRow.created_at >= since)
    return [convert.row_to_report(r) for r in session.scalars(stmt)]


def update_report_counts(
    session: Session,
    report_id: UUID,
    *,
    confirms: int | None = None,
    denies: int | None = None,
    trust_score: float | None = None,
) -> CommunityReport | None:
    row = session.get(CommunityReportRow, report_id)
    if row is None:
        return None
    if confirms is not None:
        row.confirms = confirms
    if denies is not None:
        row.denies = denies
    if trust_score is not None:
        row.trust_score = trust_score
    session.flush()
    return convert.row_to_report(row)


# ---------------------------------------------------------------------------
# safe zones / routes
# ---------------------------------------------------------------------------


def upsert_safe_zone(session: Session, zone: SafeZone) -> SafeZone:
    existing = session.get(SafeZoneRow, zone.id)
    row = convert.safe_zone_to_row(zone)
    if existing is None:
        session.add(row)
    else:
        existing.name = row.name
        existing.geom = row.geom
        existing.capacity = row.capacity
        existing.current_load = row.current_load
    session.flush()
    return zone


def list_safe_zones(session: Session) -> list[SafeZone]:
    return [
        convert.row_to_safe_zone(r)
        for r in session.scalars(select(SafeZoneRow).order_by(SafeZoneRow.id))
    ]


def replace_routes(session: Session, routes: Iterable[Route]) -> list[Route]:
    """Publish the complete current route set: every previously current route becomes
    non-current (including groups that no longer exist, e.g. a cell that became
    unreachable), then the new ones are inserted. History is kept for the audit trail."""
    routes = list(routes)
    session.execute(update(RouteRow).where(RouteRow.is_current.is_(True)).values(is_current=False))
    session.add_all(convert.route_to_row(r, is_current=True) for r in routes)
    session.flush()
    return routes


def list_routes(
    session: Session, *, crowd_group_id: str | None = None, current_only: bool = True
) -> list[Route]:
    stmt = select(RouteRow).order_by(RouteRow.crowd_group_id, RouteRow.version.desc())
    if current_only:
        stmt = stmt.where(RouteRow.is_current.is_(True))
    if crowd_group_id is not None:
        stmt = stmt.where(RouteRow.crowd_group_id == crowd_group_id)
    return [convert.row_to_route(r) for r in session.scalars(stmt)]


def latest_route_version(session: Session, crowd_group_id: str) -> int:
    v = session.scalar(
        select(func.max(RouteRow.version)).where(RouteRow.crowd_group_id == crowd_group_id)
    )
    return int(v) if v is not None else -1


# ---------------------------------------------------------------------------
# agent events
# ---------------------------------------------------------------------------


def append_agent_event(session: Session, event: AgentEvent) -> AgentEvent:
    session.add(convert.agent_event_to_row(event))
    session.flush()
    return event


def list_agent_events(
    session: Session, *, agent: str | None = None, since: datetime | None = None, limit: int = 200
) -> list[AgentEvent]:
    stmt = select(AgentEventRow).order_by(AgentEventRow.wall_t.desc()).limit(limit)
    if agent is not None:
        stmt = stmt.where(AgentEventRow.agent == agent)
    if since is not None:
        stmt = stmt.where(AgentEventRow.wall_t >= since)
    return [convert.row_to_agent_event(r) for r in session.scalars(stmt)]


# ---------------------------------------------------------------------------
# misc
# ---------------------------------------------------------------------------


def table_counts(session: Session) -> dict[str, int]:
    out: dict[str, int] = {}
    for name, model in (
        ("hazards", HazardRow),
        ("road_edges", RoadEdgeRow),
        ("edge_hazard_links", EdgeHazardLinkRow),
        ("community_reports", CommunityReportRow),
        ("routes", RouteRow),
        ("safe_zones", SafeZoneRow),
        ("agent_events", AgentEventRow),
    ):
        out[name] = int(session.scalar(select(func.count()).select_from(model)) or 0)
    return out
