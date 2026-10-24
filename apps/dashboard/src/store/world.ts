/**
 * Zustand store for everything the dashboard renders. `applyMessage` is a pure reducer
 * over `AnyMessage` from `@arco/schemas`: the same function consumes the live WebSocket
 * stream and the recorded replay, so the two modes cannot drift apart.
 *
 * The scenario clock is the backend's (`clock.tick`); the dashboard never runs its own
 * clock while connected. In replay mode the replay player feeds recorded `clock.tick`s.
 */
import { create } from "zustand";

import type {
  AgentEvent,
  AnyMessage,
  BenchmarkResult,
  ClockEvent,
  CommunityReport,
  DetectionsMessage,
  Hazard,
  RoadEdge,
  Route,
  SafeZone,
  Scenario,
  TelemetrySample,
} from "@arco/schemas";

import type { ConnState } from "@/lib/ws";

export type Mode = "connecting" | "live" | "replay";
export type Feed = "blackboard" | "telemetry" | "detections";
export type Pipeline = TelemetrySample["pipeline"];

export const MAX_AGENT_EVENTS = 400;
export const MAX_TELEMETRY = 600;
export const EDGE_FLASH_MS = 1800;
export const SNAP_ANIM_MS = 450;

export interface ClockState {
  scenarioId: string | null;
  t: number;
  playing: boolean;
  speed: number;
  durationS: number | null;
  /** performance.now() when the last tick arrived (for display interpolation only). */
  receivedAt: number;
}

export interface SnapState {
  /** performance.now() when the new routes arrived (animation start). */
  at: number;
  graphVersion: number;
  /** graph.updated -> routes.updated wall latency, when both were observed. */
  latencyMs: number | null;
  trigger: string;
  count: number;
}

export interface TelemetryState {
  byPipeline: Record<Pipeline, TelemetrySample[]>;
  last: TelemetrySample | null;
  benchmark: BenchmarkResult | null;
}

export interface WorldState {
  mode: Mode;
  connections: Record<Feed, ConnState>;
  reconnectAttempts: Record<Feed, number>;
  scenario: Scenario | null;
  hazards: Record<string, Hazard>;
  edges: Record<number, RoadEdge>;
  /** edge id -> performance.now() until which it flashes (severed) */
  edgeFlash: Record<number, number>;
  graphVersion: number;
  lastGraphUpdate: { version: number; at: number; severed: number; penalized: number } | null;
  routes: Route[];
  routesMeta: { graphVersion: number; computeMs: number; trigger: string; at: number } | null;
  snap: SnapState | null;
  safeZones: Record<string, SafeZone>;
  reports: CommunityReport[];
  agentEvents: AgentEvent[];
  clock: ClockState;
  cellularUp: boolean;
  cellularReason: string | null;
  detections: Partial<Record<DetectionsMessage["stream"], DetectionsMessage>>;
  telemetry: TelemetryState;
  threeFps: number | null;
  threeStats: { volumes: number; particles: number; beacons: number } | null;
  replayInfo: { recordedAt: string; scenarioId: string; messages: number } | null;
  lastError: string | null;
}

export interface WorldActions {
  applyMessage: (msg: AnyMessage, now?: number) => void;
  loadSnapshot: (snap: Snapshot) => void;
  setConnection: (feed: Feed, state: ConnState, attempt: number) => void;
  setMode: (mode: Mode) => void;
  setThreeFps: (
    fps: number,
    stats?: { volumes: number; particles: number; beacons: number },
  ) => void;
  setReplayInfo: (info: WorldState["replayInfo"]) => void;
  setError: (msg: string | null) => void;
  reset: () => void;
}

export interface Snapshot {
  scenario?: Scenario | null;
  edges?: RoadEdge[];
  hazards?: Hazard[];
  routes?: Route[];
  safeZones?: SafeZone[];
  reports?: CommunityReport[];
  agentEvents?: AgentEvent[];
}

const emptyTelemetry = (): TelemetryState => ({
  byPipeline: { fp32_full: [], int8_split: [], synthetic: [] },
  last: null,
  benchmark: null,
});

