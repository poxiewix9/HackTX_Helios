"use client";

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { fmtMs } from "@/lib/utils";
import type { ConnState } from "@/lib/ws";
import { SNAP_ANIM_MS, selectActiveHazards, selectSeveredCount, useWorld } from "@/store/world";

function Stat({
  label,
  value,
  hot = false,
}: {
  label: string;
  value: number | string;
  hot?: boolean;
}) {
  return (
    <span className="flex items-baseline gap-1">
      <span className="font-sans text-[10px] uppercase tracking-wider">{label}</span>
      <span className={hot ? "text-ops-danger" : "text-ops-text"}>{value}</span>
    </span>
  );
}

function connTone(s: ConnState): "ok" | "warn" | "danger" | "neutral" {
  if (s === "open") return "ok";
  if (s === "connecting" || s === "reconnecting") return "warn";
  return "neutral";
}

export function StatusBar() {
  const mode = useWorld((s) => s.mode);
  const connections = useWorld((s) => s.connections);
  const attempts = useWorld((s) => s.reconnectAttempts);
  const snap = useWorld((s) => s.snap);
  const routesMeta = useWorld((s) => s.routesMeta);
  const routes = useWorld((s) => s.routes.length);
  const hazards = useWorld((s) => selectActiveHazards(s).length);
  const severed = useWorld(selectSeveredCount);
  const graphVersion = useWorld((s) => s.graphVersion);
  const threeFps = useWorld((s) => s.threeFps);
  const threeStats = useWorld((s) => s.threeStats);
  const cellularUp = useWorld((s) => s.cellularUp);
  const replayInfo = useWorld((s) => s.replayInfo);
  const lastError = useWorld((s) => s.lastError);
  const [snapState, setSnapState] = useState<"idle" | "firing" | "fired">("idle");

  useEffect(() => {
    if (!snap) return;
    setSnapState("firing");
    const t = setTimeout(() => {
      setSnapState("fired");
    }, SNAP_ANIM_MS);
    return () => {
      clearTimeout(t);
    };
  }, [snap]);

  return (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b border-white/[0.06] bg-[#0d131b] px-3 font-mono text-[11px]">
      <div className="flex items-center gap-2.5">
        <span className="text-[13px] font-semibold tracking-[0.28em] text-ops-accent">ARCO</span>
        <span className="hidden text-[11px] font-sans text-ops-dim 2xl:inline">
          Relief corridors
        </span>
      </div>
      <Badge
        tone={mode === "live" ? "ok" : mode === "replay" ? "warn" : "neutral"}
        data-testid="mode-badge"
      >
        {mode === "live" ? "LIVE STACK" : mode === "replay" ? "RECORDED REPLAY" : "CONNECTING"}
      </Badge>
      {mode === "replay" && replayInfo && (
        <span className="max-w-[18rem] truncate text-ops-warn" data-testid="replay-banner">
          backend unreachable · replaying {replayInfo.scenarioId} recorded{" "}
          {new Date(replayInfo.recordedAt).toLocaleString()}
        </span>
      )}
      <div className="hidden items-center gap-3 text-ops-dim xl:flex">
        <Stat label="hazards" value={hazards} />
        <Stat label="routes" value={routes} />
        <Stat label="severed" value={severed} hot={severed > 0} />
        <Stat label="graph" value={`v${graphVersion}`} />
      </div>
      <span className="ml-auto flex items-center gap-1.5">
        {(["blackboard", "telemetry", "detections"] as const).map((feed) => (
          <Badge
            key={feed}
            tone={connTone(connections[feed])}
            data-testid={`conn-${feed}`}
            title={`${feed} websocket`}
          >
            <span className={connections[feed] === "open" ? "" : "animate-pulse"}>●</span> {feed}
            {connections[feed] === "reconnecting" && attempts[feed] > 0
              ? ` #${attempts[feed]}`
              : ""}
          </Badge>
        ))}
        <span
          data-testid="snap-indicator"
          data-state={snapState}
          data-count={snap?.count ?? 0}
          className={
            snapState === "firing"
              ? "px-1 text-white"
              : snapState === "fired"
                ? "px-1 text-ops-accent"
                : "px-1 text-ops-dim"
          }
          title="graph.updated → routes.updated latency measured in the browser; animation 450 ms"
        >
          SNAP {snap ? `#${snap.count}` : "–"}{" "}
          {snap?.latencyMs !== null && snap?.latencyMs !== undefined ? fmtMs(snap.latencyMs) : ""}
          {routesMeta ? ` · compute ${fmtMs(routesMeta.computeMs, 1)}` : ""}
        </span>
        <Badge tone={cellularUp ? "ok" : "danger"} data-testid="cellular-badge">
          cellular {cellularUp ? "UP" : "DOWN"}
        </Badge>
        <span
          className="text-ops-dim"
          data-testid="three-fps"
          data-fps={threeFps?.toFixed(1) ?? ""}
          title={
            threeStats
              ? `${threeStats.volumes} hazard volumes · ${threeStats.particles} crowd particles · ${threeStats.beacons} beacons`
              : ""
          }
        >
          <span className="text-ops-text">
            {threeFps !== null ? `${threeFps.toFixed(0)} fps` : "–"}
          </span>
        </span>
        {lastError && (
          <span className="max-w-[10rem] truncate text-ops-danger" title={lastError}>
            {lastError}
          </span>
        )}
      </span>
    </header>
  );
}
