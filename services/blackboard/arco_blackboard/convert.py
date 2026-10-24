"""ORM row <-> Pydantic wire model conversion, including geometry (WKB <-> GeoJSON)."""

from __future__ import annotations

from typing import Any, TypeVar

from geoalchemy2.elements import WKBElement, WKTElement
from geoalchemy2.shape import from_shape, to_shape
from pydantic import BaseModel
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

from arco_blackboard.models import (
    AgentEventRow,
    CommunityReportRow,
    HazardRow,
    RoadEdgeRow,
    RouteRow,
    SafeZoneRow,
)
from arco_schemas import (
    AgentEvent,
    CommunityReport,
    Hazard,
    LineString,
    MultiPolygon,
    Point,
    Polygon,
    RoadEdge,
    Route,
    SafeZone,
)
from arco_schemas.geo import HazardGeometry

G = TypeVar("G", bound=BaseModel)

# --- geometry ---------------------------------------------------------------


def geojson_to_wkb(geom: BaseModel) -> WKBElement:
    """Pydantic GeoJSON model -> GeoAlchemy2 element (SRID 4326)."""
    return from_shape(shape(geom.model_dump(mode="json")), srid=4326)


def wkb_to_geojson(elem: WKBElement | WKTElement | BaseGeometry, model: type[G]) -> G:
    """GeoAlchemy2 element / shapely geometry -> Pydantic GeoJSON model."""
    shp = elem if isinstance(elem, BaseGeometry) else to_shape(elem)
    return model.model_validate(mapping(shp))


def hazard_geometry(elem: WKBElement | WKTElement | BaseGeometry) -> HazardGeometry:
    shp = elem if isinstance(elem, BaseGeometry) else to_shape(elem)
    data: dict[str, Any] = dict(mapping(shp))
    if data["type"] == "Polygon":
        return Polygon.model_validate(data)
    return MultiPolygon.model_validate(data)


# --- hazards ----------------------------------------------------------------


def hazard_to_row(h: Hazard) -> HazardRow:
    return HazardRow(
        id=h.id,
        geom=geojson_to_wkb(h.geom),
        hazard_class=str(h.hazard_class),
        confidence=h.confidence,
        source=h.source,
        stream=h.stream,
        camera_id=h.camera_id,
        first_seen=h.first_seen,
        last_seen=h.last_seen,
        ttl_s=h.ttl_s,
        status=h.status,
        severity=h.severity,
        velocity_m_s=h.velocity_m_s,
    )


def row_to_hazard(r: HazardRow) -> Hazard:
    return Hazard(
        id=r.id,
        geom=hazard_geometry(r.geom),
        hazard_class=r.hazard_class,  # type: ignore[arg-type]
        confidence=r.confidence,
        source=r.source,  # type: ignore[arg-type]
        stream=r.stream,  # type: ignore[arg-type]
        camera_id=r.camera_id,
        first_seen=r.first_seen,
        last_seen=r.last_seen,
        ttl_s=r.ttl_s,
        status=r.status,  # type: ignore[arg-type]
        severity=r.severity,
        velocity_m_s=r.velocity_m_s,
    )


# --- road edges -------------------------------------------------------------


def edge_to_row(e: RoadEdge) -> RoadEdgeRow:
    return RoadEdgeRow(
        id=e.id,
        geom=geojson_to_wkb(e.geom),
        osm_way_id=e.osm_way_id,
        u=e.u,
        v=e.v,
        length_m=e.length_m,
        base_cost=e.base_cost,
        current_cost=e.current_cost,
        status=e.status,
        capacity_estimate=e.capacity_estimate,
        current_load=e.current_load,
        highway=e.highway,
        name=e.name,
    )


def row_to_edge(r: RoadEdgeRow) -> RoadEdge:
    return RoadEdge(
        id=r.id,
        geom=wkb_to_geojson(r.geom, LineString),
        osm_way_id=r.osm_way_id,
        u=r.u,
        v=r.v,
        length_m=r.length_m,
        base_cost=r.base_cost,
        current_cost=r.current_cost,
        status=r.status,  # type: ignore[arg-type]
        capacity_estimate=r.capacity_estimate,
        current_load=r.current_load,
        highway=r.highway,
        name=r.name,
    )


# --- community reports ------------------------------------------------------


