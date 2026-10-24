#!/usr/bin/env python
"""Fine-tune YOLOv8-seg on a hazard dataset whose class names ARE ARCO hazard classes,
then hand the result to `export_split.py`.

Dataset layout (Ultralytics YOLO format), described by a data YAML such as
`data/datasets/hazards_micro.yaml`:

    path: data/datasets/hazards_micro
    train: images/train
    val: images/val
    names: [debris, flooded_road, fire, smoke, crowd_bottleneck, vehicle_blockage, downed_line]

Public sources that fit the taxonomy (download and relabel by hand; licences vary):
  - FireNet / D-Fire (fire, smoke, street level)       -> fire, smoke
  - FloodNet (aerial flood imagery)                    -> flood_water (macro)
  - xBD / xView2 (building damage, satellite)          -> collapsed_building (macro)
  - RescueNet (UAV post-disaster: debris, blocked road)-> debris, blocked_road
  - CrowdHuman (dense people)                          -> crowd_bottleneck

    uv sync --package arco-edge-capture --extra export
    uv run python scripts/finetune.py --data data/datasets/hazards_micro.yaml --epochs 30
    uv run python scripts/export_split.py --weights runs/segment/arco-micro/weights/best.pt \
        --model-id yolov8n-seg-hazards-micro --split-index 4

The script validates that every class name is in the shared taxonomy before training so
the base station can use the identity `hazards` class map.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from arco_schemas import HazardClass


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--data", type=Path, required=True, help="Ultralytics data YAML")
    ap.add_argument("--base", default="yolov8n-seg.pt", help="Starting weights")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--name", default="arco-hazards")
    ap.add_argument("--device", default=None, help="cpu | 0 | mps")
    args = ap.parse_args()

    spec = yaml.safe_load(args.data.read_text())
    names = spec.get("names")
    names = list(names.values()) if isinstance(names, dict) else list(names or [])
    taxonomy = {c.value for c in HazardClass}
    bad = [n for n in names if n not in taxonomy]
    if bad:
        raise SystemExit(
            f"class names not in the ARCO taxonomy: {bad}; allowed: {sorted(taxonomy)}"
        )

    try:
        from ultralytics import YOLO  # noqa: PLC0415 - optional heavy extra
    except ImportError as exc:
        raise SystemExit(
            "ultralytics not installed: uv sync --package arco-edge-capture --extra export"
        ) from exc
    model = YOLO(args.base)
    model.train(
        data=str(args.data),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        name=args.name,
        device=args.device,
        pretrained=True,
    )
    print(
        f"done: runs/segment/{args.name}/weights/best.pt -> scripts/export_split.py --weights ..."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
