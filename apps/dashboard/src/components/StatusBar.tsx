"use client";

import { useEffect, useState } from "react";

import { SIMULATION_LABEL } from "@arco/schemas";

import { Badge } from "@/components/ui/badge";
import { fmtMs } from "@/lib/utils";
import type { ConnState } from "@/lib/ws";
import {
  SNAP_ANIM_MS,
  selectActiveHazards,
  selectPenalizedCount,
  selectSeveredCount,
  useWorld,
} from "@/store/world";

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
  const penalized = useWorld(selectPenalizedCount);
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
    <header className="flex h-9 items-center gap-2 border-b border-ops-edge bg-ops-panel px-3 font-mono text-[11px]">
      <span className="text-sm font-bold tracking-[0.3em] text-ops-accent">ARCO</span>
      <span className="hidden text-ops-dim xl:inline">Autonomous Relief Corridor Orchestrator</span>
      <Badge tone="warn" className="hidden lg:inline-flex">
        {SIMULATION_LABEL}
      </Badge>
      <Badge
        tone={mode === "live" ? "ok" : mode === "replay" ? "warn" : "neutral"}
        data-testid="mode-badge"
      >
        {mode === "live" ? "LIVE STACK" : mode === "replay" ? "RECORDED REPLAY" : "CONNECTING"}
      </Badge>
      {mode === "replay" && replayInfo && (
        <span className="text-ops-warn" data-testid="replay-banner">
          backend unreachable · replaying {replayInfo.scenarioId} recorded{" "}
          {new Date(replayInfo.recordedAt).toLocaleString()}
        </span>
      )}
      <span className="mx-1 h-4 w-px bg-ops-edge" />
      {(["blackboard", "telemetry", "detections"] as const).map((feed) => (
        <Badge
          key={feed}
          tone={connTone(connections[feed])}
          data-testid={`conn-${feed}`}
          title={`${feed} websocket`}
        >
          <span className={connections[feed] === "open" ? "" : "animate-pulse"}>●</span> {feed}
          {connections[feed] === "reconnecting" && attempts[feed] > 0 ? ` #${attempts[feed]}` : ""}
        </Badge>
      ))}
      <span className="mx-1 h-4 w-px bg-ops-edge" />
      <span className="text-ops-dim">
        graph v<span className="text-ops-text">{graphVersion}</span> · severed{" "}
        <span className={severed > 0 ? "text-ops-danger" : "text-ops-text"}>{severed}</span> ·
        penalized{" "}
        <span className={penalized > 0 ? "text-ops-warn" : "text-ops-text"}>{penalized}</span> ·
        hazards <span className="text-ops-text">{hazards}</span> · routes{" "}
        <span className="text-ops-text">{routes}</span>
      </span>
      <span className="mx-1 h-4 w-px bg-ops-edge" />
      <span
        data-testid="snap-indicator"
        data-state={snapState}
        data-count={snap?.count ?? 0}
        className={
          snapState === "firing"
            ? "text-white"
            : snapState === "fired"
              ? "text-ops-accent"
              : "text-ops-dim"
        }
        title="graph.updated → routes.updated latency measured in the browser; animation 450 ms"
      >
        SNAP {snap ? `#${snap.count}` : "–"}{" "}
        {snap?.latencyMs !== null && snap?.latencyMs !== undefined ? fmtMs(snap.latencyMs) : ""}
        {routesMeta ? ` · compute ${fmtMs(routesMeta.computeMs, 1)}` : ""}
      </span>
      <span className="ml-auto flex items-center gap-2">
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
          three.js{" "}
          <span className="text-ops-text">
            {threeFps !== null ? `${threeFps.toFixed(0)} fps` : "–"}
          </span>
          {threeStats ? ` · ${threeStats.volumes}v/${threeStats.particles}p` : ""}
        </span>
        {lastError && (
          <span className="max-w-xs truncate text-ops-danger" title={lastError}>
            {lastError}
          </span>
        )}
      </span>
    </header>
  );
}
