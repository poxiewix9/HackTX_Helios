"use client";

/**
 * uPlot strip: FP32 full pipeline vs INT8 split total latency per frame (two series),
 * refreshed at animation-frame rate from the telemetry ring buffers; stage-by-stage bars
 * for the latest sample of each pipeline; bytes/frame; FPS; and the LIVE / RECORDED badge
 * straight from `TelemetrySample.mode` (or RECORDED REPLAY when the dashboard itself is
 * on the offline fallback).
 */
import { useEffect, useMemo, useRef } from "react";
import uPlot from "uplot";

import type { TelemetrySample } from "@arco/schemas";

import { Badge } from "@/components/ui/badge";
import { fmtBytes, fmtMs } from "@/lib/utils";
import { useWorld } from "@/store/world";
import type { Pipeline } from "@/store/world";

const STAGES = [
  "capture",
  "head",
  "serialize",
  "network",
  "deserialize",
  "tail",
  "postprocess",
  "georef",
  "db_write",
] as const;
const TARGET_MS = 80;
const COLORS: Record<Pipeline, string> = {
  fp32_full: "#ffb020",
  int8_split: "#36c4ff",
  synthetic: "#c77dff",
};

/** Build uPlot data: x = sample index (aligned by frame order), y = total_ms per pipeline. */
export function seriesData(by: Record<Pipeline, TelemetrySample[]>, n = 240): uPlot.AlignedData {
  const len = Math.max(by.fp32_full.length, by.int8_split.length, by.synthetic.length, 1);
  const count = Math.min(len, n);
  const xs: number[] = Array.from({ length: count }, (_, i) => i);
  const pick = (arr: TelemetrySample[]): (number | null)[] => {
    const tail = arr.slice(-count);
    const pad: (number | null)[] = Array.from({ length: count - tail.length }, () => null);
    return [...pad, ...tail.map((s) => s.total_ms)];
  };
  return [xs, pick(by.fp32_full), pick(by.int8_split), pick(by.synthetic)];
}

function StageBars({ sample }: { sample: TelemetrySample }) {
  const total = Math.max(
    1e-6,
    STAGES.reduce((a, s) => a + (sample.stage_ms[s] ?? 0), 0),
  );
  return (
    <div
      className="flex h-3 w-full overflow-hidden rounded-sm border border-ops-edge"
      title="stage-by-stage latency"
    >
      {STAGES.map((s, i) => {
        const v = sample.stage_ms[s] ?? 0;
        if (v <= 0) return null;
        return (
          <div
            key={s}
            style={{ width: `${(100 * v) / total}%`, background: `hsl(${(i * 37) % 360} 70% 55%)` }}
            title={`${s}: ${v.toFixed(1)} ms`}
          />
        );
      })}
    </div>
  );
}

