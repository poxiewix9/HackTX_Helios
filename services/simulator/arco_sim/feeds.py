"""Synthetic street-feed generator (SIMULATED footage).

Renders a procedural street scene for a `MicroFeed` and composites the feed's
`synthetic_overlays` (debris / flooded_road / smoke / fire / vehicle_blockage / …) at
their scripted scenario times, so a blockade appears at a *known* timestamp. Clips written
with `write_clip` are plain video files that `arco_edge` can read as a capture source
(`--source path.mp4`), which lets real or synthetic detectors run on them.

No real imagery is used; these frames must never be presented as footage of the event.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from arco_schemas import HAZARD_CLASSES, HazardClass
from arco_schemas.scenario import MicroFeed, SyntheticOverlay

WATERMARK = "SIMULATED street feed"


@dataclass(frozen=True)
class FeedSpec:
    width: int = 1280
    height: int = 720
    fps: float = 10.0
    seed: int = 0


def _hex_bgr(color: str) -> tuple[int, int, int]:
    r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    return (b, g, r)


def _base_scene(spec: FeedSpec, t: float, rng: np.random.Generator) -> np.ndarray:
    w, h = spec.width, spec.height
    img = np.zeros((h, w, 3), dtype=np.uint8)
    horizon = int(h * 0.45)
    # sky gradient
    for y in range(horizon):
        f = y / max(1, horizon)
        img[y, :] = (int(200 - 60 * f), int(160 - 40 * f), int(120 - 30 * f))
    # ground
    img[horizon:, :] = (70, 75, 70)
    # road trapezoid (perspective)
    road = np.array(
        [
            [int(w * 0.44), horizon],
            [int(w * 0.56), horizon],
            [int(w * 0.95), h],
            [int(w * 0.05), h],
        ],
        dtype=np.int32,
    )
    cv2.fillPoly(img, [road], (60, 60, 60))
    # lane dashes that move with t (so frames are not identical)
    phase = (t * 2.0) % 1.0
    for k in range(8):
        f0 = ((k + phase) / 8.0) ** 2
        f1 = ((k + phase + 0.4) / 8.0) ** 2
        y0, y1 = int(horizon + f0 * (h - horizon)), int(horizon + f1 * (h - horizon))
        if y1 <= y0:
            continue
        half = 2 + int(6 * f1)
        cv2.rectangle(img, (w // 2 - half, y0), (w // 2 + half, y1), (210, 210, 210), -1)
    # buildings
    for i in range(6):
        bw, bh = int(w * 0.07), int(h * (0.15 + 0.05 * (i % 3)))
        x = int(w * 0.02 + i * w * 0.075) if i < 3 else int(w * 0.72 + (i - 3) * w * 0.085)
        cv2.rectangle(img, (x, horizon - bh), (x + bw, horizon), (110 + 10 * i, 105, 100), -1)
    noise = rng.integers(0, 10, size=img.shape, dtype=np.uint8)
    out: np.ndarray = cv2.add(img, noise)
    return out


def _overlay_active(ov: SyntheticOverlay, t: float) -> bool:
    if t < ov.t:
        return False
    return ov.duration_s is None or t < ov.t + ov.duration_s


def _draw_overlay(
    img: np.ndarray, ov: SyntheticOverlay, t: float, rng: np.random.Generator
) -> None:
    x0, y0, x1, y1 = (int(v) for v in ov.bbox_px)
    x0, x1 = max(0, x0), min(img.shape[1] - 1, x1)
    y0, y1 = max(0, y0), min(img.shape[0] - 1, y1)
    if x1 <= x0 or y1 <= y0:
        return
    cls = HazardClass(ov.hazard_class)
    color = _hex_bgr(HAZARD_CLASSES[cls].color)
    region = img[y0:y1, x0:x1]
    age = t - ov.t
    if cls in (HazardClass.SMOKE, HazardClass.FIRE, HazardClass.FIRE_FRONT):
        # soft blob that grows in for 3 s, then flickers
        grow = min(1.0, age / 3.0)
        rh, rw = int(region.shape[0]), int(region.shape[1])
        disk: np.ndarray = np.zeros((rh, rw), dtype=np.float32)
        cy, cx = rh // 2, rw // 2
        ry, rx = max(1, int(cy * grow)), max(1, int(cx * grow))
        cv2.ellipse(disk, (cx, cy), (rx, ry), 0, 0, 360, 1.0, -1)
        mask: np.ndarray = cv2.GaussianBlur(disk, (0, 0), max(1.0, min(rx, ry) / 3))
        flicker = 0.85 + 0.15 * float(rng.random())
        tint = np.array(color, dtype=np.float32) * flicker
        blended = (
            region.astype(np.float32) * (1 - mask[..., None] * 0.8) + tint * mask[..., None] * 0.8
        )
        region[:] = np.clip(blended, 0, 255).astype(np.uint8)
    elif cls in (HazardClass.FLOODED_ROAD, HazardClass.FLOOD_WATER):
        tint = np.array(color, dtype=np.float32)
        ripple = (np.sin(np.arange(region.shape[1]) / 9.0 + t * 3.0) * 8).astype(np.float32)
        blended = region.astype(np.float32) * 0.45 + tint * 0.55 + ripple[None, :, None]
        region[:] = np.clip(blended, 0, 255).astype(np.uint8)
    else:
        # debris / vehicle / downed line / collapsed: solid clutter boxes
        n = 6 if cls == HazardClass.DEBRIS else 2
        local = np.random.default_rng(int(ov.t * 1000) + x0)
        for _ in range(n):
            bw = max(8, int(local.integers(region.shape[1] // 4, max(9, region.shape[1] // 2))))
            bh = max(8, int(local.integers(region.shape[0] // 4, max(9, region.shape[0] // 2))))
            bx = int(local.integers(0, max(1, region.shape[1] - bw)))
            by = int(local.integers(0, max(1, region.shape[0] - bh)))
            shade = tuple(int(c * (0.7 + 0.3 * local.random())) for c in color)
            cv2.rectangle(region, (bx, by), (bx + bw, by + bh), shade, -1)
            cv2.rectangle(region, (bx, by), (bx + bw, by + bh), (30, 30, 30), 2)


def render_frame(feed: MicroFeed, t: float, spec: FeedSpec | None = None) -> np.ndarray:
    """One BGR frame of `feed` at scenario time `t` (seconds)."""
    spec = spec or FeedSpec()
    rng = np.random.default_rng(spec.seed + int(t * spec.fps))
    img = _base_scene(spec, t, rng)
    for ov in feed.synthetic_overlays:
        if _overlay_active(ov, t):
            _draw_overlay(img, ov, t, rng)
    cv2.putText(
        img,
        f"{WATERMARK} | {feed.camera_id} | t={t:7.1f}s",
        (12, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return img


def active_overlays(feed: MicroFeed, t: float) -> list[SyntheticOverlay]:
    return [ov for ov in feed.synthetic_overlays if _overlay_active(ov, t)]


def region_difference(a: np.ndarray, b: np.ndarray, bbox_px: list[float]) -> float:
    """Mean absolute pixel difference inside bbox between two frames (0..255)."""
    x0, y0, x1, y1 = (int(v) for v in bbox_px)
    ra = a[y0:y1, x0:x1].astype(np.int16)
    rb = b[y0:y1, x0:x1].astype(np.int16)
    return float(np.abs(ra - rb).mean())


def write_clip(
    feed: MicroFeed,
    out_path: Path,
    *,
    t_start: float,
    duration_s: float,
    spec: FeedSpec | None = None,
) -> int:
    """Write an MP4 covering [t_start, t_start + duration_s). Returns frames written."""
    spec = spec or FeedSpec()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fourcc = cv2.VideoWriter.fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(out_path), fourcc, spec.fps, (spec.width, spec.height))
    if not writer.isOpened():
        raise RuntimeError(f"cannot open video writer for {out_path}")
    n = round(duration_s * spec.fps)
    try:
        for i in range(n):
            writer.write(render_frame(feed, t_start + i / spec.fps, spec))
    finally:
        writer.release()
    return n
