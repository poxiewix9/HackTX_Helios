"""SQLAlchemy table models for the blackboard.

Column shapes mirror the wire models in `arco_schemas.blackboard` one-to-one; the only
deliberate difference is `hazards.class` (SQL column) <-> `Hazard.hazard_class` (wire).
Geometry columns are WGS84 (SRID 4326) with GiST indexes. Conversion to/from the
Pydantic models lives in `arco_blackboard.convert`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from arco_blackboard.db import Base

# ---------------------------------------------------------------------------
# hazards
# ---------------------------------------------------------------------------


class HazardRow(Base):
    __tablename__ = "hazards"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    # Polygon or MultiPolygon; enforced at the application layer by the wire model.
    geom: Mapped[Any] = mapped_column(
        Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=False), nullable=False
    )
    hazard_class: Mapped[str] = mapped_column("class", String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    stream: Mapped[str | None] = mapped_column(String(8))
    camera_id: Mapped[str | None] = mapped_column(String(64))
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ttl_s: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    severity: Mapped[float] = mapped_column(Float, nullable=False)
    velocity_m_s: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        Index("ix_hazards_geom", "geom", postgresql_using="gist"),
        Index("ix_hazards_status_last_seen", "status", "last_seen"),
    )


# ---------------------------------------------------------------------------
# road_edges
# ---------------------------------------------------------------------------


class RoadEdgeRow(Base):
    __tablename__ = "road_edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    geom: Mapped[Any] = mapped_column(
        Geometry(geometry_type="LINESTRING", srid=4326, spatial_index=False), nullable=False
    )
    osm_way_id: Mapped[int | None] = mapped_column(BigInteger)
    u: Mapped[int] = mapped_column(BigInteger, nullable=False)
    v: Mapped[int] = mapped_column(BigInteger, nullable=False)
    length_m: Mapped[float] = mapped_column(Float, nullable=False)
    base_cost: Mapped[float] = mapped_column(Float, nullable=False)
    current_cost: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    capacity_estimate: Mapped[float] = mapped_column(Float, nullable=False)
    current_load: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    capacity_factor: Mapped[float] = mapped_column(
        Float, nullable=False, default=1.0, server_default="1.0"
    )
    highway: Mapped[str | None] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(String(256))

    __table_args__ = (
        Index("ix_road_edges_geom", "geom", postgresql_using="gist"),
        Index("ix_road_edges_u_v", "u", "v"),
        Index("ix_road_edges_status", "status"),
    )


class EdgeHazardLinkRow(Base):
    __tablename__ = "edge_hazard_links"

    edge_id: Mapped[int] = mapped_column(
        ForeignKey("road_edges.id", ondelete="CASCADE"), primary_key=True
    )
    hazard_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hazards.id", ondelete="CASCADE"), primary_key=True
    )
    overlap_fraction: Mapped[float] = mapped_column(Float, nullable=False)
    penalty_applied: Mapped[float] = mapped_column(Float, nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_edge_hazard_links_hazard", "hazard_id"),)


# ---------------------------------------------------------------------------
# community_reports
# ---------------------------------------------------------------------------


class CommunityReportRow(Base):
    __tablename__ = "community_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    geom: Mapped[Any] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False
    )
    report_type: Mapped[str] = mapped_column(String(16), nullable=False)
    hazard_class: Mapped[str | None] = mapped_column(String(32))
    target_hazard_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reporter_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    device_pubkey: Mapped[str] = mapped_column(Text, nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    trust_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    denies: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(String(280))

    __table_args__ = (
        Index("ix_community_reports_geom", "geom", postgresql_using="gist"),
        Index("ix_community_reports_reporter_created", "reporter_hash", "created_at"),
        Index("ix_community_reports_created_at", "created_at"),
    )


# ---------------------------------------------------------------------------
# safe_zones / routes
# ---------------------------------------------------------------------------


class SafeZoneRow(Base):
    __tablename__ = "safe_zones"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    geom: Mapped[Any] = mapped_column(
        Geometry(geometry_type="POLYGON", srid=4326, spatial_index=False), nullable=False
    )
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    current_load: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (Index("ix_safe_zones_geom", "geom", postgresql_using="gist"),)


class RouteRow(Base):
    __tablename__ = "routes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    origin_cell_id: Mapped[str] = mapped_column(String(64), nullable=False)
    safe_zone_id: Mapped[str] = mapped_column(
        ForeignKey("safe_zones.id", ondelete="CASCADE"), nullable=False
    )
    crowd_group_id: Mapped[str] = mapped_column(String(64), nullable=False)
    group_size: Mapped[int] = mapped_column(Integer, nullable=False)
    path: Mapped[Any] = mapped_column(
        Geometry(geometry_type="LINESTRING", srid=4326, spatial_index=False), nullable=False
    )
    edge_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)
    cost: Mapped[float] = mapped_column(Float, nullable=False)
    length_m: Mapped[float] = mapped_column(Float, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    graph_version: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    explanation_source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="deterministic"
    )
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index("ix_routes_path", "path", postgresql_using="gist"),
        Index("ix_routes_group_current", "crowd_group_id", "is_current"),
    )


# ---------------------------------------------------------------------------
# agent_events (append-only)
# ---------------------------------------------------------------------------


class AgentEventRow(Base):
    __tablename__ = "agent_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    agent: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    scenario_t: Mapped[float] = mapped_column(Float, nullable=False)
    wall_t: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    hazard_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list
    )
    edge_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False, default=list)
    route_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list
    )
    report_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list
    )
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_agent_events_wall_t", "wall_t"),
        Index("ix_agent_events_agent_wall_t", "agent", "wall_t"),
    )


# ---------------------------------------------------------------------------
# graph_state (single row) and bus_outbox (event log for NOTIFY + catch-up)
# ---------------------------------------------------------------------------


class GraphStateRow(Base):
    """Single-row table: monotonically increasing version of `road_edges`."""

    __tablename__ = "graph_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BusOutboxRow(Base):
    """Every published event. `id` doubles as the message `seq`.

    Postgres NOTIFY payloads are capped at 8000 bytes, so NOTIFY carries only the id and
    subscribers hydrate from this table; it also lets WebSocket clients catch up with
    `?since_seq=`.
    """

    __tablename__ = "bus_outbox"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    topic: Mapped[str] = mapped_column(String(32), nullable=False)
    publisher: Mapped[str] = mapped_column(String(32), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (Index("ix_bus_outbox_topic_id", "topic", "id"),)
