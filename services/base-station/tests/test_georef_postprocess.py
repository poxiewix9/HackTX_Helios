"""Homography / affine georeferencing and YOLO-seg post-processing (no DB, no model)."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest

from arco_base import classmap, georef, postprocess
from arco_base.classmap import ClassMap
from arco_base.settings import DEFAULT_CLASS_MAP
from arco_edge.preprocess import Letterbox
from arco_schemas.vision import CameraGeoref, TileGeoref

GEOREF_DIR = Path(__file__).resolve().parents[3] / "data" / "samples" / "vision" / "georef"


# ----------------------------------------------------------------------------- pinhole model


class Pinhole:
    """Ground-truth camera used to generate correspondences: height h, pitch down p,
    horizontal FOV, heading north. World = local metres (east, north) on z = 0."""

    def __init__(
        self,
        w: int = 1280,
        h: int = 720,
        height_m: float = 6.0,
        pitch_deg: float = 14.0,
        fov_deg: float = 70.0,
    ) -> None:
        self.w, self.h = w, h
        self.f = (w / 2) / math.tan(math.radians(fov_deg / 2))
        self.height_m, self.p = height_m, math.radians(pitch_deg)

    def ground(self, u: float, v: float) -> tuple[float, float] | None:
        x = (u - self.w / 2) / self.f
        y = (v - self.h / 2) / self.f
        forward = math.cos(self.p) - y * math.sin(self.p)
        up = -(y * math.cos(self.p) + math.sin(self.p))
        if up >= 0:
            return None
        s = self.height_m / (-up)
        return x * s, forward * s  # east, north


def _camera_georef(
    cam: Pinhole,
    pts: Sequence[tuple[float, float]],
    lat0: float = 20.8745,
    lon0: float = -156.6787,
) -> CameraGeoref:
    world = []
    for u, v in pts:
        g = cam.ground(u, v)
        assert g is not None
        e, n = g
        m_per_deg_lon = georef.M_PER_DEG_LAT * math.cos(math.radians(lat0))
        world.append([lon0 + e / m_per_deg_lon, lat0 + n / georef.M_PER_DEG_LAT])
    return CameraGeoref(
        camera_id="t",
        lat=lat0,
        lon=lon0,
        heading_deg=0,
        fov_deg=70,
        image_width=cam.w,
        image_height=cam.h,
        image_points=[[float(u), float(v)] for u, v in pts],
        world_points=world,
    )


def test_homography_recovers_unseen_ground_points() -> None:
    cam = Pinhole()
    calib_pts = [(200, 700), (1080, 700), (420, 480), (860, 480)]  # exactly 4 -> exact DLT
    g = _camera_georef(cam, calib_pts)
    hom = georef.CameraHomography.from_georef(g)
    assert hom.reprojection_rms_px < 0.05
    # unseen pixels, including far ones (near the horizon the ground distance explodes)
    for u, v in [(640, 600), (300, 520), (900, 450), (640, 400)]:
        truth = cam.ground(u, v)
        assert truth is not None
        ll = hom.pixels_to_lonlat(np.array([[u, v]]))
        assert ll is not None
        east = (ll[0, 0] - g.lon) * georef.M_PER_DEG_LAT * math.cos(math.radians(g.lat))
        north = (ll[0, 1] - g.lat) * georef.M_PER_DEG_LAT
        dist = math.hypot(east - truth[0], north - truth[1])
        assert dist < 0.05 * max(1.0, truth[1]), f"{(u, v)} off by {dist:.2f} m"


def test_homography_rejects_horizon_and_degenerate_input() -> None:
    cam = Pinhole()
    pts = [(200, 700), (1080, 700), (420, 480), (860, 480), (560, 420), (720, 420)]
    g = _camera_georef(cam, pts)
    hom = georef.CameraHomography.from_georef(g)
    # the horizon for a 14 deg down-pitch camera is at row ~132; row 100 is sky
    assert cam.ground(640, 100) is None
    assert hom.pixels_to_lonlat(np.array([[640, 100]])) is None
    assert georef.ring_to_lonlat(hom, [[640, 100], [700, 100], [700, 200]]) is None
    collinear = np.array([[0, 0], [1, 1], [2, 2], [3, 3]], dtype=float)
    with pytest.raises(georef.GeorefError):
        georef.solve_homography(collinear, collinear * 2)


def test_sample_camera_file_is_consistent() -> None:
    g = CameraGeoref.model_validate(json.loads((GEOREF_DIR / "camera_front_st.json").read_text()))
    hom = georef.CameraHomography.from_georef(g)
    assert hom.reprojection_rms_px < 0.5
    ring = georef.ring_to_lonlat(hom, [[420, 470], [860, 470], [860, 700], [420, 700], [420, 470]])
    assert ring is not None and ring[0] == ring[-1]
    # footprint is within ~60 m of the camera
    for lon, lat in ring:
        assert abs(lat - g.lat) * georef.M_PER_DEG_LAT < 60
        assert abs(lon - g.lon) * georef.M_PER_DEG_LAT < 60


def test_tile_affine_corners() -> None:
    g = TileGeoref.model_validate(json.loads((GEOREF_DIR / "tile_lahaina_0.json").read_text()))
    aff = georef.TileAffine.from_georef(g)
    lon0, lat0, lon1, lat1 = g.bounds
    pts = aff.pixels_to_lonlat(np.array([[0, 0], [g.image_width, g.image_height]]))
    assert pts[0].tolist() == pytest.approx([lon0, lat1])  # top-left = west, north
    assert pts[1].tolist() == pytest.approx([lon1, lat0])


# ----------------------------------------------------------------------------- postprocess


def _fake_seg_output(imgsz: int, nc: int, nm: int = 4, mh: int = 40) -> list[np.ndarray]:
    """Two confident boxes of class 0 and 2 plus noise; protos make a filled rectangle for
    instance 0 and a diagonal blob for instance 1."""
    n = 50
    pred = np.zeros((4 + nc + nm, n), dtype=np.float32)
    pred[4 : 4 + nc] = 0.01
    # box 0: centre (100,100) size 80x60, class 0, mask coeff -> proto 0
    pred[:4, 0] = [100, 100, 80, 60]
    pred[4 + 0, 0] = 0.9
    pred[4 + nc + 0, 0] = 6.0
    # a duplicate of box 0 with lower score -> suppressed by NMS
    pred[:4, 1] = [104, 98, 80, 60]
    pred[4 + 0, 1] = 0.6
    pred[4 + nc + 0, 1] = 6.0
    # box 2: centre (240,200) size 60x60, class 2, proto 1
    pred[:4, 2] = [240, 200, 60, 60]
    pred[4 + 2, 2] = 0.8
    pred[4 + nc + 1, 2] = 6.0
    protos = np.full((nm, mh, mh), -6.0, dtype=np.float32)
    protos[0] = 1.0  # everywhere-on: the box crop defines the mask
    yy, xx = np.mgrid[0:mh, 0:mh]
    protos[1] = np.where(np.abs(yy - xx * 0.8) < 4, 1.0, -1.0)
    return [pred[None], protos[None]]


def test_decode_nms_and_polygons() -> None:
    imgsz = 320
    lb = Letterbox.for_size(640, 480, imgsz)  # ratio 0.5, pad_y 40
    inst = postprocess.decode(
        _fake_seg_output(imgsz, nc=3), lb, num_classes=3, conf_thr=0.25, iou_thr=0.5
    )
    assert [i.class_index for i in inst] == [0, 2]  # duplicate suppressed, order by score
    a = inst[0]
    # (100,100) 80x60 in letterbox -> original:
    #   x (60..140)/0.5 = 120..280, y (70-40..130-40)/0.5 = 60..180
    assert a.bbox_px == pytest.approx([120, 60, 280, 180], abs=1.0)
    assert a.polygon_px is not None and len(a.polygon_px) >= 4
    xs = [p[0] for p in a.polygon_px]
    ys = [p[1] for p in a.polygon_px]
    # mask crops are at prototype resolution (8 letterbox px = 16 original px per cell)
    assert min(xs) >= 104 and max(xs) <= 296 and min(ys) >= 44 and max(ys) <= 196
    assert inst[1].polygon_px is not None


def test_decode_empty_when_nothing_confident() -> None:
    lb = Letterbox.for_size(640, 480, 320)
    out = _fake_seg_output(320, nc=3)
    out[0][0, 4:7] = 0.0
    assert postprocess.decode(out, lb, num_classes=3, conf_thr=0.25, iou_thr=0.5) == []


def test_class_map_proxy_aggregation() -> None:
    cmap = ClassMap.load(DEFAULT_CLASS_MAP, "coco")
    names = ["person", "bicycle", "car"]

    def square(x: int, y: int, s: int) -> list[list[float]]:
        return [[x, y], [x + s, y], [x + s, y + s], [x, y + s], [x, y]]

    one_person = [postprocess.Instance(0, 0.9, [0, 0, 10, 10], square(0, 0, 10))]
    assert classmap.apply(cmap, "micro", names, one_person, default_min_conf=0.3) == []
    crowd = [
        postprocess.Instance(0, 0.8, [i * 12, 0, i * 12 + 10, 10], square(i * 12, 0, 10))
        for i in range(4)
    ]
    out = classmap.apply(cmap, "micro", names, crowd, default_min_conf=0.3)
    assert len(out) == 1 and out[0].hazard_class.value == "crowd_bottleneck" and out[0].count == 4
    assert out[0].bbox_px[2] >= 46  # union spans all four
    # identity map for a fine-tuned model
    ident = ClassMap.for_model(DEFAULT_CLASS_MAP, ["fire", "smoke"])
    assert ident.mapping == "hazards"
    fire = [postprocess.Instance(0, 0.7, [0, 0, 10, 10], square(0, 0, 10))]
    out = classmap.apply(ident, "micro", ["fire", "smoke"], fire, default_min_conf=0.3)
    assert out[0].hazard_class.value == "fire"
