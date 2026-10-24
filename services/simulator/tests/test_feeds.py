"""Synthetic street feed: the blockade appears at the scripted timestamp, nowhere else."""

from __future__ import annotations

from pathlib import Path

import cv2

from arco_sim.feeds import (
    WATERMARK,
    FeedSpec,
    active_overlays,
    region_difference,
    render_frame,
    write_clip,
)
from arco_sim.scenario_io import load_scenario

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"
SCENARIOS_DIR = DATA_DIR / "scenarios"


def test_blockade_appears_at_known_timestamp() -> None:
    sc = load_scenario(SCENARIOS_DIR / "lahaina_2023.json")
    feed = sc.micro_feeds[0]
    debris = next(o for o in feed.synthetic_overlays if o.hazard_class == "debris")
    spec = FeedSpec(width=640, height=360, fps=10)
    # scale the bbox to the smaller test frame
    sx, sy = 640 / feed.calibration.image_width, 360 / feed.calibration.image_height
    bbox = [
        debris.bbox_px[0] * sx,
        debris.bbox_px[1] * sy,
        debris.bbox_px[2] * sx,
        debris.bbox_px[3] * sy,
    ]
    feed = feed.model_copy(
        update={
            "synthetic_overlays": [
                o.model_copy(
                    update={
                        "bbox_px": [
                            o.bbox_px[0] * sx,
                            o.bbox_px[1] * sy,
                            o.bbox_px[2] * sx,
                            o.bbox_px[3] * sy,
                        ]
                    }
                )
                for o in feed.synthetic_overlays
            ]
        }
    )
    before_a = render_frame(feed, debris.t - 20.0, spec)
    before_b = render_frame(feed, debris.t - 5.0, spec)
    after = render_frame(feed, debris.t + 1.0, spec)
    assert before_a.shape == (360, 640, 3)
    # frames are not static (lane dashes move), but the blockade region stays unchanged
    # until the scripted time, then changes a lot
    quiet = region_difference(before_a, before_b, bbox)
    loud = region_difference(before_b, after, bbox)
    assert loud > 25 and loud > 4 * quiet, (quiet, loud)
    assert [o.hazard_class for o in active_overlays(feed, debris.t - 1)] == [
        "smoke"
    ]  # smoke scripted earlier
    assert "debris" in {o.hazard_class for o in active_overlays(feed, debris.t)}
    assert debris.duration_s is not None
    assert "debris" not in {
        o.hazard_class for o in active_overlays(feed, debris.t + debris.duration_s + 1)
    }


def test_clip_is_readable_and_watermarked(tmp_path: Path) -> None:
    sc = load_scenario(SCENARIOS_DIR / "lahaina_2023.json")
    feed = sc.micro_feeds[0]
    out = tmp_path / "clip.mp4"
    n = write_clip(
        feed, out, t_start=470.0, duration_s=2.0, spec=FeedSpec(width=320, height=180, fps=5)
    )
    assert n == 10 and out.stat().st_size > 1000
    cap = cv2.VideoCapture(str(out))
    assert cap.isOpened()
    frames = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames += 1
        assert frame.shape == (180, 320, 3)
    cap.release()
    assert frames == n
    assert WATERMARK.startswith("SIMULATED")
