"""Blackboard entities: the rows every service reads and writes in PostGIS.

These are *wire/contract* models. `services/blackboard` maps them to SQLAlchemy /
GeoAlchemy2 tables; everyone else only ever sees these shapes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from arco_schemas.geo import HazardGeometry, LineString, Point, Polygon
from arco_schemas.hazard_classes import HazardClass, StreamKind

HazardSource = Literal["macro_vision", "micro_vision", "community", "simulated"]
"""`simulated` is used by the scenario player so the UI can label injected hazards
honestly. Real inference on scenario frames uses `macro_vision` / `micro_vision`."""

HazardStatus = Literal["active", "resolved", "disputed"]
EdgeStatus = Literal["open", "penalized", "severed"]
ReportType = Literal["hazard_pin", "confirm", "deny", "all_clear"]
AgentName = Literal["cartographer", "navigator", "consensus"]


class _Entity(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Hazard(_Entity):
    """A georeferenced hazard polygon on the blackboard (table `hazards`)."""

    id: UUID
    geom: HazardGeometry
    hazard_class: HazardClass = Field(
        description="DB column `class`; named hazard_class on the wire"
    )
    confidence: float = Field(ge=0.0, le=1.0)
    source: HazardSource
    stream: StreamKind | None = Field(default=None, description="Emitting stream, if vision")
    camera_id: str | None = Field(default=None, description="Camera / tile id, if vision")
    first_seen: datetime
    last_seen: datetime
    ttl_s: float = Field(gt=0, description="Seconds after last_seen before auto-resolve")
    status: HazardStatus = "active"
    severity: float = Field(ge=0.0, le=1.0)
    velocity_m_s: float | None = Field(
        default=None,
        ge=0.0,
        description="Estimated expansion speed for growing hazards; drives Cartographer buffer",
    )


class RoadEdge(_Entity):
    """Directed road segment mirrored from OSM (table `road_edges`)."""

    id: int
    geom: LineString
    osm_way_id: int | None = None
    u: int = Field(description="Source OSM node id")
    v: int = Field(description="Target OSM node id")
    length_m: float = Field(ge=0.0)
    base_cost: float = Field(ge=0.0, description="Travel cost with no hazards (seconds)")
    current_cost: float = Field(ge=0.0, description="base_cost x penalties; inf when severed")
    status: EdgeStatus = "open"
    capacity_estimate: float = Field(
        ge=0.0, description="People per minute the edge can carry before congestion"
    )
    current_load: float = Field(default=0.0, ge=0.0, description="Assigned people per minute")
    highway: str | None = Field(default=None, description="OSM highway tag")
    name: str | None = None


class EdgeHazardLink(_Entity):
    """Which hazard caused which edge change (table `edge_hazard_links`)."""

    edge_id: int
    hazard_id: UUID
    overlap_fraction: float = Field(ge=0.0, le=1.0)
    penalty_applied: float = Field(ge=1.0, description="Cost multiplier; inf for sever")
    applied_at: datetime


class CommunityReport(_Entity):
    """Anonymous, signed peer report (table `community_reports`)."""

    id: UUID
    geom: Point
    report_type: ReportType
    hazard_class: HazardClass | None = Field(
        default=None, description="Required for hazard_pin, optional for confirm/deny"
    )
    target_hazard_id: UUID | None = Field(
        default=None, description="Hazard this confirms/denies, when known"
    )
    reporter_hash: str = Field(
        min_length=8, description="Hash of the device public key; never raw identity"
    )
    device_pubkey: str = Field(description="Base64 Ed25519 public key used to verify signature")
    signature: str = Field(description="Base64 Ed25519 signature over canonical report bytes")
    trust_score: float = Field(default=0.5, ge=0.0, le=1.0)
    created_at: datetime
    confirms: int = Field(default=0, ge=0)
    denies: int = Field(default=0, ge=0)
    note: str | None = Field(default=None, max_length=280)


class SafeZone(_Entity):
    """Evacuation destination (table `safe_zones`)."""

    id: str
    name: str
    geom: Polygon
    capacity: int = Field(ge=0)
    current_load: int = Field(default=0, ge=0)


class Route(_Entity):
    """One computed evacuation route for a crowd group (table `routes`)."""

    id: UUID
    origin_cell_id: str
    safe_zone_id: str
    crowd_group_id: str
    group_size: int = Field(ge=0)
    path: LineString
    edge_ids: list[int]
    cost: float = Field(ge=0.0)
    length_m: float = Field(ge=0.0)
    computed_at: datetime
    graph_version: int = Field(ge=0, description="road_edges version the route was built on")
    version: int = Field(ge=0, description="Monotonic per crowd_group_id")
    instruction: str = Field(description="One-line human instruction (SLM or deterministic)")
    rationale: str | None = Field(default=None, description="Why this route; may be SLM text")
    explanation_source: Literal["slm", "deterministic"] = "deterministic"


class AgentEvent(_Entity):
    """Append-only audit entry (table `agent_events`)."""

    id: UUID
    agent: AgentName
    kind: str = Field(description="e.g. edge.severed, route.recomputed, hazard.overridden")
    reasoning: str
    scenario_t: float = Field(description="Scenario clock seconds")
    wall_t: datetime
    hazard_ids: list[UUID] = Field(default_factory=list)
    edge_ids: list[int] = Field(default_factory=list)
    route_ids: list[UUID] = Field(default_factory=list)
    report_ids: list[UUID] = Field(default_factory=list)
    data: dict[str, float | int | str | bool | None] = Field(default_factory=dict)
