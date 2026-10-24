"use client";

/**
 * Wires the store to the live stack: REST snapshot + three reconnecting WebSockets
 * (blackboard bus, benchmark telemetry, base-station detections). If the blackboard is
 * not reachable within `offlineFallbackAfterMs`, the recorded replay takes over and is
 * labelled as such; when the blackboard comes back the replay stops and the live
 * snapshot is reloaded.
 */
import { useEffect, useRef } from "react";

import type { AnyMessage } from "@arco/schemas";

import { api } from "@/lib/api";
import { config } from "@/lib/config";
import { ReplayPlayer, fetchReplay } from "@/lib/replay";
import { ReconnectingSocket } from "@/lib/ws";
import type { ConnState } from "@/lib/ws";
import { useWorld } from "@/store/world";

export function parseMessage(data: string): AnyMessage | null {
  try {
    const obj: unknown = JSON.parse(data);
    if (typeof obj === "object" && obj !== null && "type" in obj && "seq" in obj) {
      return obj as AnyMessage;
    }
    return null;
  } catch {
    return null;
  }
}

async function loadLiveSnapshot(): Promise<void> {
  const w = useWorld.getState();
  const scenario = await api.sim.scenario().catch(() => null);
  const bbox = scenario?.bbox ?? null;
  const [edges, hazards, routes, safeZones, reports, agentEvents] = await Promise.all([
    bbox ? api.blackboard.edges(bbox).catch(() => []) : Promise.resolve([]),
    api.blackboard.hazards().catch(() => []),
    api.blackboard.routes().catch(() => []),
    api.blackboard.safeZones().catch(() => []),
    api.blackboard.reports().catch(() => []),
    api.blackboard.agentEvents(200).catch(() => []),
  ]);
  w.loadSnapshot({ scenario, edges, hazards, routes, safeZones, reports, agentEvents });
}

export function useLiveFeeds(enabled = true): void {
  const started = useRef(false);

  useEffect(() => {
    if (!enabled || started.current) return;
    started.current = true;
    let replay: ReplayPlayer | null = null;
    let telemetryLoop: ReturnType<typeof setInterval> | null = null;
    const life = { disposed: false };
    const isDisposed = () => life.disposed; // re-read after awaits (TS keeps the narrowing)
    let everLive = false;

    const stopReplay = () => {
      replay?.stop();
      replay = null;
      if (telemetryLoop !== null) clearInterval(telemetryLoop);
      telemetryLoop = null;
    };

    const startReplay = async () => {
      if (life.disposed || replay !== null) return;
      try {
        const file = await fetchReplay(`/recorded/${config.defaultScenario}.replay.json`);
        if (isDisposed() || useWorld.getState().mode === "live") return;
        const st = useWorld.getState();
        st.reset();
        st.loadSnapshot({ scenario: file.scenario, edges: file.edges, safeZones: file.safe_zones });
        st.setMode("replay");
        st.setReplayInfo({
          recordedAt: file.recorded_at,
          scenarioId: file.scenario.id,
          messages: file.messages.length,
        });
        replay = new ReplayPlayer({
          messages: file.messages,
          emit: (m) => {
            useWorld.getState().applyMessage(m);
          },
          speed: 20,
        });
        replay.start();
        if (file.telemetry.length > 0) {
          let i = 0;
          telemetryLoop = setInterval(() => {
            const m = file.telemetry[i % file.telemetry.length];
            i += 1;
            if (m) useWorld.getState().applyMessage(m);
          }, 33);
        }
      } catch (err) {
        useWorld.getState().setError(`offline and no recorded replay: ${String(err)}`);
      }
    };

    const onBlackboardState = (state: ConnState, attempt: number) => {
      useWorld.getState().setConnection("blackboard", state, attempt);
      if (state === "open") {
        const wasReplay = useWorld.getState().mode === "replay";
        stopReplay();
        if (wasReplay) useWorld.getState().reset();
        useWorld.getState().setMode("live");
        everLive = true;
        void loadLiveSnapshot();
      }
    };

    const blackboard = new ReconnectingSocket({
      url: `${config.blackboardWs}/ws/blackboard`,
      onMessage: (d) => {
        const m = parseMessage(d);
        if (m) useWorld.getState().applyMessage(m);
      },
      onState: onBlackboardState,
    });
    const telemetry = new ReconnectingSocket({
      url: `${config.benchmarkWs}/ws/telemetry`,
      onMessage: (d) => {
        if (useWorld.getState().mode === "replay") return;
        const m = parseMessage(d);
        if (m) useWorld.getState().applyMessage(m);
      },
      onState: (s, a) => {
        useWorld.getState().setConnection("telemetry", s, a);
      },
    });
    const detections = new ReconnectingSocket({
      url: `${config.baseStationWs}/ws/detections`,
      onMessage: (d) => {
        const m = parseMessage(d);
        if (m) useWorld.getState().applyMessage(m);
      },
      onState: (s, a) => {
        useWorld.getState().setConnection("detections", s, a);
      },
    });

    blackboard.start();
    telemetry.start();
    detections.start();

    const fallbackTimer = setTimeout(() => {
      if (!everLive && useWorld.getState().connections.blackboard !== "open") void startReplay();
    }, config.offlineFallbackAfterMs);

    return () => {
      life.disposed = true;
      clearTimeout(fallbackTimer);
      stopReplay();
      blackboard.stop();
      telemetry.stop();
      detections.stop();
      started.current = false;
    };
  }, [enabled]);
}
