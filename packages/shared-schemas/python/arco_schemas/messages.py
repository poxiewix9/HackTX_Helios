"""Typed WebSocket messages for `/ws/detections`, `/ws/blackboard`, `/ws/routes`,
`/ws/telemetry`, plus the Redis / NOTIFY event envelope the services exchange.

Every message carries both clocks: `scenario_t` (scenario clock, the single source of
truth for ordering) and `wall_t` (UTC wall clock). Messages are discriminated by `type`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from arco_schemas.blackboard import (
    AgentEvent,
    CommunityReport,
    Hazard,
    RoadEdge,
    Route,
    SafeZone,
)
from arco_schemas.hazard_classes import StreamKind
from arco_schemas.telemetry import BenchmarkResult, TelemetrySample
from arco_schemas.vision import Detection, DetectorKind

Channel = Literal["detections", "blackboard", "routes", "telemetry"]
"""WebSocket path is `/ws/<channel>`."""


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_t: float = Field(description="Scenario clock seconds; -1 when no scenario")
    wall_t: datetime
    seq: int = Field(ge=0, description="Per-channel monotonic sequence for gap detection")


# --- /ws/detections ---------------------------------------------------------


class DetectionsMessage(_Message):
    type: Literal["detections"] = "detections"
    stream: StreamKind
    source_id: str
    frame_id: int = Field(ge=0)
    detector: DetectorKind
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    detections: list[Detection]
    latency_ms: float = Field(ge=0, description="Measured capture->detections for this frame")


# --- /ws/blackboard ---------------------------------------------------------


class HazardEvent(_Message):
    type: Literal["hazard.created", "hazard.updated", "hazard.resolved", "hazard.disputed"]
    hazard: Hazard


class EdgeEvent(_Message):
    type: Literal["edge.updated"]
    edges: list[RoadEdge] = Field(description="Edges whose status/cost changed")
    graph_version: int = Field(ge=0)


class GraphUpdatedEvent(_Message):
    """Cartographer finished a batch; Navigator should recompute."""

    type: Literal["graph.updated"]
    graph_version: int = Field(ge=0)
    changed_edge_ids: list[int]
    severed_count: int = Field(ge=0)
    penalized_count: int = Field(ge=0)
    full_rebuild: bool = Field(
        default=False,
        description="True after (re)ingestion: consumers must reload every edge",
    )


class ReportEvent(_Message):
    type: Literal["report.created"]
    report: CommunityReport


class SafeZoneEvent(_Message):
    type: Literal["safe_zone.updated"]
    safe_zone: SafeZone


class AgentEventMessage(_Message):
    type: Literal["agent.event"]
    event: AgentEvent


class ClockEvent(_Message):
    """Scenario clock heartbeat from the simulator (1-2 Hz)."""

    type: Literal["clock.tick"]
    scenario_id: str | None
    playing: bool
    speed: float = Field(gt=0)
    duration_s: float | None = Field(default=None, gt=0)


class NetworkEvent(_Message):
    """Simulated cellular network state (chaos button / scenario)."""

    type: Literal["network.cellular"]
    cellular_up: bool
    reason: str | None = None


BlackboardMessage = Annotated[
    HazardEvent
    | EdgeEvent
    | GraphUpdatedEvent
    | ReportEvent
    | SafeZoneEvent
    | AgentEventMessage
    | ClockEvent
    | NetworkEvent,
    Field(discriminator="type"),
]


# --- /ws/routes -------------------------------------------------------------


class RoutesUpdatedMessage(_Message):
    type: Literal["routes.updated"] = "routes.updated"
    graph_version: int = Field(ge=0)
    routes: list[Route]
    trigger: Literal["graph.updated", "hazard", "periodic", "manual"]
    compute_ms: float = Field(ge=0, description="Measured Navigator compute time")
    max_edge_load_ratio: float = Field(
        ge=0, description="max(current_load / capacity) across used edges after dispersal"
    )


# --- /ws/telemetry ----------------------------------------------------------


class TelemetryMessage(_Message):
    type: Literal["telemetry.sample"] = "telemetry.sample"
    sample: TelemetrySample


class BenchmarkMessage(_Message):
    type: Literal["telemetry.benchmark"] = "telemetry.benchmark"
    result: BenchmarkResult


TelemetryChannelMessage = Annotated[
    TelemetryMessage | BenchmarkMessage, Field(discriminator="type")
]


# --- Any channel ------------------------------------------------------------

AnyMessage = Annotated[
    DetectionsMessage
    | HazardEvent
    | EdgeEvent
    | GraphUpdatedEvent
    | ReportEvent
    | SafeZoneEvent
    | AgentEventMessage
    | ClockEvent
    | NetworkEvent
    | RoutesUpdatedMessage
    | TelemetryMessage
    | BenchmarkMessage,
    Field(discriminator="type"),
]


class BusEnvelope(BaseModel):
    """What travels on Redis pub/sub channel `blackboard.<topic>` and on Postgres
    NOTIFY `blackboard_events`. `message` is any typed message above."""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(description="e.g. hazard, graph, routes, telemetry")
    published_at: datetime
    publisher: str = Field(description="Service name")
    message: AnyMessage
