/** Typed message fixtures. Every object is checked against `@arco/schemas` at compile time. */
import type {
  AgentEventMessage,
  ClockEvent,
  EdgeEvent,
  GraphUpdatedEvent,
  Hazard,
  HazardEvent,
  NetworkEvent,
  RoadEdge,
  Route,
  RoutesUpdatedMessage,
  TelemetryMessage,
  TelemetrySample,
} from "@arco/schemas";

let seq = 0;
export const nextSeq = (): number => (seq += 1);
const WALL = "2026-10-24T20:00:00Z";

export function edge(
  id: number,
  status: RoadEdge["status"] = "open",
  over: Partial<RoadEdge> = {},
): RoadEdge {
  return {
    id,
    u: id * 10,
    v: id * 10 + 1,
    geom: {
      type: "LineString",
      coordinates: [
        [-156.67 + id * 0.001, 20.87],
        [-156.67 + id * 0.001, 20.871],
      ],
    },
    length_m: 110,
    base_cost: 10,
    current_cost: status === "severed" ? 1e9 : status === "penalized" ? 25 : 10,
    capacity_estimate: 1200,
    status,
    ...over,
  };
}

export function hazard(id: string, over: Partial<Hazard> = {}): Hazard {
  return {
    id,
    geom: {
      type: "Polygon",
      coordinates: [
        [
          [-156.68, 20.87],
          [-156.67, 20.87],
          [-156.67, 20.88],
          [-156.68, 20.88],
          [-156.68, 20.87],
        ],
      ],
    },
    hazard_class: "fire_front",
    confidence: 0.9,
    source: "simulated",
    first_seen: WALL,
    last_seen: WALL,
    ttl_s: 600,
    status: "active",
    severity: 1,
    ...over,
  };
}

export function route(groupId: string, edgeIds: number[], over: Partial<Route> = {}): Route {
  return {
    id: `route-${groupId}-${over.version ?? 1}`,
    crowd_group_id: groupId,
    origin_cell_id: `cell-${groupId}`,
    safe_zone_id: "north-exit",
    edge_ids: edgeIds,
    path: {
      type: "LineString",
      coordinates: [
        [-156.67, 20.87],
        [-156.675, 20.88],
      ],
    },
    length_m: 1500,
    cost: 150,
    group_size: 1200,
    graph_version: 1,
    version: 1,
    computed_at: WALL,
    instruction: "Head north on Front St to the Honoapiilani Hwy exit.",
    ...over,
  };
}

export function hazardEvent(type: HazardEvent["type"], h: Hazard, t = 0): HazardEvent {
  return { type, hazard: h, scenario_t: t, seq: nextSeq(), wall_t: WALL };
}

export function edgeEvent(edges: RoadEdge[], graphVersion: number, t = 0): EdgeEvent {
  return {
    type: "edge.updated",
    edges,
    graph_version: graphVersion,
    scenario_t: t,
    seq: nextSeq(),
    wall_t: WALL,
  };
}

export function graphUpdated(
  graphVersion: number,
  severed: number,
  changed: number[],
  t = 0,
): GraphUpdatedEvent {
  return {
    type: "graph.updated",
    graph_version: graphVersion,
    changed_edge_ids: changed,
    severed_count: severed,
    penalized_count: 0,
    scenario_t: t,
    seq: nextSeq(),
    wall_t: WALL,
  };
}

export function routesUpdated(routes: Route[], graphVersion: number, t = 0): RoutesUpdatedMessage {
  return {
    type: "routes.updated",
    routes,
    graph_version: graphVersion,
    compute_ms: 4.2,
    max_edge_load_ratio: 0.1,
    trigger: "graph.updated",
    scenario_t: t,
    seq: nextSeq(),
    wall_t: WALL,
  };
}

export function clockTick(t: number, playing: boolean, speed = 1): ClockEvent {
  return {
    type: "clock.tick",
    scenario_id: "lahaina_2023",
    scenario_t: t,
    playing,
    speed,
    duration_s: 1800,
    seq: nextSeq(),
    wall_t: WALL,
  };
}

export function network(up: boolean, t = 0): NetworkEvent {
  return {
    type: "network.cellular",
    cellular_up: up,
    reason: up ? null : "chaos",
    scenario_t: t,
    seq: nextSeq(),
    wall_t: WALL,
  };
}

export function agentEvent(
  agent: AgentEventMessage["event"]["agent"],
  kind: string,
  t = 0,
): AgentEventMessage {
  return {
    type: "agent.event",
    event: {
      id: `ev-${nextSeq()}`,
      agent,
      kind,
      reasoning: `${agent} did ${kind}`,
      scenario_t: t,
      wall_t: WALL,
    },
    scenario_t: t,
    seq: nextSeq(),
    wall_t: WALL,
  };
}

export function telemetry(
  pipeline: TelemetrySample["pipeline"],
  totalMs: number,
  mode: TelemetrySample["mode"] = "live",
): TelemetryMessage {
  return {
    type: "telemetry.sample",
    sample: {
      pipeline,
      stream: "micro",
      mode,
      frame_id: nextSeq(),
      total_ms: totalMs,
      stage_ms: { capture: 2, head: 10, tail: totalMs - 12 },
      bytes_sent: 120_000,
      fps: 1000 / totalMs,
      dropped_frames: 0,
      wall_t: WALL,
    },
    scenario_t: 0,
    seq: nextSeq(),
    wall_t: WALL,
  };
}
