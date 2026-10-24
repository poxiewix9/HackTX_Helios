"""Scenario file schema (`data/scenarios/*.json`).

A scenario replays a real past event's *geometry* with scripted hazards and community
events. It is simulated: the UI label is `SIMULATION_LABEL`. Each scenario must cite
its data sources and state that polygons are digitized approximations.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from arco_schemas.geo import BBox, HazardGeometry, Point, Polygon
from arco_schemas.hazard_classes import HazardClass, StreamKind
from arco_schemas.vision import CameraGeoref, TileGeoref

SimulationLabel = Literal["Simulated on real event geometry"]
SIMULATION_LABEL: Final[SimulationLabel] = "Simulated on real event geometry"

EventKind = Literal["wildfire", "tornado", "flood", "hurricane", "earthquake", "other"]
RoadGraphSource = Literal["osmnx_bbox", "osmnx_place", "graphml_file"]
TimelineEventType = Literal[
    "hazard_appear",
    "hazard_update",
    "hazard_resolve",
    "cell_network_down",
    "cell_network_up",
    "camera_obscured",
    "note",
]
CommunityEventType = Literal["hazard_pin", "confirm", "deny", "all_clear"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DataSource(_Model):
    name: str
    url: str | None = None
    license: str
    note: str = Field(description="How it was used and how it was approximated")


class PopulationCell(_Model):
    """Origin cell: where evacuees start."""

    id: str
    geom: Point | Polygon
    population: int = Field(ge=0)


class SafeZoneSpec(_Model):
    id: str
    name: str
    geom: Polygon
    capacity: int = Field(ge=0)


class TileRef(_Model):
    tile_id: str
    path: str = Field(description="Relative to data/, e.g. samples/lahaina/tile_0.png")
    georef: TileGeoref


class FrameRef(_Model):
    t: float = Field(ge=0, description="Scenario seconds")
    tile_id: str
    path: str


class MacroFeed(_Model):
    tiles: list[TileRef] = Field(default_factory=list)
    frames: list[FrameRef] = Field(default_factory=list)


class SyntheticOverlay(_Model):
    """Composite a hazard overlay onto the base clip at a scripted time."""

    t: float = Field(ge=0)
    hazard_class: HazardClass
    bbox_px: list[float] = Field(min_length=4, max_length=4)
    duration_s: float | None = Field(default=None, gt=0)


class MicroFeed(_Model):
    camera_id: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    heading_deg: float = Field(ge=0, lt=360)
    fov_deg: float = Field(gt=0, lt=180)
    video_path: str | None = Field(
        default=None, description="Relative to data/; None = synthetic generator"
    )
    calibration: CameraGeoref
    synthetic_overlays: list[SyntheticOverlay] = Field(default_factory=list)


class TimelineEvent(_Model):
    t: float = Field(ge=0, description="Scenario seconds from start")
    type: TimelineEventType
    hazard_id: str | None = Field(
        default=None, description="Stable id for appear/update/resolve of the same hazard"
    )
    hazard_polygon_geojson: HazardGeometry | None = None
    hazard_class: HazardClass | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    stream: StreamKind | None = None
    camera_id: str | None = None
    velocity_m_s: float | None = Field(default=None, ge=0)
    note: str | None = None

    @model_validator(mode="after")
    def _hazard_fields_present(self) -> TimelineEvent:
        if self.type in ("hazard_appear", "hazard_update"):
            missing = [
                name
                for name, value in (
                    ("hazard_id", self.hazard_id),
                    ("hazard_polygon_geojson", self.hazard_polygon_geojson),
                    ("hazard_class", self.hazard_class),
                    ("confidence", self.confidence),
                    ("stream", self.stream),
                )
                if value is None
            ]
            if missing:
                msg = f"{self.type} at t={self.t} missing {missing}"
                raise ValueError(msg)
        if self.type == "hazard_resolve" and self.hazard_id is None:
            msg = f"hazard_resolve at t={self.t} missing hazard_id"
            raise ValueError(msg)
        return self


class CommunityEvent(_Model):
    t: float = Field(ge=0)
    type: CommunityEventType
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    reporter: str = Field(description="Simulated device label, e.g. phone-03")
    hazard_class: HazardClass | None = None
    target_hazard_id: str | None = None
    note: str | None = None


class Scenario(_Model):
    schema_version: str = Field(description="arco_schemas.SCHEMA_VERSION it was written for")
    id: str = Field(pattern=r"^[a-z0-9_\-]+$")
    name: str
    event: EventKind
    event_date: str = Field(description="ISO date of the real event, e.g. 2023-08-08")
    location: str
    bbox: BBox
    start_time: datetime = Field(description="Wall-clock the scenario claims to start at")
    duration_s: float = Field(gt=0)
    road_graph_source: RoadGraphSource
    road_graph_ref: str = Field(description="bbox string, place name, or graphml path")
    population_cells: list[PopulationCell] = Field(min_length=1)
    safe_zones: list[SafeZoneSpec] = Field(min_length=1)
    macro_feed: MacroFeed
    micro_feeds: list[MicroFeed] = Field(default_factory=list)
    timeline: list[TimelineEvent]
    community_events: list[CommunityEvent] = Field(default_factory=list)
    expected_blocked_edges: list[int] = Field(
        default_factory=list, description="road_edges ids expected severed by the end"
    )
    data_sources: list[DataSource] = Field(min_length=1)
    notes: str = Field(
        min_length=20, description="Must state data source and that polygons are approximations"
    )
    label: SimulationLabel = SIMULATION_LABEL

    @model_validator(mode="after")
    def _timeline_sorted_and_in_range(self) -> Scenario:
        ts = [e.t for e in self.timeline]
        if ts != sorted(ts):
            msg = "timeline must be sorted by t"
            raise ValueError(msg)
        if ts and ts[-1] > self.duration_s:
            msg = f"timeline event at t={ts[-1]} exceeds duration_s={self.duration_s}"
            raise ValueError(msg)
        return self
