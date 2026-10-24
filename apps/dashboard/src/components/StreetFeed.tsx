"use client";

/**
 * Street-camera panel, synchronised to the scenario clock: polls the simulator's synthetic
 * frame for the selected micro feed at the current `t` and draws the latest micro-stream
 * detections (boxes / masks / class + confidence) on a canvas on top. The frames are
 * watermarked "SIMULATED street feed" by the simulator itself; this panel adds the
 * scenario label and shows explicitly when no detection stream is connected.
 */
import { useEffect, useMemo, useRef, useState } from "react";

import { HAZARD_CLASSES, SIMULATION_LABEL } from "@arco/schemas";
import type { DetectionsMessage } from "@arco/schemas";

import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { fmtClock } from "@/lib/utils";
import { displayClockT, useWorld } from "@/store/world";

const POLL_MS = 250;

export function drawDetections(
  ctx: CanvasRenderingContext2D,
  det: DetectionsMessage,
  width: number,
  height: number,
): void {
  const sx = width / det.image_width;
  const sy = height / det.image_height;
  ctx.clearRect(0, 0, width, height);
  ctx.lineWidth = 2;
  ctx.font = "11px ui-monospace, Menlo, monospace";
  for (const d of det.detections) {
    const color = HAZARD_CLASSES[d.hazard_class].color;
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    if (d.mask_polygon_px && d.mask_polygon_px.length >= 3) {
      ctx.beginPath();
      d.mask_polygon_px.forEach((pt, i) => {
        const x = (pt[0] ?? 0) * sx;
        const y = (pt[1] ?? 0) * sy;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.closePath();
      ctx.globalAlpha = 0.25;
      ctx.fill();
      ctx.globalAlpha = 1;
      ctx.stroke();
    }
    const [x1, y1, x2, y2] = d.bbox_px;
    ctx.strokeRect(x1 * sx, y1 * sy, (x2 - x1) * sx, (y2 - y1) * sy);
    const label = `${HAZARD_CLASSES[d.hazard_class].label} ${(d.confidence * 100).toFixed(0)}%`;
    const tw = ctx.measureText(label).width + 6;
    ctx.fillRect(x1 * sx, Math.max(0, y1 * sy - 14), tw, 14);
    ctx.fillStyle = "#07090d";
    ctx.fillText(label, x1 * sx + 3, Math.max(11, y1 * sy - 3));
  }
}

export function StreetFeed() {
  const scenario = useWorld((s) => s.scenario);
  const mode = useWorld((s) => s.mode);
  const detections = useWorld((s) => s.detections.micro);
  const detConn = useWorld((s) => s.connections.detections);
  const feeds = useMemo(() => scenario?.micro_feeds ?? [], [scenario]);
  const [cameraId, setCameraId] = useState<string | null>(null);
  const [src, setSrc] = useState<string | null>(null);
  const [frameT, setFrameT] = useState(0);
  const [failed, setFailed] = useState(false);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const camera = feeds.find((f) => f.camera_id === cameraId) ?? feeds[0] ?? null;

  useEffect(() => {
    if (!camera || mode !== "live") return;
    let cancelled = false;
    const poll = () => {
      if (cancelled) return;
      const t = displayClockT(useWorld.getState().clock);
      setFrameT(t);
      setSrc(`${api.sim.frameUrl(camera.camera_id, t)}&_=${Math.floor(t * 10)}`);
    };
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [camera, mode]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas || !img) return;
    const w = img.clientWidth || 640;
    const h = img.clientHeight || 360;
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    if (detections && detections.source_id === camera?.camera_id)
      drawDetections(ctx, detections, w, h);
    else ctx.clearRect(0, 0, w, h);
  }, [detections, camera, src]);

  return (
    <section className="panel flex h-full flex-col" data-testid="street-feed">
      <div className="panel-title">
        <span>street camera · {camera?.camera_id ?? "—"}</span>
        <span className="flex items-center gap-1">
          {feeds.length > 1 && (
            <select
              className="h-5 rounded border border-ops-edge bg-ops-bg px-1 text-[10px] text-ops-text"
              value={camera?.camera_id ?? ""}
              onChange={(e) => {
                setCameraId(e.target.value);
              }}
            >
              {feeds.map((f) => (
                <option key={f.camera_id} value={f.camera_id}>
                  {f.camera_id}
                </option>
              ))}
            </select>
          )}
          <Badge tone="warn">synthetic</Badge>
          <Badge tone={detConn === "open" ? "ok" : "neutral"}>
            {detConn === "open"
              ? `detections ${detections?.detector ?? "—"}`
              : "no detection stream"}
          </Badge>
        </span>
      </div>
      <div className="relative flex-1 overflow-hidden bg-black">
        {mode === "live" && src && !failed ? (
          <>
            <img
              ref={imgRef}
              src={src}
              alt={`Synthetic street feed ${camera?.camera_id ?? ""} at t=${frameT.toFixed(1)} s`}
              className="h-full w-full object-contain"
              onError={() => {
                setFailed(true);
              }}
              onLoad={() => {
                setFailed(false);
              }}
            />
            <canvas
              ref={canvasRef}
              className="pointer-events-none absolute inset-0 h-full w-full"
            />
          </>
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-1 font-mono text-[11px] text-ops-dim">
            <span>
              {mode === "replay"
                ? "recorded replay: no live street feed"
                : "street feed unavailable"}
            </span>
            <span className="text-[10px]">{SIMULATION_LABEL}</span>
          </div>
        )}
        <div className="pointer-events-none absolute bottom-1 left-1 flex gap-1 font-mono text-[10px]">
          <span className="rounded-sm bg-ops-bg/80 px-1 text-ops-dim">t {fmtClock(frameT)}</span>
          {camera && (
            <span className="rounded-sm bg-ops-bg/80 px-1 text-ops-dim">
              {camera.synthetic_overlays?.length ?? 0} scripted overlays
            </span>
          )}
        </div>
      </div>
    </section>
  );
}
