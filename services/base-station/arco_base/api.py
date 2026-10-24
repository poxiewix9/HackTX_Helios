"""Base-station API.

Ingress (from edge nodes on the local network): `WS /ws/features` (binary msgpack frames)
and `POST /features`. Egress: `WS /ws/detections` (`DetectionsMessage`), `WS /ws/telemetry`
(`TelemetryMessage`), Prometheus `/metrics`, `/health`. Hazards go to the blackboard REST.

There is deliberately no endpoint that accepts or forwards images.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

from arco_base.detectors import (
    Detector,
    SplitDetector,
    SyntheticDetector,
    select_providers,
)
from arco_base.hazards import HazardWriter
from arco_base.settings import BaseStationSettings, get_settings
from arco_base.telemetry import TelemetryHub
from arco_edge.logging import configure_logging, get_logger
from arco_edge.packet import decode
from arco_schemas import DetectionsMessage, TelemetryMessage
from arco_schemas.telemetry import PipelineKind
from arco_schemas.vision import DetectorKind

log = get_logger("base.api")


class HealthResponse(BaseModel):
    status: str
    version: str
    detector: str
    model_id: str
    providers: list[str]
    blackboard_url: str
    frames: int
    streams: dict[str, dict[str, float | int]]
    last_total_ms: float | None
    last_error: str | None
    ws_clients: dict[str, int]


class Broadcaster:
    """Fan-out of JSON text frames to WebSocket clients with bounded per-client queues
    (slow clients drop, the pipeline never blocks)."""

    def __init__(self, maxsize: int = 64) -> None:
        self._queues: set[asyncio.Queue[str]] = set()
        self._maxsize = maxsize

    @property
    def clients(self) -> int:
        return len(self._queues)

    def publish(self, text: str) -> None:
        for q in list(self._queues):
            if q.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            q.put_nowait(text)

    async def serve(self, ws: WebSocket) -> None:
        await ws.accept()
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=self._maxsize)
        self._queues.add(q)

        async def pump() -> None:
            while True:
                await ws.send_text(await q.get())

        async def watch_close() -> None:
            # a send-only loop never learns the peer left; draining the receive side
            # surfaces WebSocketDisconnect promptly instead of at server shutdown
            while True:
                await ws.receive_text()

        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(pump())
                tg.create_task(watch_close())
        except* (WebSocketDisconnect, RuntimeError):
            pass  # RuntimeError: starlette raises it when sending after the peer closed
        finally:
            self._queues.discard(q)


_PIPELINE_FOR: dict[DetectorKind, PipelineKind] = {
    "onnx_split": "int8_split",
    "onnx_full": "fp32_full",
    "synthetic": "synthetic",
}


@dataclass
class Runtime:
    settings: BaseStationSettings
    detector: Detector
    writer: HazardWriter
    hub: TelemetryHub
    detections_bus: Broadcaster = field(default_factory=Broadcaster)
    telemetry_bus: Broadcaster = field(default_factory=Broadcaster)
    seq: int = 0
    frames: int = 0
    last_total_ms: float | None = None
    last_error: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def process(
        self, frame: bytes, received_wall_t: float
    ) -> tuple[DetectionsMessage, TelemetryMessage]:
        """Whole base-station path for one packet, synchronously (runs in a worker thread)."""
        pkt = decode(frame)
        h = pkt.header
        det = self.detector.detect(pkt)
        wr = self.writer.write(
            det.detections,
            stream=h.stream,
            source_id=h.source_id,
            detector=self.detector.kind,
            scenario_t=h.scenario_t,
        )
        stage_ms = {
            "capture": 0.0,  # the edge does not send capture_ms (not in the header contract)
            "head": h.head_ms,
            "serialize": h.serialize_ms,
            "deserialize": pkt.deserialize_ms,
            "db_write": wr.db_write_ms,
            **det.stage_ms,
        }
        pipeline: PipelineKind = _PIPELINE_FOR[self.detector.kind]
        sample = self.hub.record(
            stream=h.stream,
            pipeline=pipeline,
            frame_id=h.frame_id,
            scenario_t=h.scenario_t,
            captured_wall_t=h.captured_wall_t,
            received_wall_t=received_wall_t,
            stage_ms=stage_ms,
            bytes_received=pkt.frame_bytes,
        )
        self.seq += 1
        self.frames += 1
        self.last_total_ms = sample.total_ms
        scenario_t = h.scenario_t if h.scenario_t is not None else -1.0
        now = datetime.now(UTC)
        msg = DetectionsMessage(
            scenario_t=scenario_t,
            wall_t=now,
            seq=self.seq,
            stream=h.stream,
            source_id=h.source_id,
            frame_id=h.frame_id,
            detector=self.detector.kind,
            image_width=det.image_width,
            image_height=det.image_height,
            detections=det.detections,
            latency_ms=sample.total_ms,
        )
        tele = TelemetryMessage(scenario_t=scenario_t, wall_t=now, seq=self.seq, sample=sample)
        return msg, tele

    async def handle(self, frame: bytes) -> DetectionsMessage:
        received = time.time()
        async with self.lock:  # one packet at a time: ordered, no ORT contention
            try:
                msg, tele = await asyncio.to_thread(self.process, frame, received)
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("packet failed")
                raise
        self.detections_bus.publish(msg.model_dump_json())
        self.telemetry_bus.publish(tele.model_dump_json())
        return msg


def make_detector(settings: BaseStationSettings) -> Detector:
    if settings.arco_detector == "synthetic":
        return SyntheticDetector(settings.arco_synthetic_schedule_path)
    if settings.arco_detector == "onnx_split":
        return SplitDetector(
            settings.arco_models_dir / settings.arco_model_id,
            settings.arco_class_map_path,
            providers=select_providers(settings.arco_ort_providers),
            conf_thr=settings.arco_conf_threshold,
            iou_thr=settings.arco_iou_threshold,
        )
    raise ValueError(
        "ARCO_DETECTOR=onnx_full needs frames and only runs in-process (benchmark); "
        "the base station accepts onnx_split or synthetic"
    )


def make_runtime(
    settings: BaseStationSettings, *, blackboard_transport: httpx.BaseTransport | None = None
) -> Runtime:
    detector = make_detector(settings)
    writer = HazardWriter(
        settings.blackboard_url,
        ttl_s=settings.arco_hazard_ttl_s,
        default_severity=settings.arco_hazard_default_severity,
        repost_s=settings.arco_hazard_repost_s,
        change_fraction=settings.arco_hazard_change_fraction,
        min_confidence=settings.arco_hazard_min_confidence,
        transport=blackboard_transport,
    )
    return Runtime(settings, detector, writer, TelemetryHub(window=settings.arco_telemetry_window))


def create_app(
    settings: BaseStationSettings | None = None,
    *,
    blackboard_transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging("base-station")

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        rt = make_runtime(settings, blackboard_transport=blackboard_transport)
        app.state.runtime = rt
        log.info(
            "base station up",
            detector=rt.detector.kind,
            model=rt.detector.model_id,
            providers=getattr(rt.detector, "providers", []),
            blackboard=settings.blackboard_url,
        )
        try:
            yield
        finally:
            rt.writer.close()

    app = FastAPI(title="ARCO base station", version="0.1.0", lifespan=lifespan)

    def runtime(request: Request | WebSocket) -> Runtime:
        rt: Runtime = request.app.state.runtime
        return rt

    @app.get("/health", response_model=HealthResponse)
    def health(request: Request) -> HealthResponse:
        rt = runtime(request)
        return HealthResponse(
            status="ok",
            version="0.1.0",
            detector=rt.detector.kind,
            model_id=rt.detector.model_id,
            providers=list(getattr(rt.detector, "providers", [])),
            blackboard_url=settings.blackboard_url,
            frames=rt.frames,
            streams=rt.hub.summary(),
            last_total_ms=rt.last_total_ms,
            last_error=rt.last_error or rt.writer.last_error,
            ws_clients={
                "detections": rt.detections_bus.clients,
                "telemetry": rt.telemetry_bus.clients,
            },
        )

    @app.get("/metrics")
    def metrics(request: Request) -> Response:
        return Response(
            generate_latest(runtime(request).hub.registry), media_type=CONTENT_TYPE_LATEST
        )

    @app.post("/features", response_model=DetectionsMessage)
    async def post_features(request: Request) -> DetectionsMessage:
        body = await request.body()
        return await runtime(request).handle(body)

    @app.websocket("/ws/features")
    async def ws_features(ws: WebSocket) -> None:
        await ws.accept()
        rt = runtime(ws)
        try:
            while True:
                data = await ws.receive_bytes()
                with contextlib.suppress(Exception):  # logged in handle(); keep the link up
                    await rt.handle(data)
        except WebSocketDisconnect:
            pass

    @app.websocket("/ws/detections")
    async def ws_detections(ws: WebSocket) -> None:
        await runtime(ws).detections_bus.serve(ws)

    @app.websocket("/ws/telemetry")
    async def ws_telemetry(ws: WebSocket) -> None:
        await runtime(ws).telemetry_bus.serve(ws)

    return app


def main() -> int:
    import uvicorn  # noqa: PLC0415 - server-only dependency path

    settings = get_settings()
    uvicorn.run(
        "arco_base.api:app",
        host="0.0.0.0",
        port=settings.base_station_port,
        log_level=settings.arco_log_level.lower(),
    )
    return 0


app: Any = create_app()  # detector/model loading happens at startup (lifespan), not import

if __name__ == "__main__":
    raise SystemExit(main())
