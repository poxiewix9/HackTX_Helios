"""Unified hazard class taxonomy.

This module is the single source of truth for hazard classes across the vision
pipeline, the blackboard, the agents, the simulator, the dashboard and the mobile
app. `make schemas` exports it to `schema/hazard_classes.json`, which the TypeScript
package re-exports, so there is exactly one place to add or change a class.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

StreamKind = Literal["macro", "micro"]
"""`macro` = top-down satellite/aerial tiles; `micro` = ground-level street cameras."""

EdgeAction = Literal["sever", "penalize", "capacity"]
"""Default Cartographer action when a hazard of this class overlaps a road edge.

- sever:    edge becomes impassable (`status=severed`)
- penalize: edge cost multiplied (`status=penalized`)
- capacity: edge capacity reduced (crowding), cost raised via congestion function
Thresholds and multipliers live in `services/agents/config/penalties.yaml`.
"""


class HazardClass(StrEnum):
    """Every hazard class ARCO knows about, across both streams."""

    # Macro (satellite / aerial)
    FIRE_FRONT = "fire_front"
    SMOKE = "smoke"  # also detectable on micro
    FLOOD_WATER = "flood_water"
    COLLAPSED_BUILDING = "collapsed_building"
    TORNADO_SWATH = "tornado_swath"
    BLOCKED_ROAD = "blocked_road"
    # Micro (street cameras)
    DEBRIS = "debris"
    FLOODED_ROAD = "flooded_road"
    FIRE = "fire"
    CROWD_BOTTLENECK = "crowd_bottleneck"
    VEHICLE_BLOCKAGE = "vehicle_blockage"
    DOWNED_LINE = "downed_line"


class HazardClassInfo(BaseModel):
    """Display + behaviour metadata for one hazard class."""

    model_config = ConfigDict(frozen=True)

    id: HazardClass
    label: str = Field(description="Human-readable label for UI")
    streams: list[StreamKind] = Field(min_length=1, description="Streams that can emit it")
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$", description="Hex color for overlays")
    description: str
    default_action: EdgeAction
    expands: bool = Field(
        default=False,
        description="True if the hazard grows over time (fire/flood); Cartographer buffers it",
    )
    default_severity: float = Field(ge=0.0, le=1.0)


_MACRO: list[StreamKind] = ["macro"]
_MICRO: list[StreamKind] = ["micro"]
_BOTH: list[StreamKind] = ["macro", "micro"]

HAZARD_CLASSES: dict[HazardClass, HazardClassInfo] = {
    info.id: info
    for info in (
        HazardClassInfo(
            id=HazardClass.FIRE_FRONT,
            label="Fire front",
            streams=_MACRO,
            color="#ff3b30",
            description="Active flame front visible from above",
            default_action="sever",
            expands=True,
            default_severity=1.0,
        ),
        HazardClassInfo(
            id=HazardClass.SMOKE,
            label="Smoke",
            streams=_BOTH,
            color="#8e8e93",
            description="Smoke plume; reduces visibility, may hide other hazards",
            default_action="penalize",
            expands=True,
            default_severity=0.5,
        ),
        HazardClassInfo(
            id=HazardClass.FLOOD_WATER,
            label="Flood water",
            streams=_MACRO,
            color="#0a84ff",
            description="Standing or moving flood water over terrain",
            default_action="sever",
            expands=True,
            default_severity=0.9,
        ),
        HazardClassInfo(
            id=HazardClass.COLLAPSED_BUILDING,
            label="Collapsed building",
            streams=_MACRO,
            color="#a2845e",
            description="Structural collapse footprint",
            default_action="penalize",
            default_severity=0.7,
        ),
        HazardClassInfo(
            id=HazardClass.TORNADO_SWATH,
            label="Tornado swath",
            streams=_MACRO,
            color="#bf5af2",
            description="Long narrow damage corridor from a tornado track",
            default_action="sever",
            default_severity=0.95,
        ),
        HazardClassInfo(
            id=HazardClass.BLOCKED_ROAD,
            label="Blocked road",
            streams=_MACRO,
            color="#ff9f0a",
            description="Road obstruction visible from above",
            default_action="sever",
            default_severity=0.8,
        ),
        HazardClassInfo(
            id=HazardClass.DEBRIS,
            label="Debris",
            streams=_MICRO,
            color="#ffd60a",
            description="Debris field on the roadway",
            default_action="penalize",
            default_severity=0.6,
        ),
        HazardClassInfo(
            id=HazardClass.FLOODED_ROAD,
            label="Flooded road",
            streams=_MICRO,
            color="#64d2ff",
            description="Water across the road surface at street level",
            default_action="sever",
            expands=True,
            default_severity=0.85,
        ),
        HazardClassInfo(
            id=HazardClass.FIRE,
            label="Fire",
            streams=_MICRO,
            color="#ff453a",
            description="Flames visible from a street camera",
            default_action="sever",
            expands=True,
            default_severity=1.0,
        ),
        HazardClassInfo(
            id=HazardClass.CROWD_BOTTLENECK,
            label="Crowd bottleneck",
            streams=_MICRO,
            color="#30d158",
            description="Pedestrian or vehicle crowding approaching stampede density",
            default_action="capacity",
            default_severity=0.5,
        ),
        HazardClassInfo(
            id=HazardClass.VEHICLE_BLOCKAGE,
            label="Vehicle blockage",
            streams=_MICRO,
            color="#ff9f0a",
            description="Stalled or crashed vehicles blocking lanes",
            default_action="penalize",
            default_severity=0.6,
        ),
        HazardClassInfo(
            id=HazardClass.DOWNED_LINE,
            label="Downed power line",
            streams=_MICRO,
            color="#ffd60a",
            description="Downed electrical line on or over the road",
            default_action="sever",
            default_severity=0.9,
        ),
    )
}

MACRO_CLASSES: tuple[HazardClass, ...] = tuple(
    c for c, info in HAZARD_CLASSES.items() if "macro" in info.streams
)
MICRO_CLASSES: tuple[HazardClass, ...] = tuple(
    c for c, info in HAZARD_CLASSES.items() if "micro" in info.streams
)


def classes_for_stream(stream: StreamKind) -> tuple[HazardClass, ...]:
    """Return the hazard classes a given stream's model is expected to emit."""
    return MACRO_CLASSES if stream == "macro" else MICRO_CLASSES
