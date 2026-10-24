"""Edge node loop: capture -> head -> packet -> link. Emits per-frame timings to the log;
the base station turns them into `TelemetrySample`s (it owns the remaining stages)."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from arco_edge.client import Link, WebSocketLink
from arco_edge.head import Head, OnnxHead, SyntheticHead
from arco_edge.logging import configure_logging, get_logger
from arco_edge.packet import encode
from arco_edge.settings import EdgeSettings, get_settings
from arco_edge.sources import FrameSource, make_source
from arco_schemas.vision import Georef

log = get_logger("edge.main")
_georef_adapter: TypeAdapter[Any] = TypeAdapter(Georef)


def load_georef(path: Path) -> Any:
    return _georef_adapter.validate_python(json.loads(path.read_text()))


@dataclass
class EdgeStats:
    frames: int = 0
    bytes_sent: int = 0
    head_ms: list[float] = field(default_factory=list)
    serialize_ms: list[float] = field(default_factory=list)
    capture_ms: list[float] = field(default_factory=list)

    def summary(self, dropped: int) -> dict[str, float | int]:
        def mean(xs: list[float]) -> float:
            return round(sum(xs) / len(xs), 2) if xs else 0.0

        return {
            "frames": self.frames,
            "dropped": dropped,
            "bytes_sent": self.bytes_sent,
            "mean_capture_ms": mean(self.capture_ms),
            "mean_head_ms": mean(self.head_ms),
            "mean_serialize_ms": mean(self.serialize_ms),
            "mean_packet_bytes": int(self.bytes_sent / self.frames) if self.frames else 0,
        }


def make_head(settings: EdgeSettings) -> Head:
    if settings.arco_detector == "synthetic":
        return SyntheticHead()
    return OnnxHead(
        settings.arco_models_dir / settings.arco_model_id, quantized=settings.arco_head_quantized
    )


class EdgeNode:
    def __init__(
        self,
        settings: EdgeSettings,
        *,
        source: FrameSource | None = None,
        head: Head | None = None,
        link: Link | None = None,
    ) -> None:
        self.settings = settings
        self.georef = load_georef(settings.arco_edge_georef_path)
        self.source = source or make_source(
            settings.arco_edge_source,
            width=settings.arco_edge_width,
            height=settings.arco_edge_height,
            fps=settings.arco_edge_fps,
            loop=settings.arco_edge_loop,
            stream=settings.arco_edge_stream,
            realtime=settings.arco_edge_realtime,
        )
        self.head = head or make_head(settings)
        self.link = link or WebSocketLink(
            settings.arco_base_station_url,
            queue_size=settings.arco_edge_queue,
            allow_remote=settings.arco_allow_remote_base,
        )
        self.stats = EdgeStats()
        self.source_id = (
            self.georef.camera_id if self.georef.kind == "camera" else self.georef.tile_id
        )

    def run(self, max_frames: int | None = None) -> EdgeStats:
        limit = max_frames if max_frames is not None else self.settings.arco_edge_frames
        try:
            for frame in self.source.frames():
                tensor, _lb, head_ms = self.head.run(frame.image)
                enc = encode(
                    tensor,
                    header_fields={
                        "stream": self.settings.arco_edge_stream,
                        "frame_id": frame.frame_id,
                        "source_id": self.source_id,
                        "captured_wall_t": frame.captured_wall_t,
                        "scenario_t": frame.scenario_t,
                        "model_id": self.head.model_id,
                        "split_layer": self.head.split_layer,
                        "head_ms": head_ms,
                        "georef": self.georef,
                    },
                )
                self.link.send(enc.frame)
                self.stats.frames += 1
                self.stats.bytes_sent += len(enc.frame)
                self.stats.head_ms.append(head_ms)
                self.stats.serialize_ms.append(enc.serialize_ms)
                self.stats.capture_ms.append(frame.capture_ms)
                if self.stats.frames % 50 == 0:
                    log.info("edge", **self.stats.summary(self.link.dropped))
                if limit is not None and self.stats.frames >= limit:
                    break
        finally:
            self.source.close()
            self.link.close()
        log.info("edge done", **self.stats.summary(self.link.dropped))
        return self.stats


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCO edge node")
    parser.add_argument("--frames", type=int, default=None)
    args = parser.parse_args()
    configure_logging("edge-capture")
    settings = get_settings()
    node = EdgeNode(settings)
    log.info(
        "edge starting",
        detector=settings.arco_detector,
        source=node.source.label,
        head=node.head.model_id,
        base=settings.arco_base_station_url,
        stream=settings.arco_edge_stream,
    )
    t0 = time.perf_counter()
    stats = node.run(args.frames)
    log.info("edge finished", wall_s=round(time.perf_counter() - t0, 2), frames=stats.frames)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
