"use client";

/**
 * Demo street cameras. Local stock footage plays in a monitor frame with a dense,
 * hardcoded track overlay (people, vehicles, cycles, buses) so the panel reads as a
 * live detector. Boxes are scripted — they are not the simulator's synthetic feed.
 */
import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";

import { Badge } from "@/components/ui/badge";
import {
  DEMO_CAMERAS,
  TRACK_COLOR,
  TRACK_LABEL,
  cameraById,
  countKinds,
  coveredRect,
  tracksAt,
} from "@/components/camera/demoTracks";
import type { FrameRect, TrackBox, TrackKind } from "@/components/camera/demoTracks";
import { fmtClock } from "@/lib/utils";

const KINDS: readonly TrackKind[] = ["person", "vehicle", "cyclist", "bus"];

export function StreetFeed() {
  const [cameraId, setCameraId] = useState(DEMO_CAMERAS[0]?.id ?? "cam-front");
  const camera = cameraById(cameraId);
  const [boxes, setBoxes] = useState<TrackBox[]>(() => tracksAt(camera.id, 0));
  const [clock, setClock] = useState(0);
  const [layout, setLayout] = useState<FrameRect>({ x: 0, y: 0, w: 0, h: 0 });
  const [ready, setReady] = useState(false);
  const frameRef = useRef<HTMLDivElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  useEffect(() => {
    const frame = frameRef.current;
    const video = videoRef.current;
    if (!frame || !video) return;
    video.muted = true;
    void video.play().catch(() => undefined);
    let raf = 0;
    const tick = () => {
      const next = coveredRect(
        video.videoWidth,
        video.videoHeight,
        frame.clientWidth,
        frame.clientHeight,
      );
      const t = Number.isFinite(video.currentTime) ? video.currentTime : 0;
      setLayout(next);
      setClock(t);
      setBoxes(tracksAt(camera.id, t));
      setReady(video.videoWidth > 0);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
    };
  }, [camera.id]);

  const counts = countKinds(boxes);
  const ordered = [...boxes].sort((a, b) => b.w * b.h - a.w * a.h);

  return (
    <section className="panel flex h-full min-h-0 flex-col" data-testid="street-feed">
      <div className="panel-title">
        <div className="flex items-center gap-2 text-ops-text">
          <span>Street camera</span>
          <span className="font-normal text-ops-dim">{camera.place}</span>
        </div>
        <div className="flex items-center gap-2">
          <select
            className="h-7 rounded-md border border-white/10 bg-black/30 px-2 font-mono text-[11px] text-ops-text outline-none"
            value={camera.id}
            data-testid="camera-select"
            onChange={(e) => {
              setCameraId(e.target.value);
              setReady(false);
            }}
          >
            {DEMO_CAMERAS.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name} · {item.place}
              </option>
            ))}
          </select>
          <Badge tone="accent">{boxes.length} tracks</Badge>
        </div>
      </div>
      <div
        ref={frameRef}
        className="relative min-h-0 flex-1 overflow-hidden bg-black"
        data-camera-frame
        data-ready={ready ? "yes" : "no"}
      >
        <video
          ref={videoRef}
          key={camera.src}
          src={camera.src}
          className="absolute inset-0 h-full w-full object-cover"
          autoPlay
          muted
          loop
          playsInline
          preload="auto"
          aria-label={`${camera.name} ${camera.place} demo footage`}
        />
        <div
          className="pointer-events-none absolute"
          style={{ left: layout.x, top: layout.y, width: layout.w, height: layout.h }}
        >
          {ready && ordered.map((box) => <TrackOutline key={box.id} box={box} />)}
        </div>
        <div className="pointer-events-none absolute inset-0">
          <div className="absolute left-3 top-3 h-4 w-4 border-l-2 border-t-2 border-white/80" />
          <div className="absolute right-3 top-3 h-4 w-4 border-r-2 border-t-2 border-white/80" />
          <div className="absolute bottom-3 left-3 h-4 w-4 border-b-2 border-l-2 border-white/80" />
          <div className="absolute bottom-3 right-3 h-4 w-4 border-b-2 border-r-2 border-white/80" />
          <div className="absolute inset-x-0 top-0 flex items-center justify-between bg-gradient-to-b from-black/80 via-black/35 to-transparent px-3 pb-6 pt-2.5 font-mono text-[11px] text-white">
            <span className="flex items-center gap-2">
              <span className="h-1.5 w-1.5 rounded-full bg-red-500 shadow-[0_0_8px_#ef4444]" />
              <span className="tracking-[0.18em]">REC</span>
              <span className="text-white/55">{camera.name}</span>
              <span>{fmtClock(clock)}</span>
            </span>
            <span className="rounded bg-black/40 px-1.5 py-0.5 text-white/90">
              {boxes.length} tracking
            </span>
          </div>
          <div className="absolute inset-x-0 bottom-0 flex items-center justify-between gap-3 bg-gradient-to-t from-black/85 via-black/40 to-transparent px-3 pb-2.5 pt-8 font-mono text-[10px] text-white/85">
            <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
              {KINDS.map((kind) =>
                counts[kind] > 0 ? (
                  <span key={kind} className="flex items-center gap-1">
                    <span
                      className="inline-block h-1.5 w-1.5 rounded-[2px]"
                      style={{ background: TRACK_COLOR[kind] }}
                    />
                    {counts[kind]} {TRACK_LABEL[kind]}
                  </span>
                ) : null,
              )}
            </span>
            <span className="shrink-0 rounded border border-white/20 bg-black/40 px-1.5 py-0.5 tracking-[0.16em] text-white/70">
              DEMO
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}

function TrackOutline({ box }: { box: TrackBox }) {
  const color = TRACK_COLOR[box.kind];
  const style: CSSProperties = {
    left: `${box.x * 100}%`,
    top: `${box.y * 100}%`,
    width: `${box.w * 100}%`,
    height: `${box.h * 100}%`,
    borderColor: color,
    boxShadow: "0 0 0 1px rgba(0,0,0,0.55)",
  };
  return (
    <div className="absolute border-[1.5px]" style={style}>
      <span
        className="absolute left-0 top-0 whitespace-nowrap px-1 font-mono text-[9px] font-semibold leading-[14px]"
        style={{ background: color, color: "#081018" }}
      >
        {box.id} {Math.round(box.confidence * 100)}
      </span>
    </div>
  );
}
