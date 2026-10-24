"""YOLOv8 detect/segment output -> boxes, masks, simplified pixel polygons.

Ultralytics ONNX export layout (batch 1):
  detect : output0 [1, 4 + nc, N]            rows = cx, cy, w, h, class scores
  segment: output0 [1, 4 + nc + nm, N], output1 (protos) [1, nm, mh, mw]
           instance mask = sigmoid(coeffs(nm) · protos) cropped to the box, > 0.5
All coordinates are in letterboxed model-input pixels; `Letterbox.to_original` maps back.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from shapely.geometry import Polygon

from arco_edge.preprocess import Letterbox


@dataclass
class Instance:
    class_index: int
    confidence: float
    bbox_px: list[float]  # original-image pixels [x0, y0, x1, y1]
    polygon_px: list[list[float]] | None  # closed ring in original-image pixels


def _xywh2xyxy(b: np.ndarray) -> np.ndarray:
    out = np.empty_like(b)
    out[:, 0] = b[:, 0] - b[:, 2] / 2
    out[:, 1] = b[:, 1] - b[:, 3] / 2
    out[:, 2] = b[:, 0] + b[:, 2] / 2
    out[:, 3] = b[:, 1] + b[:, 3] / 2
    return out


def nms(boxes: np.ndarray, scores: np.ndarray, iou_thr: float) -> list[int]:
    """Greedy IoU NMS (class-agnostic input; callers offset boxes per class if needed)."""
    order = scores.argsort()[::-1]
    keep: list[int] = []
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    while order.size:
        i = int(order[0])
        keep.append(i)
        rest = order[1:]
        xx0 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy0 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx1 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy1 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.clip(xx1 - xx0, 0, None) * np.clip(yy1 - yy0, 0, None)
        iou = inter / (areas[i] + areas[rest] - inter + 1e-9)
        order = rest[iou <= iou_thr]
    return keep


def mask_to_polygon(mask: np.ndarray, *, simplify_px: float) -> list[list[float]] | None:
    """Binary mask -> largest external contour -> Douglas-Peucker -> valid Shapely ring."""
    contours, _ = cv2.findContours(
        mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) < 4:
        return None
    approx = cv2.approxPolyDP(contour, simplify_px, True).reshape(-1, 2).astype(np.float64)
    if len(approx) < 3:
        return None
    poly: Any = Polygon(approx)
    if not poly.is_valid:
        poly = poly.buffer(0)
        if poly.is_empty:
            return None
        if poly.geom_type == "MultiPolygon":
            poly = max(poly.geoms, key=lambda g: g.area)
    coords = [[float(x), float(y)] for x, y in poly.exterior.coords]
    return coords


def decode(
    outputs: list[np.ndarray],
    lb: Letterbox,
    *,
    num_classes: int,
    conf_thr: float,
    iou_thr: float,
    max_det: int = 50,
    simplify_px: float = 2.0,
) -> list[Instance]:
    det = outputs[0]
    if det.ndim != 3:
        raise ValueError(f"unexpected detection output shape {det.shape}")
    pred = det[0]  # [4+nc(+nm), N]
    if pred.shape[0] < 4 + num_classes and pred.shape[1] >= 4 + num_classes:
        pred = pred.T  # some exporters emit [N, 4+nc]
    nm = pred.shape[0] - 4 - num_classes
    boxes = _xywh2xyxy(pred[:4].T)
    scores_all = pred[4 : 4 + num_classes].T
    cls = scores_all.argmax(axis=1)
    conf = scores_all[np.arange(len(cls)), cls]
    keep0 = conf >= conf_thr
    if not keep0.any():
        return []
    boxes, conf, cls = boxes[keep0], conf[keep0], cls[keep0]
    coeffs = pred[4 + num_classes :].T[keep0] if nm > 0 else None
    # class-aware NMS by offsetting boxes per class (standard trick)
    offset = cls[:, None].astype(np.float32) * (lb.imgsz + 1)
    keep = nms(boxes + offset, conf, iou_thr)[:max_det]

    protos = outputs[1][0] if (nm > 0 and len(outputs) > 1) else None
    instances: list[Instance] = []
    for i in keep:
        b = boxes[i]
        poly_px: list[list[float]] | None = None
        if protos is not None and coeffs is not None:
            mh, mw = protos.shape[1:]
            m = 1.0 / (1.0 + np.exp(-(coeffs[i] @ protos.reshape(nm, -1)))).reshape(mh, mw)
            sx, sy = mw / lb.imgsz, mh / lb.imgsz
            x0, y0 = int(max(0, b[0] * sx)), int(max(0, b[1] * sy))
            x1, y1 = int(min(mw, np.ceil(b[2] * sx))), int(min(mh, np.ceil(b[3] * sy)))
            crop = np.zeros_like(m, dtype=bool)
            crop[y0:y1, x0:x1] = m[y0:y1, x0:x1] > 0.5
            ring = mask_to_polygon(crop, simplify_px=simplify_px * sx)
            if ring is not None:
                pts = np.asarray(ring) / np.array([sx, sy])
                poly_px = lb.to_original(pts).round(1).tolist()
        bb = lb.to_original(np.array([[b[0], b[1]], [b[2], b[3]]])).round(1)
        bbox = [float(bb[0, 0]), float(bb[0, 1]), float(bb[1, 0]), float(bb[1, 1])]
        if poly_px is None:
            poly_px = [
                [bbox[0], bbox[1]],
                [bbox[2], bbox[1]],
                [bbox[2], bbox[3]],
                [bbox[0], bbox[3]],
                [bbox[0], bbox[1]],
            ]
        instances.append(Instance(int(cls[i]), float(conf[i]), bbox, poly_px))
    return instances
