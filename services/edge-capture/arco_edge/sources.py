"""Frame sources for the edge node: camera / video / RTSP (OpenCV), image folders, and a
clearly-labelled synthetic generator used when no footage is available."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}


@dataclass(frozen=True)
class Frame:
    image: np.ndarray  # BGR uint8 HxWx3 — stays in this process
    frame_id: int
    captured_wall_t: float
    scenario_t: float | None
    capture_ms: float


class FrameSource(Protocol):
    label: str

    def frames(self) -> Iterator[Frame]: ...

    def close(self) -> None: ...


class CaptureSource:
    """cv2.VideoCapture over a device index, file path or RTSP/HTTP URL."""

    def __init__(self, uri: str, *, loop: bool = False, max_fps: float | None = None) -> None:
        self.label = uri
        self._uri: int | str = int(uri) if uri.isdigit() else uri
        self._loop = loop
        self._min_dt = 1.0 / max_fps if max_fps else 0.0
        self._cap = cv2.VideoCapture(self._uri)
        if not self._cap.isOpened():
            raise RuntimeError(f"cannot open capture source {uri!r}")

    def frames(self) -> Iterator[Frame]:
        fid = 0
        last = 0.0
        while True:
            t0 = time.perf_counter()
            ok, img = self._cap.read()
            if not ok:
                if self._loop and isinstance(self._uri, str):
                    self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                return
            now = time.time()
            if self._min_dt and (now - last) < self._min_dt:
                time.sleep(self._min_dt - (now - last))
                now = time.time()
            last = now
            yield Frame(img, fid, now, None, (time.perf_counter() - t0) * 1000.0)
            fid += 1

    def close(self) -> None:
        self._cap.release()


class ImageDirSource:
    """Sorted images in a folder; `scenario_t` advances by 1/fps per image."""

    def __init__(self, folder: Path, *, fps: float = 5.0, loop: bool = False) -> None:
        self.label = str(folder)
        self._paths = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
        if not self._paths:
            raise RuntimeError(f"no images in {folder}")
        self._fps = fps
        self._loop = loop

    def frames(self) -> Iterator[Frame]:
        fid = 0
        while True:
            for p in self._paths:
                t0 = time.perf_counter()
                img = cv2.imread(str(p), cv2.IMREAD_COLOR)
                if img is None:
                    continue
                yield Frame(
                    img, fid, time.time(), fid / self._fps, (time.perf_counter() - t0) * 1e3
                )
                fid += 1
            if not self._loop:
                return

    def close(self) -> None:
        return None


class SyntheticSource:
    """SIMULATED footage: flat scene with moving coloured blobs. Exists so the whole
    pipeline (capture -> head -> packet -> tail -> polygons -> georef -> blackboard) can be
    exercised and timed without video. Detections on it are meaningless unless the
    synthetic detector is selected, and the stream is labelled `synthetic`."""

    label = "synthetic"

    def __init__(
        self,
        *,
        width: int,
        height: int,
        fps: float = 10.0,
        seed: int = 0,
        stream: str = "micro",
        realtime: bool = False,
    ) -> None:
        self._w, self._h, self._fps = width, height, fps
        self._rng = np.random.default_rng(seed)
        self._stream = stream
        self._realtime = realtime

    def frames(self) -> Iterator[Frame]:
        fid = 0
        base = np.full((self._h, self._w, 3), (90, 90, 90), dtype=np.uint8)
        if self._stream == "macro":
            base[:] = (60, 120, 60)  # terrain-ish green, BGR
        cv2.rectangle(base, (0, int(self._h * 0.55)), (self._w, self._h), (70, 70, 70), -1)
        noise = self._rng.integers(0, 12, size=base.shape, dtype=np.uint8)
        base = np.asarray(cv2.add(base, noise), dtype=np.uint8)
        while True:
            t0 = time.perf_counter()
            img = base.copy()
            t = fid / self._fps
            # a slowly growing orange blob (fire-like) and a drifting grey blob (smoke-like)
            r = int(20 + 3 * t)
            cv2.circle(img, (int(self._w * 0.3), int(self._h * 0.7)), min(r, 120), (0, 90, 255), -1)
            cx = int(self._w * 0.6 + 8 * t) % self._w
            cv2.ellipse(img, (cx, int(self._h * 0.35)), (70, 30), 0, 0, 360, (160, 160, 160), -1)
            yield Frame(img, fid, time.time(), t, (time.perf_counter() - t0) * 1000.0)
            fid += 1
            if self._realtime:
                time.sleep(1.0 / self._fps)

    def close(self) -> None:
        return None


def make_source(
    uri: str,
    *,
    width: int,
    height: int,
    fps: float,
    loop: bool,
    stream: str,
    realtime: bool,
) -> FrameSource:
    """`synthetic` | `synthetic:<seed>` | directory | file/device/URL."""
    if uri == "synthetic" or uri.startswith("synthetic:"):
        seed = int(uri.split(":", 1)[1]) if ":" in uri else 0
        return SyntheticSource(
            width=width, height=height, fps=fps, seed=seed, stream=stream, realtime=realtime
        )
    p = Path(uri)
    if p.is_dir():
        return ImageDirSource(p, fps=fps, loop=loop)
    return CaptureSource(uri, loop=loop, max_fps=fps if realtime else None)