def report_to_row(c: CommunityReport) -> CommunityReportRow:
    return CommunityReportRow(
        id=c.id,
        geom=geojson_to_wkb(c.geom),
        report_type=c.report_type,
        hazard_class=str(c.hazard_class) if c.hazard_class is not None else None,
        target_hazard_id=c.target_hazard_id,
        reporter_hash=c.reporter_hash,
        device_pubkey=c.device_pubkey,
        signature=c.signature,
        trust_score=c.trust_score,
        created_at=c.created_at,
        confirms=c.confirms,
        denies=c.denies,
        note=c.note,
    )


def row_to_report(r: CommunityReportRow) -> CommunityReport:
    return CommunityReport(
        id=r.id,
        geom=wkb_to_geojson(r.geom, Point),
        report_type=r.report_type,  # type: ignore[arg-type]
        hazard_class=r.hazard_class,  # type: ignore[arg-type]
        target_hazard_id=r.target_hazard_id,
        reporter_hash=r.reporter_hash,
        device_pubkey=r.device_pubkey,
        signature=r.signature,
        trust_score=r.trust_score,
        created_at=r.created_at,
        confirms=r.confirms,
        denies=r.denies,
        note=r.note,
    )


# --- safe zones / routes ----------------------------------------------------


def safe_zone_to_row(z: SafeZone) -> SafeZoneRow:
    return SafeZoneRow(
        id=z.id,
        name=z.name,
        geom=geojson_to_wkb(z.geom),
        capacity=z.capacity,
        current_load=z.current_load,
    )


def row_to_safe_zone(r: SafeZoneRow) -> SafeZone:
    return SafeZone(
        id=r.id,
        name=r.name,
        geom=wkb_to_geojson(r.geom, Polygon),
        capacity=r.capacity,
        current_load=r.current_load,
    )


def route_to_row(rt: Route, *, is_current: bool = True) -> RouteRow:
    return RouteRow(
        id=rt.id,
        origin_cell_id=rt.origin_cell_id,
        safe_zone_id=rt.safe_zone_id,
        crowd_group_id=rt.crowd_group_id,
        group_size=rt.group_size,
        path=geojson_to_wkb(rt.path),
        edge_ids=list(rt.edge_ids),
        cost=rt.cost,
        length_m=rt.length_m,
        computed_at=rt.computed_at,
        graph_version=rt.graph_version,
        version=rt.version,
        instruction=rt.instruction,
        rationale=rt.rationale,
        explanation_source=rt.explanation_source,
        is_current=is_current,
    )


def row_to_route(r: RouteRow) -> Route:
    return Route(
        id=r.id,
        origin_cell_id=r.origin_cell_id,
        safe_zone_id=r.safe_zone_id,
        crowd_group_id=r.crowd_group_id,
        group_size=r.group_size,
        path=wkb_to_geojson(r.path, LineString),
        edge_ids=list(r.edge_ids),
        cost=r.cost,
        length_m=r.length_m,
        computed_at=r.computed_at,
        graph_version=r.graph_version,
        version=r.version,
        instruction=r.instruction,
        rationale=r.rationale,
        explanation_source=r.explanation_source,  # type: ignore[arg-type]
    )


# --- agent events -----------------------------------------------------------


def agent_event_to_row(ev: AgentEvent) -> AgentEventRow:
    return AgentEventRow(
        id=ev.id,
        agent=ev.agent,
        kind=ev.kind,
        reasoning=ev.reasoning,
        scenario_t=ev.scenario_t,
        wall_t=ev.wall_t,
        hazard_ids=list(ev.hazard_ids),
        edge_ids=list(ev.edge_ids),
        route_ids=list(ev.route_ids),
        report_ids=list(ev.report_ids),
        data=dict(ev.data),
    )


def row_to_agent_event(r: AgentEventRow) -> AgentEvent:
    return AgentEvent(
        id=r.id,
        agent=r.agent,  # type: ignore[arg-type]
        kind=r.kind,
        reasoning=r.reasoning,
        scenario_t=r.scenario_t,
        wall_t=r.wall_t,
        hazard_ids=list(r.hazard_ids),
        edge_ids=list(r.edge_ids),
        route_ids=list(r.route_ids),
        report_ids=list(r.report_ids),
        data=dict(r.data),
    )