export function TelemetryStrip() {
  const telemetry = useWorld((s) => s.telemetry);
  const mode = useWorld((s) => s.mode);
  const conn = useWorld((s) => s.connections.telemetry);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const plotRef = useRef<uPlot | null>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const opts: uPlot.Options = {
      width: host.clientWidth || 600,
      height: 84,
      cursor: { show: false },
      legend: { show: false },
      scales: {
        x: { time: false },
        y: { range: (_u, _min, max) => [0, Math.max(TARGET_MS * 1.3, max || 0)] },
      },
      axes: [
        { show: false },
        {
          stroke: "#7b879a",
          grid: { stroke: "#1b2230", width: 1 },
          ticks: { show: false },
          font: "10px ui-monospace, Menlo, monospace",
          size: 36,
          values: (_u, vals) => vals.map((v) => `${v}ms`),
        },
      ],
      series: [
        {},
        { stroke: COLORS.fp32_full, width: 1.5, spanGaps: true },
        { stroke: COLORS.int8_split, width: 1.5, spanGaps: true },
        { stroke: COLORS.synthetic, width: 1, spanGaps: true, dash: [3, 3] },
      ],
      hooks: {
        draw: [
          (u) => {
            const y = u.valToPos(TARGET_MS, "y", true);
            const ctx = u.ctx;
            ctx.save();
            ctx.strokeStyle = "rgba(255,59,48,0.7)";
            ctx.setLineDash([4, 4]);
            ctx.beginPath();
            ctx.moveTo(u.bbox.left, y);
            ctx.lineTo(u.bbox.left + u.bbox.width, y);
            ctx.stroke();
            ctx.restore();
          },
        ],
      },
    };
    const plot = new uPlot(opts, seriesData(useWorld.getState().telemetry.byPipeline), host);
    plotRef.current = plot;
    const ro = new ResizeObserver(() => {
      plot.setSize({ width: host.clientWidth, height: 84 });
    });
    ro.observe(host);
    let raf = 0;
    let lastRef: unknown = null;
    const frame = () => {
      raf = requestAnimationFrame(frame);
      const by = useWorld.getState().telemetry.byPipeline;
      if (by === lastRef) return;
      lastRef = by;
      plot.setData(seriesData(by));
    };
    raf = requestAnimationFrame(frame);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      plot.destroy();
      plotRef.current = null;
    };
  }, []);

  const last = telemetry.last;
  const latest = useMemo(
    () =>
      (["fp32_full", "int8_split", "synthetic"] as Pipeline[])
        .map((p) => telemetry.byPipeline[p].at(-1))
        .filter((s): s is TelemetrySample => s !== undefined),
    [telemetry],
  );
  const recorded = mode === "replay" || last?.mode === "recorded";
  const bench = telemetry.benchmark;

  return (
    <section className="panel flex h-full flex-col" data-testid="telemetry-strip">
      <div className="panel-title">
        <span>telemetry · FP32 full vs INT8 split · target {TARGET_MS} ms / 30 fps</span>
        <span className="flex items-center gap-1">
          <Badge tone={recorded ? "warn" : "ok"} data-testid="telemetry-mode">
            {last || mode === "replay" ? (recorded ? "RECORDED" : "LIVE") : "NO DATA"}
          </Badge>
          <Badge tone={conn === "open" ? "ok" : "neutral"}>benchmark ws {conn}</Badge>
          {bench?.hardware && (
            <span
              className="text-[10px] normal-case tracking-normal text-ops-dim"
              title={bench.hardware.platform}
            >
              {bench.hardware.cpu}
              {bench.hardware.gpu ? ` · ${bench.hardware.gpu}` : " · no GPU"}
            </span>
          )}
        </span>
      </div>
      <div className="flex flex-1 gap-2 p-2">
        <div ref={hostRef} className="min-w-0 flex-1" data-testid="telemetry-plot" />
        <div className="grid w-[26rem] grid-cols-1 gap-1 font-mono text-[10px]">
          {latest.length === 0 && (
            <span className="text-ops-dim">waiting for telemetry samples…</span>
          )}
          {latest.map((s) => (
            <div key={s.pipeline} className="flex flex-col gap-0.5">
              <div className="flex items-center justify-between">
                <span style={{ color: COLORS[s.pipeline] }}>{s.pipeline}</span>
                <span className="text-ops-dim">
                  {s.stream} ·{" "}
                  <span className={s.total_ms > TARGET_MS ? "text-ops-danger" : "text-ops-ok"}>
                    {fmtMs(s.total_ms, 1)}
                  </span>{" "}
                  · {s.fps.toFixed(1)} fps · {fmtBytes(s.bytes_sent)}/frame · drops{" "}
                  {s.dropped_frames}
                </span>
              </div>
              <StageBars sample={s} />
            </div>
          ))}
          {bench && (
            <div className="mt-auto text-ops-dim" data-testid="benchmark-summary">
              run {bench.run_id.slice(0, 16)} · {bench.pipeline} mean {fmtMs(bench.mean_ms, 1)} ·{" "}
              {bench.meets_target ? (
                <span className="text-ops-ok">meets target</span>
              ) : (
                <span className="text-ops-danger">misses target</span>
              )}
              {bench.bottleneck_stage ? ` · bottleneck ${bench.bottleneck_stage}` : ""}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
