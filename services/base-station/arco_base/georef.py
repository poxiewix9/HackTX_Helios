"""Pixel -> WGS84 georeferencing.

Street cameras (`CameraGeoref`): ground-plane homography.
    A pinhole camera maps points on a plane to the image by a projective transform, so
    for ground-plane points  [X, Y, 1]^T ~ H · [u, v, 1]^T  with H a 3x3 matrix (8 dof).
    We solve H from >= 4 pixel<->ground correspondences with the normalised DLT:
      * express ground points in local metres (east, north) around the camera so the
        linear system is well conditioned (lon/lat degrees differ by ~1e5 from pixels),
      * Hartley-normalise both point sets (centroid 0, mean distance sqrt(2)),
      * each correspondence gives two rows  A_i h = 0 ; h = right singular vector of A
        with the smallest singular value; denormalise  H = T_world^-1 · Ĥ · T_img.
    Points whose projective depth  w = h31·u + h32·v + h33  is <= 0 lie at or above the
    horizon (no ground intersection) and are rejected rather than mapped to nonsense.

Satellite / aerial tiles (`TileGeoref`): GDAL affine  lon = a·col + b·row + c,
    lat = d·col + e·row + f  (north-up tiles have b = d = 0, e < 0).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from arco_schemas.vision import CameraGeoref, TileGeoref

M_PER_DEG_LAT = 111_320.0  # metres per degree latitude (spherical approximation, <0.3% error)


class GeorefError(ValueError):
    pass


def _normalise(pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Hartley normalisation: returns (T, pts_h) with pts_h = T·[pts,1]; centroid at the
    origin and RMS distance sqrt(2)."""
    c = pts.mean(axis=0)
    d = np.sqrt(((pts - c) ** 2).sum(axis=1)).mean()
    s = math.sqrt(2.0) / d if d > 0 else 1.0
    t = np.array([[s, 0, -s * c[0]], [0, s, -s * c[1]], [0, 0, 1.0]])
    ph = np.hstack([pts, np.ones((len(pts), 1))]) @ t.T
    return t, ph


def solve_homography(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """DLT: H such that dst ~ H·src for Nx2 arrays (N >= 4)."""
    if len(src) < 4 or len(src) != len(dst):
        raise GeorefError("need >= 4 matching correspondences")
    t_src, s = _normalise(src.astype(np.float64))
    t_dst, d = _normalise(dst.astype(np.float64))
    rows = []
    for (x, y, _), (xp, yp, _) in zip(s, d, strict=True):
        # from  [xp, yp, 1] x (H [x, y, 1]) = 0  (cross product), two independent rows:
        rows.append([0, 0, 0, -x, -y, -1, yp * x, yp * y, yp])
        rows.append([x, y, 1, 0, 0, 0, -xp * x, -xp * y, -xp])
    a = np.asarray(rows)
    _, sv, vt = np.linalg.svd(a)
    if sv[-2] <= 1e-12:  # rank < 8: degenerate (collinear) correspondences
        raise GeorefError("degenerate correspondences (collinear points)")
    h_norm = vt[-1].reshape(3, 3)
    h: np.ndarray = np.linalg.inv(t_dst) @ h_norm @ t_src
    h = h / np.linalg.norm(h)
    # H is defined up to scale *and sign*; pick the sign that gives positive projective
    # depth w for the calibration points (all on the ground in front of the camera) so
    # that w <= 0 later really means "at or beyond the horizon".
    w = np.hstack([src, np.ones((len(src), 1))]) @ h[2]
    if w.mean() < 0:
        h = -h
    return np.asarray(h, dtype=np.float64)


@dataclass
class CameraHomography:
    camera_id: str
    h: np.ndarray  # pixel -> local metres (east, north) relative to the camera
    lat0: float
    lon0: float
    width: int
    height: int
    reprojection_rms_px: float

    @classmethod
    def from_georef(cls, g: CameraGeoref) -> CameraHomography:
        img = np.asarray(g.image_points, dtype=np.float64)[:, :2]
        world = np.asarray([p[:2] for p in g.world_points], dtype=np.float64)
        local = np.column_stack(
            [
                (world[:, 0] - g.lon) * M_PER_DEG_LAT * math.cos(math.radians(g.lat)),
                (world[:, 1] - g.lat) * M_PER_DEG_LAT,
            ]
        )
        h = solve_homography(img, local)
        # reprojection error in pixels (through the inverse) documents the calibration
        back = _apply(np.linalg.inv(h), local)
        rms = float(np.sqrt(((back - img) ** 2).sum(axis=1).mean()))
        return cls(g.camera_id, h, g.lat, g.lon, g.image_width, g.image_height, rms)

    def pixels_to_lonlat(self, pts_px: np.ndarray) -> np.ndarray | None:
        """Nx2 pixels -> Nx2 lon/lat, or None if any point is at/above the horizon."""
        pts = np.asarray(pts_px, dtype=np.float64)
        ph = np.hstack([pts, np.ones((len(pts), 1))]) @ self.h.T
        w = ph[:, 2]
        if np.any(w <= 1e-9):
            return None
        east, north = ph[:, 0] / w, ph[:, 1] / w
        lon = self.lon0 + east / (M_PER_DEG_LAT * math.cos(math.radians(self.lat0)))
        lat = self.lat0 + north / M_PER_DEG_LAT
        out: np.ndarray = np.column_stack([lon, lat])
        return out


def _apply(h: np.ndarray, pts: np.ndarray) -> np.ndarray:
    ph = np.hstack([pts, np.ones((len(pts), 1))]) @ h.T
    out: np.ndarray = ph[:, :2] / ph[:, 2:3]
    return out


@dataclass
class TileAffine:
    tile_id: str
    a: float
    b: float
    c: float
    d: float
    e: float
    f: float

    @classmethod
    def from_georef(cls, g: TileGeoref) -> TileAffine:
        a, b, c, d, e, f = g.affine
        return cls(g.tile_id, a, b, c, d, e, f)

    def pixels_to_lonlat(self, pts_px: np.ndarray) -> np.ndarray:
        pts = np.asarray(pts_px, dtype=np.float64)
        col, row = pts[:, 0], pts[:, 1]
        return np.column_stack(
            [self.a * col + self.b * row + self.c, self.d * col + self.e * row + self.f]
        )


Georeferencer = CameraHomography | TileAffine


def build(georef: Any) -> Georeferencer:
    if isinstance(georef, CameraGeoref):
        return CameraHomography.from_georef(georef)
    if isinstance(georef, TileGeoref):
        return TileAffine.from_georef(georef)
    raise GeorefError(f"unsupported georef {type(georef).__name__}")


def ring_to_lonlat(geo: Georeferencer, ring_px: list[list[float]]) -> list[list[float]] | None:
    """Closed pixel ring -> closed lon/lat ring (rounded to 1e-7 deg ≈ 1 cm)."""
    if len(ring_px) < 3:
        return None
    pts = np.asarray(ring_px, dtype=np.float64)[:, :2]
    out = geo.pixels_to_lonlat(pts)
    if out is None:
        return None
    ring = [[round(float(x), 7), round(float(y), 7)] for x, y in out]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring
