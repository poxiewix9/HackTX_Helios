"""Deterministic YOLO-style letterbox preprocessing.

Both ends use this: the edge to build the model input, the base station to map
detections back to original pixels. Because the mapping is a pure function of
(image_width, image_height, imgsz) the packet header needs no extra fields.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class Letterbox:
    imgsz: int
    ratio: float  # original -> letterboxed scale
    pad_x: float  # left padding in letterboxed pixels
    pad_y: float
    width: int  # original size
    height: int

    @classmethod
    def for_size(cls, width: int, height: int, imgsz: int) -> Letterbox:
        ratio = min(imgsz / height, imgsz / width)
        new_w, new_h = round(width * ratio), round(height * ratio)
        pad_x = (imgsz - new_w) / 2.0
        pad_y = (imgsz - new_h) / 2.0
        return cls(imgsz, ratio, pad_x, pad_y, width, height)

    def to_original(self, pts: np.ndarray) -> np.ndarray:
        """Letterboxed pixel coords (N,2) -> original image pixels, clipped to the frame."""
        out: np.ndarray = (pts - np.array([self.pad_x, self.pad_y])) / self.ratio
        out[:, 0] = np.clip(out[:, 0], 0, self.width - 1)
        out[:, 1] = np.clip(out[:, 1], 0, self.height - 1)
        return out


def letterbox_tensor(frame_bgr: np.ndarray, imgsz: int) -> tuple[np.ndarray, Letterbox]:
    """BGR HxWx3 uint8 -> float32 NCHW RGB in [0,1], padded with 114 like Ultralytics."""
    h, w = frame_bgr.shape[:2]
    lb = Letterbox.for_size(w, h, imgsz)
    new_w, new_h = round(w * lb.ratio), round(h * lb.ratio)
    resized = cv2.resize(frame_bgr, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((imgsz, imgsz, 3), 114, dtype=np.uint8)
    x0, y0 = round(lb.pad_x - 0.1), round(lb.pad_y - 0.1)
    canvas[y0 : y0 + new_h, x0 : x0 + new_w] = resized
    rgb = canvas[:, :, ::-1]
    tensor: np.ndarray = rgb.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    return np.ascontiguousarray(tensor), lb
