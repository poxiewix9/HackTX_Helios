"""Minimal GeoJSON geometry models (RFC 7946), WGS84 lon/lat order.

Positions are `[lon, lat]` or `[lon, lat, alt]`. Lists (not tuples) are used so the
exported JSON Schema uses `minItems`/`maxItems`, which the TypeScript generator turns
into tuple types without draft-2020 `prefixItems`.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Position = Annotated[list[float], Field(min_length=2, max_length=3)]
LinearRing = Annotated[list[Position], Field(min_length=4)]
BBox = Annotated[
    list[float],
    Field(min_length=4, max_length=4, description="[min_lon, min_lat, max_lon, max_lat]"),
]


class _Geometry(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Point(_Geometry):
    type: Literal["Point"] = "Point"
    coordinates: Position


class LineString(_Geometry):
    type: Literal["LineString"] = "LineString"
    coordinates: Annotated[list[Position], Field(min_length=2)]


class Polygon(_Geometry):
    type: Literal["Polygon"] = "Polygon"
    coordinates: Annotated[list[LinearRing], Field(min_length=1)]


class MultiPolygon(_Geometry):
    type: Literal["MultiPolygon"] = "MultiPolygon"
    coordinates: Annotated[list[list[LinearRing]], Field(min_length=1)]


Geometry = Point | LineString | Polygon | MultiPolygon
HazardGeometry = Polygon | MultiPolygon
