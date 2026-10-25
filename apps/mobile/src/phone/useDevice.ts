/**
 * The phone's runtime: one MeshNode over the dev relay (+ optional radio transports when
 * an adapter exists), a simulated cellular switch that gates the uplink and route polling,
 * a simulated walker along the active route, and arrow guidance derived from both.
 */
import { useEffect, useMemo, useReducer, useRef, useState } from "react";

import type { CommunityReport, HazardClass, Route } from "@arco/schemas";

import type { LngLat } from "@/core/geo";
import { guidance } from "@/core/guidance";
import type { Guidance } from "@/core/guidance";
import { MeshNode } from "@/core/node";
import type { ActiveRoute } from "@/core/node";
import { BleTransport, WifiDirectTransport } from "@/core/radioTransports";
import { WebSocketRelayTransport } from "@/core/relayTransport";
import type { RelayState } from "@/core/relayTransport";
import { isGraphTile } from "@/core/router";
import type { GraphTile } from "@/core/router";
import { DeviceKey, uuid4 } from "@/core/signing";
import type { Connectivity } from "@/core/transport";

import { appConfig } from "./config";
import { step } from "./walker";
import type { WalkerState } from "./walker";

// Static tile shipped with the app (exported from the OSM sample by scripts/export_graph_tile.py).
// eslint-disable-next-line @typescript-eslint/no-require-imports
const rawTile: unknown = require("../../assets/tiles/lahaina_2023.json");
const TILE: GraphTile | null = isGraphTile(rawTile) ? rawTile : null;

/** Simulated start: the scenario's first population cell (coordinates come from the data file). */
const START: LngLat | null = TILE?.population_cells[0]?.center ?? null;

export interface DeviceView {
  id: string;
  reporterHash: string;
  connectivity: Connectivity;
  relayState: RelayState;
  relayAttempt: number;
  peers: string[];
  cellular: boolean;
  walking: boolean;
  position: LngLat | null;
  headingDeg: number;
  route: ActiveRoute | null;
  guidance: Guidance | null;
  pins: CommunityReport[];
  pendingUpload: number;
  uploaded: number;
  lastError: string | null;
  radios: { kind: string; available: boolean; reason: string | null }[];
  tileLabel: string | null;
}

export interface DeviceActions {
  setCellular(on: boolean): void;
  setWalking(on: boolean): void;
  dropPin(cls: HazardClass, note?: string | null): CommunityReport | null;
  resetPosition(): void;
}

export function useDevice(): [DeviceView, DeviceActions] {
  const [, bump] = useReducer((n: number) => n + 1, 0);
  const [cellular, setCellular] = useState(false);
  const [walking, setWalking] = useState(false);
  const [relayState, setRelayState] = useState<RelayState>("closed");
  const [lastError, setLastError] = useState<string | null>(null);
  const walkerRef = useRef<WalkerState | null>(
    START ? { position: START, headingDeg: 0, alongM: 0 } : null,
  );
  const uploadedRef = useRef(0);

  const rt = useMemo(() => {
    const id = `phone-${uuid4().slice(0, 8)}`;
    const relay = new WebSocketRelayTransport({
      url: appConfig.meshRelayUrl,
      room: appConfig.room,
      id,
    });
    const ble = new BleTransport(id); // no adapter in Expo Go: reports why
    const wifi = new WifiDirectTransport(id);
    const node = new MeshNode({
      id,
      key: DeviceKey.generate(),
      transports: [relay, ble, wifi],
      uplink: { blackboardUrl: appConfig.blackboardUrl },
      tile: TILE,
    });
    node.position = START;
    return { id, relay, ble, wifi, node };
  }, []);

  // Start/stop the stack with the component.
  useEffect(() => {
    const { relay, node } = rt;
    const offState = relay.onState((s) => {
      setRelayState(s);
      bump();
    });
    const offPeers = relay.onPeers(() => {
      bump();
    });
    const offPins = node.doc.onPins(() => {
      bump();
    });
    const offRoutes = node.doc.onRoutes(() => {
      bump();
    });
    relay.start();
    node.start();
    return () => {
      offState();
      offPeers();
      offPins();
      offRoutes();
      node.stop();
      relay.stop();
    };
  }, [rt]);

  // Cellular gates the uplink and the route poll (the agents' routes then spread over the mesh).
  useEffect(() => {
    rt.node.cellularUp = cellular;
    bump();
    if (!cellular) return;
    let alive = true;
    const tick = async () => {
      try {
        const n = await rt.node.flushUplink();
        uploadedRef.current += n;
        const res = await fetch(`${appConfig.blackboardUrl}/routes`);
        if (res.ok) {
          const routes = (await res.json()) as Route[];
          rt.node.ingestRoutes(routes);
          if (!rt.node.crowdGroupId && routes.length > 0) {
            // Join the crowd group whose route starts nearest to us.
            const pos = rt.node.position;
            if (pos) {
              let best: { d: number; id: string } | null = null;
              for (const r of routes) {
                const c0 = r.path.coordinates[0];
                const d = Math.hypot(c0[0] - pos[0], c0[1] - pos[1]);
                if (!best || d < best.d) best = { d, id: r.crowd_group_id };
              }
              if (best) rt.node.crowdGroupId = best.id;
            }
          }
        }
        if (alive) setLastError(null);
      } catch (err) {
        if (alive) setLastError(err instanceof Error ? err.message : String(err));
      }
      if (alive) bump();
    };
    void tick();
    const h = setInterval(() => {
      void tick();
    }, appConfig.uplinkEveryMs);
    return () => {
      alive = false;
      clearInterval(h);
    };
  }, [rt, cellular]);

  // Simulated walking along the active route.
  useEffect(() => {
    if (!walking) return;
    const h = setInterval(() => {
      const w = walkerRef.current;
      const route = rt.node.activeRoute();
      if (!w || !route) return;
      const next = step(w, route.path, 1, appConfig.walkSpeedMps);
      walkerRef.current = next;
      rt.node.position = next.position;
      bump();
    }, 1000);
    return () => {
      clearInterval(h);
    };
  }, [rt, walking]);

  const route = rt.node.activeRoute();
  const w = walkerRef.current;
  const g = route && w ? guidance(w.position, w.headingDeg, route.path) : null;

  const view: DeviceView = {
    id: rt.id,
    reporterHash: rt.node.key.reporterHash,
    connectivity: rt.node.connectivity(),
    relayState,
    relayAttempt: rt.relay.attempt,
    peers: rt.node.sync.peers(),
    cellular,
    walking,
    position: w?.position ?? null,
    headingDeg: w?.headingDeg ?? 0,
    route,
    guidance: g,
    pins: rt.node.doc.pins(),
    pendingUpload: rt.node.doc.pendingUpload().length,
    uploaded: uploadedRef.current,
    lastError,
    radios: [rt.ble, rt.wifi].map((t) => ({ kind: t.info.kind, ...t.availability() })),
    tileLabel: TILE ? `${TILE.label} · ${TILE.source.name} (${TILE.source.license})` : null,
  };

  const actions: DeviceActions = {
    setCellular,
    setWalking,
    dropPin: (cls, note = null) => {
      try {
        const r = rt.node.dropPin(cls, note);
        setLastError(null);
        bump();
        return r;
      } catch (err) {
        setLastError(err instanceof Error ? err.message : String(err));
        return null;
      }
    },
    resetPosition: () => {
      if (START) {
        walkerRef.current = { position: START, headingDeg: 0, alongM: 0 };
        rt.node.position = START;
        bump();
      }
    },
  };
  return [view, actions];
}
