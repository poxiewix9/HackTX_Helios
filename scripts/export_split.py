#!/usr/bin/env python
"""Export a YOLOv8 (Ultralytics) model to ONNX, split it into head/tail at a single-tensor
cut, statically quantize the head to INT8, measure the split/quant error and write
`models/<model_id>/manifest.json`.

    uv sync --package arco-edge-capture --extra export        # torch + ultralytics (large)
    uv run python scripts/export_split.py --weights yolov8n-seg.pt --imgsz 640 --split-index 4
    uv run python scripts/export_split.py --onnx path/to/model.onnx --names names.txt ...

Only `--onnx` mode is needed at runtime; the Ultralytics path is for producing the FP32
model. The split logic itself lives in `arco_edge.onnx_tools` (tested without torch).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import onnx

from arco_edge.onnx_tools import (
    SplitManifest,
    choose_cut,
    compare_split,
    quantize_static_int8,
    session,
    sha256_file,
    single_cut_points,
    split_model,
)


def export_ultralytics(weights: str, imgsz: int, out_dir: Path) -> tuple[Path, list[str], str]:
    try:
        from ultralytics import YOLO  # noqa: PLC0415 - optional heavy extra
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise SystemExit(
            "ultralytics not installed: uv sync --package arco-edge-capture --extra export"
        ) from exc
    model = YOLO(weights)
    task = str(model.task)
    names = [model.names[i] for i in sorted(model.names)]
    exported = model.export(format="onnx", imgsz=imgsz, opset=17, simplify=True, dynamic=False)
    src = Path(str(exported))
    dst = out_dir / "full_fp32.onnx"
    dst.write_bytes(src.read_bytes())
    return dst, names, task


def calibration_frames(n: int, imgsz: int, seed: int, folder: Path | None) -> list[np.ndarray]:
    """Calibration inputs: real images from `folder` when given, else deterministic noise
    textures (documented in the manifest — noise calibration is a weaker recipe)."""
    rng = np.random.default_rng(seed)
    out: list[np.ndarray] = []
    if folder is not None and folder.is_dir():
        import cv2  # noqa: PLC0415 - only with --calib-dir

        from arco_edge.preprocess import letterbox_tensor  # noqa: PLC0415

        for p in sorted(folder.iterdir())[:n]:
            img = cv2.imread(str(p))
            if img is not None:
                out.append(letterbox_tensor(img, imgsz)[0])
    while len(out) < n:
        base = rng.random((1, 3, imgsz // 8, imgsz // 8), dtype=np.float32)
        up = np.kron(base, np.ones((1, 1, 8, 8), dtype=np.float32))
        out.append(np.clip(up + rng.normal(0, 0.05, up.shape).astype(np.float32), 0, 1))
    return out


def main() -> int:  # noqa: PLR0915 - linear export pipeline, kept in one place on purpose
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--weights", help="Ultralytics .pt (needs the `export` extra)")
    src.add_argument("--onnx", help="Already-exported FP32 ONNX model")
    ap.add_argument("--names", help="Class names file (one per line) when using --onnx")
    ap.add_argument("--task", default="segment", help="segment | detect (with --onnx)")
    ap.add_argument("--model-id", default=None)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--split-index", type=int, default=None, help="Index into the valid cut list")
    ap.add_argument("--split-tensor", default=None, help="Cut tensor name (overrides index)")
    ap.add_argument("--list-cuts", action="store_true", help="Print valid cuts and exit")
    ap.add_argument("--calib-dir", type=Path, default=None, help="Images for calibration")
    ap.add_argument("--calib-n", type=int, default=16)
    ap.add_argument("--models-dir", type=Path, default=Path("models"))
    ap.add_argument("--tolerance-cosine", type=float, default=0.99, help="INT8 head bound")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    model_id = args.model_id or Path(args.weights or args.onnx).stem
    out_dir = args.models_dir / model_id
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.weights:
        full_path, names, task = export_ultralytics(args.weights, args.imgsz, out_dir)
    else:
        full_path = out_dir / "full_fp32.onnx"
        if Path(args.onnx).resolve() != full_path.resolve():
            full_path.write_bytes(Path(args.onnx).read_bytes())
        names = Path(args.names).read_text().split() if args.names else []
        task = args.task

    model = onnx.load(str(full_path))
    cuts = single_cut_points(model)
    if args.list_cuts or not cuts:
        print(f"{len(cuts)} valid single-tensor cuts (head_nodes -> tensor [op]):")
        for i, c in enumerate(cuts):
            print(
                f"  [{i:2d}] head={c.head_nodes:4d} tail={c.tail_nodes:4d}  "
                f"{c.tensor}  [{c.producer_op}]"
            )
        if args.list_cuts:
            return 0
        print("no valid cut: tail would need the image", file=sys.stderr)
        return 2
    cut = choose_cut(cuts, index=args.split_index, tensor=args.split_tensor)
    head, tail = split_model(model, cut)
    head_path, tail_path = out_dir / "head_fp32.onnx", out_dir / "tail_fp32.onnx"
    onnx.save(head, str(head_path))
    onnx.save(tail, str(tail_path))

    calib = calibration_frames(args.calib_n, args.imgsz, args.seed, args.calib_dir)
    in_name = session(full_path).get_inputs()[0].name
    cut_shape = [int(s) for s in session(head_path).run(None, {in_name: calib[0]})[0].shape]

    t0 = time.perf_counter()
    head_int8 = quantize_static_int8(head_path, out_dir / "head_int8.onnx", calib)
    quant_s = time.perf_counter() - t0

    full_s, tail_s = session(full_path), session(tail_path)
    eval_inputs = calibration_frames(4, args.imgsz, args.seed + 1, args.calib_dir)
    fp32 = compare_split(full_s, session(head_path), tail_s, eval_inputs)
    int8 = compare_split(full_s, session(head_int8), tail_s, eval_inputs)
    worst_fp32 = max(c.max_abs for c in fp32)
    worst_int8_cos = min(c.cosine for c in int8)
    tolerance = {
        "fp32_split_max_abs_measured": worst_fp32,
        "fp32_split_bound": 1e-4,
        "int8_head_min_cosine_measured": worst_int8_cos,
        "int8_head_cosine_bound": args.tolerance_cosine,
        "int8_head_mean_abs_measured": max(c.mean_abs for c in int8),
        "eval_inputs": len(eval_inputs),
        "calibration": "images" if args.calib_dir else "synthetic_noise",
        "calibration_n": len(calib),
        "quantize_seconds": round(quant_s, 1),
    }
    files = {
        "full_fp32": full_path.name,
        "head_fp32": head_path.name,
        "head_int8": head_int8.name,
        "tail_fp32": tail_path.name,
    }
    manifest = SplitManifest(
        model_id=model_id,
        task=task,
        imgsz=args.imgsz,
        input_name=in_name,
        split_index=cut.index,
        split_tensor=cut.tensor,
        cut_shape=cut_shape,
        class_names=names,
        files=files,
        sha256={k: sha256_file(out_dir / v) for k, v in files.items()},
        tolerance=tolerance,
        notes=(
            "Pretrained weights; class names are the training set's (COCO for stock yolov8), "
            "mapped to hazard classes by services/base-station/config/class_map.yaml. "
            "Fine-tune with scripts/finetune.py for real hazard classes."
        ),
    )
    manifest.dump(out_dir / "manifest.json")
    print(
        json.dumps(
            {"model_id": model_id, "cut": cut.tensor, "cut_shape": cut_shape, **tolerance}, indent=2
        )
    )
    ok = worst_fp32 <= 1e-4 and worst_int8_cos >= args.tolerance_cosine
    print("tolerance:", "OK" if ok else "EXCEEDED (manifest records the measured values)")
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