export const initialWorld = (): WorldState => ({
  mode: "connecting",
  connections: { blackboard: "closed", telemetry: "closed", detections: "closed" },
  reconnectAttempts: { blackboard: 0, telemetry: 0, detections: 0 },
  scenario: null,
  hazards: {},
  edges: {},
  edgeFlash: {},
  graphVersion: 0,
  lastGraphUpdate: null,
  routes: [],
  routesMeta: null,
  snap: null,
  safeZones: {},
  reports: [],
  agentEvents: [],
  clock: { scenarioId: null, t: 0, playing: false, speed: 1, durationS: null, receivedAt: 0 },
  cellularUp: true,
  cellularReason: null,
  detections: {},
  telemetry: emptyTelemetry(),
  threeFps: null,
  threeStats: null,
  replayInfo: null,
  lastError: null,
});

const nowMs = (): number => (typeof performance !== "undefined" ? performance.now() : Date.now());

function pushCapped<T>(arr: T[], item: T, cap: number): T[] {
  const out = arr.length >= cap ? arr.slice(arr.length - cap + 1) : arr.slice();
  out.push(item);
  return out;
}

/** Pure reducer: returns the patch to apply for one bus message. */
export function applyMessage(
  state: WorldState,
  msg: AnyMessage,
  now = nowMs(),
): Partial<WorldState> {
  switch (msg.type) {
    case "hazard.created":
    case "hazard.updated":
    case "hazard.resolved":
    case "hazard.disputed": {
      const h = msg.hazard;
      return { hazards: { ...state.hazards, [h.id]: h } };
    }
    case "edge.updated": {
      const edges = { ...state.edges };
      const edgeFlash = { ...state.edgeFlash };
      for (const e of msg.edges) {
        const prev = edges[e.id];
        edges[e.id] = e;
        if (e.status === "severed" && prev?.status !== "severed")
          edgeFlash[e.id] = now + EDGE_FLASH_MS;
      }
      return { edges, edgeFlash, graphVersion: Math.max(state.graphVersion, msg.graph_version) };
    }
    case "graph.updated":
      return {
        graphVersion: Math.max(state.graphVersion, msg.graph_version),
        lastGraphUpdate: {
          version: msg.graph_version,
          at: now,
          severed: msg.severed_count,
          penalized: msg.penalized_count,
        },
      };
    case "routes.updated": {
      const latency =
        state.lastGraphUpdate?.version === msg.graph_version
          ? Math.max(0, now - state.lastGraphUpdate.at)
          : null;
      const changed =
        state.routesMeta?.graphVersion !== msg.graph_version ||
        routeSetChanged(state.routes, msg.routes);
      return {
        routes: msg.routes,
        routesMeta: {
          graphVersion: msg.graph_version,
          computeMs: msg.compute_ms,
          trigger: msg.trigger,
          at: now,
        },
        snap: changed
          ? {
              at: now,
              graphVersion: msg.graph_version,
              latencyMs: latency,
              trigger: msg.trigger,
              count: (state.snap?.count ?? 0) + 1,
            }
          : state.snap,
      };
    }
    case "safe_zone.updated":
      return { safeZones: { ...state.safeZones, [msg.safe_zone.id]: msg.safe_zone } };
    case "report.created":
      return { reports: pushCapped(state.reports, msg.report, 500) };
    case "agent.event":
      return { agentEvents: pushCapped(state.agentEvents, msg.event, MAX_AGENT_EVENTS) };
    case "clock.tick":
      return { clock: clockFrom(msg, now) };
    case "network.cellular":
      return { cellularUp: msg.cellular_up, cellularReason: msg.reason ?? null };
    case "detections":
      return { detections: { ...state.detections, [msg.stream]: msg } };
    case "telemetry.sample": {
      const s = msg.sample;
      const list = pushCapped(state.telemetry.byPipeline[s.pipeline], s, MAX_TELEMETRY);
      return {
        telemetry: {
          ...state.telemetry,
          byPipeline: { ...state.telemetry.byPipeline, [s.pipeline]: list },
          last: s,
        },
      };
    }
    case "telemetry.benchmark":
      return { telemetry: { ...state.telemetry, benchmark: msg.result } };
    default:
      return {};
  }
}

