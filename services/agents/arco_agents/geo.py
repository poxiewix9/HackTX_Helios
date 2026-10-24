"""Small metric-geometry helpers on WGS84 shapes (no hardcoded coordinates)."""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

import numpy as np
import shapely
from numpy.typing import NDArray
from pyproj import Transformer
from shapely.geometry import LineString as ShapelyLine
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

from arco_schemas import LineString
from arco_schemas.geo import Geometry, HazardGeometry, MultiPolygon, Polygon

EARTH_RADIUS_M = 6_371_008.8


def to_shape(geom: Geometry) -> BaseGeometry:
    return shape(geom.model_dump(mode="json"))


def to_hazard_geometry(geom: BaseGeometry) -> HazardGeometry:
    data: dict[str, Any] = dict(mapping(geom))
    if data["type"] == "MultiPolygon":
        return MultiPolygon.model_validate(data)
    return Polygon.model_validate(data)


@lru_cache(maxsize=64)
def _aeqd(lon: float, lat: float) -> tuple[Transformer, Transformer]:
    """Local azimuthal-equidistant projection: metres are true near the centre, which is
    all a hazard buffer of a few hundred metres needs."""
    proj = f"+proj=aeqd +lat_0={lat:.4f} +lon_0={lon:.4f} +datum=WGS84 +units=m +no_defs"
    fwd = Transformer.from_crs("EPSG:4326", proj, always_xy=True)
    inv = Transformer.from_crs(proj, "EPSG:4326", always_xy=True)
    return fwd, inv


def buffer_m(geom: BaseGeometry, distance_m: float) -> BaseGeometry:
    """Buffer a WGS84 geometry by metres (projects to a local AEQD, buffers, unprojects)."""
    if distance_m <= 0:
        return geom
    c = geom.centroid
    fwd, inv = _aeqd(round(c.x, 2), round(c.y, 2))

    def _apply(t: Transformer) -> Any:
        def f(coords: NDArray[np.float64]) -> NDArray[np.float64]:
            x, y = t.transform(coords[:, 0], coords[:, 1])
            return np.column_stack([x, y])

        return f

    local = shapely.transform(geom, _apply(fwd))
    return shapely.transform(local.buffer(distance_m, quad_segs=8), _apply(inv))


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def bearing_deg(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Initial compass bearing from point 1 to point 2, 0..360 (0 = north)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


COMPASS = ("north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west")


def compass(bearing: float) -> str:
    return COMPASS[int(((bearing + 22.5) % 360.0) // 45.0)]


def concat_lines(lines: list[LineString]) -> LineString:
    """Join consecutive edge geometries into one path LineString (dedupes shared nodes)."""
    coords: list[list[float]] = []
    for line in lines:
        pts = [list(p) for p in line.coordinates]
        if coords and coords[-1] == pts[0]:
            pts = pts[1:]
        coords.extend(pts)
    if len(coords) < 2:
        coords = coords * 2 if coords else [[0.0, 0.0], [0.0, 0.0]]
    return LineString(coordinates=coords)


def line_length_m(line: LineString) -> float:
    c = line.coordinates
    return sum(haversine_m(c[i][0], c[i][1], c[i + 1][0], c[i + 1][1]) for i in range(len(c) - 1))


def shapely_line(line: LineString) -> ShapelyLine:
    return ShapelyLine(line.coordinates)
