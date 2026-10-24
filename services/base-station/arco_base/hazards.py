"""Detections -> hazard rows on the blackboard (REST), with stable ids and change gating.

One hazard per (source_id, hazard_class): its id is `uuid5(NAMESPACE, "source:class")`
so consecutive frames UPDATE the same row (the blackboard emits `hazard.updated`, the
Cartographer recomputes the links) instead of flooding the board with duplicates. A
hazard is re-posted when its footprint area changed by more than
`change_fraction`, or at least every `repost_s` so `last_seen` keeps it alive.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from shapely.geometry import Polygon as ShapelyPolygon

from arco_edge.logging import get_logger
from arco_schemas import Detection, Hazard
from arco_schemas.geo import Polygon
from arco_schemas.hazard_classes import StreamKind
from arco_schemas.vision import DetectorKind

log = get_logger("base.hazards")
NAMESPACE = uuid.UUID("9c3f2b2a-5f3b-4a52-9b6e-1a2d3c4e5f60")


def hazard_id(source_id: str, hazard_class: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"{source_id}:{hazard_class}")


@dataclass
class _Tracked:
    first_seen: datetime
    last_post_wall: float
    last_area: float


@dataclass
class WriteResult:
    posted: int = 0
    skipped: int = 0
    failed: int = 0
    db_write_ms: float = 0.0
    hazard_ids: list[str] = field(default_factory=list)


class HazardWriter:
    def __init__(
        self,
        blackboard_url: str,
        *,
        ttl_s: float,
        default_severity: float,
        repost_s: float,
        change_fraction: float,
        min_confidence: float,
        transport: httpx.BaseTransport | None = None,
        timeout_s: float = 2.0,
    ) -> None:
        self.base = blackboard_url.rstrip("/")
        self.ttl_s = ttl_s
        self.default_severity = default_severity
        self.repost_s = repost_s
        self.change_fraction = change_fraction
        self.min_confidence = min_confidence
        self._client = httpx.Client(base_url=self.base, timeout=timeout_s, transport=transport)
        self._tracked: dict[uuid.UUID, _Tracked] = {}
        self.last_error: str | None = None

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def source_for(detector: DetectorKind, stream: StreamKind) -> str:
        if detector == "synthetic":
            return "simulated"
        return "macro_vision" if stream == "macro" else "micro_vision"

    def build(
        self,
        det: Detection,
        *,
        stream: StreamKind,
        source_id: str,
        detector: DetectorKind,
        now: datetime,
    ) -> Hazard | None:
        if det.footprint is None or len(det.footprint) < 4 or det.confidence < self.min_confidence:
            return None
        hid = hazard_id(source_id, det.hazard_class.value)
        tracked = self._tracked.get(hid)
        first_seen = tracked.first_seen if tracked else now
        return Hazard(
            id=hid,
            geom=Polygon(coordinates=[det.footprint]),
            hazard_class=det.hazard_class,
            confidence=det.confidence,
            source=self.source_for(detector, stream),  # type: ignore[arg-type]
            stream=stream,
            camera_id=source_id,
            first_seen=first_seen,
            last_seen=now,
            ttl_s=self.ttl_s,
            status="active",
            severity=self.default_severity,
            velocity_m_s=None,
        )

    def _should_post(self, h: Hazard, wall: float) -> bool:
        ring = h.geom.coordinates[0] if isinstance(h.geom, Polygon) else None
        area = ShapelyPolygon(ring).area if ring else 0.0
        tracked = self._tracked.get(h.id)
        if tracked is None:
            self._tracked[h.id] = _Tracked(h.first_seen, wall, area)
            return True
        changed = (
            abs(area - tracked.last_area) > self.change_fraction * max(tracked.last_area, 1e-12)
            if tracked.last_area > 0
            else area > 0
        )
        if changed or (wall - tracked.last_post_wall) >= self.repost_s:
            tracked.last_post_wall = wall
            tracked.last_area = area
            return True
        return False

    def write(
        self,
        detections: list[Detection],
        *,
        stream: StreamKind,
        source_id: str,
        detector: DetectorKind,
        scenario_t: float | None,
    ) -> WriteResult:
        res = WriteResult()
        now = datetime.now(UTC)
        wall = time.monotonic()
        t0 = time.perf_counter()
        for det in detections:
            h = self.build(det, stream=stream, source_id=source_id, detector=detector, now=now)
            if h is None:
                continue
            if not self._should_post(h, wall):
                res.skipped += 1
                continue
            params: dict[str, Any] = {}
            if scenario_t is not None:
                params["scenario_t"] = scenario_t
            try:
                r = self._client.post("/hazards", params=params, json=h.model_dump(mode="json"))
                r.raise_for_status()
                res.posted += 1
                res.hazard_ids.append(str(h.id))
                self.last_error = None
            except (httpx.HTTPError, ValueError) as exc:
                res.failed += 1
                self.last_error = str(exc)
                log.warning("hazard post failed", error=str(exc), hazard=str(h.id))
        res.db_write_ms = (time.perf_counter() - t0) * 1000.0
        return res