export function clockFrom(msg: ClockEvent, now: number): ClockState {
  return {
    scenarioId: msg.scenario_id,
    t: msg.scenario_t,
    playing: msg.playing,
    speed: msg.speed,
    durationS: msg.duration_s ?? null,
    receivedAt: now,
  };
}

function routeSetChanged(a: Route[], b: Route[]): boolean {
  if (a.length !== b.length) return true;
  const ids = new Map(a.map((r) => [r.crowd_group_id, r]));
  for (const r of b) {
    const prev = ids.get(r.crowd_group_id);
    if (prev?.version !== r.version || prev.safe_zone_id !== r.safe_zone_id) return true;
    if (prev.edge_ids.length !== r.edge_ids.length) return true;
    for (let i = 0; i < r.edge_ids.length; i += 1)
      if (prev.edge_ids[i] !== r.edge_ids[i]) return true;
  }
  return false;
}

/** Scenario time to display: the last tick, extrapolated only while the backend says it is playing. */
export function displayClockT(clock: ClockState, now = nowMs()): number {
  if (!clock.playing || clock.receivedAt === 0) return clock.t;
  const extra = ((now - clock.receivedAt) / 1000) * clock.speed;
  const t = clock.t + Math.max(0, Math.min(extra, 2 * clock.speed));
  return clock.durationS !== null ? Math.min(t, clock.durationS) : t;
}

export const useWorld = create<WorldState & WorldActions>((set, get) => ({
  ...initialWorld(),
  applyMessage: (msg, now) => {
    const patch = applyMessage(get(), msg, now);
    if (Object.keys(patch).length > 0) set(patch);
  },
  loadSnapshot: (snap) => {
    set(() => {
      const patch: Partial<WorldState> = {};
      if (snap.scenario !== undefined) patch.scenario = snap.scenario;
      if (snap.edges) patch.edges = Object.fromEntries(snap.edges.map((e) => [e.id, e]));
      if (snap.hazards) patch.hazards = Object.fromEntries(snap.hazards.map((h) => [h.id, h]));
      if (snap.routes) {
        patch.routes = snap.routes;
        const gv = snap.routes.reduce((m, r) => Math.max(m, r.graph_version), 0);
        patch.routesMeta = { graphVersion: gv, computeMs: 0, trigger: "snapshot", at: nowMs() };
      }
      if (snap.safeZones)
        patch.safeZones = Object.fromEntries(snap.safeZones.map((z) => [z.id, z]));
      if (snap.reports) patch.reports = snap.reports;
      if (snap.agentEvents) patch.agentEvents = snap.agentEvents.slice(-MAX_AGENT_EVENTS);
      return patch;
    });
  },
  setConnection: (feed, state, attempt) => {
    set((s) => ({
      connections: { ...s.connections, [feed]: state },
      reconnectAttempts: { ...s.reconnectAttempts, [feed]: attempt },
    }));
  },
  setMode: (mode) => {
    set({ mode });
  },
  setThreeFps: (fps, stats) => {
    set({ threeFps: fps, ...(stats ? { threeStats: stats } : {}) });
  },
  setReplayInfo: (info) => {
    set({ replayInfo: info });
  },
  setError: (msg) => {
    set({ lastError: msg });
  },
  reset: () => {
    set(initialWorld());
  },
}));

export const selectActiveHazards = (s: WorldState): Hazard[] =>
  Object.values(s.hazards).filter((h) => (h.status ?? "active") !== "resolved");
export const selectSeveredCount = (s: WorldState): number =>
  Object.values(s.edges).filter((e) => e.status === "severed").length;
export const selectPenalizedCount = (s: WorldState): number =>
  Object.values(s.edges).filter((e) => e.status === "penalized").length;
