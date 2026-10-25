/**
 * On-device FALLBACK router: A* over a precomputed graph tile (`arco.tile/1`, exported by
 * `scripts/export_graph_tile.py` from the same OSM sample the blackboard ingests).
 *
 * It is used only when no route from the Navigator is available (isolated device, or the
 * mesh has not relayed one yet). Hazard pins from the CRDT sever edges within
 * `severRadiusM` and penalise edges within `penaltyRadiusM`, mirroring the Cartographer's
 * policy in spirit — but this is a single-phone shortest path, not the crowd-dispersing
 * Navigator, and the UI says so ("on-device fallback").
 */
import type { CommunityReport } from "@arco/schemas";

import { haversineM, pointToPolylineM, polylineLengthM } from "./geo";
import type { LngLat } from "./geo";

export interface TileEdge {
  id: number;
  u: string;
  v: string;
  length_m: number;
  highway: string | null;
  name: string | null;
  geom: LngLat[];
}

export interface TileSafeZone {
  id: string;
  name: string;
  center: LngLat;
  capacity: number | null;
}

export interface TileCell {
  id: string;
  center: LngLat;
  population: number | null;
}

export interface GraphTile {
  format: "arco.tile/1";
  scenario_id: string;
  label: string;
  bbox: [number, number, number, number];
  source: { name: string; license: string; url?: string; note?: string };
  note: string;
  nodes: Record<string, LngLat>;
  edges: TileEdge[];
  safe_zones: TileSafeZone[];
  /** Scenario population cells: where evacuees start (the app's simulated position). */
  population_cells: TileCell[];
}

export function isGraphTile(x: unknown): x is GraphTile {
  return (
    typeof x === "object" &&
    x !== null &&
    (x as { format?: unknown }).format === "arco.tile/1" &&
    Array.isArray((x as { edges?: unknown }).edges)
  );
}

export interface HazardPolicy {
  severRadiusM: number;
  penaltyRadiusM: number;
  /** Multiplier applied to edges inside the penalty ring but outside the sever radius. */
  penaltyFactor: number;
}

export const DEFAULT_POLICY: HazardPolicy = {
  severRadiusM: 40,
  penaltyRadiusM: 120,
  penaltyFactor: 4,
};

export type EdgeState = "open" | "penalized" | "severed";

export interface EdgeEffect {
  edgeId: number;
  state: EdgeState;
  factor: number;
}

/** Walking speed by road class (m/s); fallback router assumes people on foot or slow cars. */
function speedFor(highway: string | null): number {
  switch (highway) {
    case "motorway":
    case "trunk":
    case "primary":
      return 8;
    case "secondary":
    case "tertiary":
      return 6;
    case "residential":
    case "unclassified":
      return 4;
    default:
      return 3;
  }
}

/** Per-edge effect of the current hazard pins; only edges actually affected are returned. */
export function hazardEffects(
  tile: GraphTile,
  pins: CommunityReport[],
  policy: HazardPolicy = DEFAULT_POLICY,
): Map<number, EdgeEffect> {
  const out = new Map<number, EdgeEffect>();
  const hazards = pins.filter((p) => p.report_type === "hazard_pin");
  if (hazards.length === 0) return out;
  for (const e of tile.edges) {
    let best: EdgeState = "open";
    for (const h of hazards) {
      const p: LngLat = [h.geom.coordinates[0], h.geom.coordinates[1]];
      const d = pointToPolylineM(p, e.geom);
      if (d <= policy.severRadiusM) {
        best = "severed";
        break;
      }
      if (d <= policy.penaltyRadiusM) best = "penalized";
    }
    if (best !== "open") {
      out.set(e.id, {
        edgeId: e.id,
        state: best,
        factor: best === "severed" ? Infinity : policy.penaltyFactor,
      });
    }
  }
  return out;
}

export function nearestNode(tile: GraphTile, p: LngLat): string | null {
  let bestId: string | null = null;
  let best = Infinity;
  for (const [id, xy] of Object.entries(tile.nodes)) {
    const d = haversineM(p, xy);
    if (d < best) {
      best = d;
      bestId = id;
    }
  }
  return bestId;
}

export interface RouteResult {
  /** Node ids from origin to destination. */
  nodes: string[];
  edgeIds: number[];
  path: LngLat[];
  lengthM: number;
  /** Seconds at the per-class speeds incl. penalties. */
  costS: number;
  safeZoneId: string;
}

class MinHeap<T> {
  private readonly items: { k: number; v: T }[] = [];
  get size(): number {
    return this.items.length;
  }
  push(k: number, v: T): void {
    const a = this.items;
    a.push({ k, v });
    let i = a.length - 1;
    while (i > 0) {
      const p = (i - 1) >> 1;
      const pi = a[p];
      const ii = a[i];
      if (!pi || !ii || pi.k <= ii.k) break;
      a[p] = ii;
      a[i] = pi;
      i = p;
    }
  }
  pop(): { k: number; v: T } | undefined {
    const a = this.items;
    const top = a[0];
    const last = a.pop();
    if (a.length > 0 && last) {
      a[0] = last;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1;
        const r = l + 1;
        let m = i;
        const am = a[m];
        const al = a[l];
        const ar = a[r];
        if (al && am && al.k < am.k) m = l;
        const am2 = a[m];
        if (ar && am2 && ar.k < am2.k) m = r;
        if (m === i) break;
        const tmp = a[i];
        const amm = a[m];
        if (!tmp || !amm) break;
        a[i] = amm;
        a[m] = tmp;
        i = m;
      }
    }
    return top;
  }
}

function buildAdjacency(tile: GraphTile): Map<string, TileEdge[]> {
  const adj = new Map<string, TileEdge[]>();
  for (const e of tile.edges) {
    let list = adj.get(e.u);
    if (!list) {
      list = [];
      adj.set(e.u, list);
    }
    list.push(e);
  }
  return adj;
}

const adjacencyCache = new WeakMap<GraphTile, Map<string, TileEdge[]>>();

/** A* from `fromNode` to `toNode` with per-edge hazard effects. */
export function astar(
  tile: GraphTile,
  fromNode: string,
  toNode: string,
  effects: Map<number, EdgeEffect>,
): { nodes: string[]; edges: TileEdge[]; costS: number } | null {
  let adj = adjacencyCache.get(tile);
  if (!adj) {
    adj = buildAdjacency(tile);
    adjacencyCache.set(tile, adj);
  }
  const goal = tile.nodes[toNode];
  if (!goal || !tile.nodes[fromNode]) return null;
  const h = (n: string): number => {
    const p = tile.nodes[n];
    return p ? haversineM(p, goal) / 8 : 0; // admissible: fastest class speed
  };
  const g = new Map<string, number>([[fromNode, 0]]);
  const cameFrom = new Map<string, { node: string; edge: TileEdge }>();
  const open = new MinHeap<string>();
  open.push(h(fromNode), fromNode);
  const closed = new Set<string>();
  while (open.size > 0) {
    const cur = open.pop();
    if (!cur) break;
    const n = cur.v;
    if (closed.has(n)) continue;
    if (n === toNode) break;
    closed.add(n);
    const gn = g.get(n) ?? Infinity;
    for (const e of adj.get(n) ?? []) {
      const eff = effects.get(e.id);
      if (eff?.state === "severed") continue;
      const w = (e.length_m / speedFor(e.highway)) * (eff?.factor ?? 1);
      const tentative = gn + w;
      if (tentative < (g.get(e.v) ?? Infinity)) {
        g.set(e.v, tentative);
        cameFrom.set(e.v, { node: n, edge: e });
        open.push(tentative + h(e.v), e.v);
      }
    }
  }
  if (!g.has(toNode)) return null;
  const nodes: string[] = [toNode];
  const edges: TileEdge[] = [];
  let n = toNode;
  while (n !== fromNode) {
    const step = cameFrom.get(n);
    if (!step) return null;
    edges.push(step.edge);
    n = step.node;
    nodes.push(n);
  }
  nodes.reverse();
  edges.reverse();
  return { nodes, edges, costS: g.get(toNode) ?? Infinity };
}

/** Route from `position` to the cheapest reachable safe zone, avoiding pinned hazards. */
export function routeToSafety(
  tile: GraphTile,
  position: LngLat,
  pins: CommunityReport[],
  policy: HazardPolicy = DEFAULT_POLICY,
): RouteResult | null {
  const effects = hazardEffects(tile, pins, policy);
  const from = nearestNode(tile, position);
  if (!from) return null;
  let best: RouteResult | null = null;
  for (const sz of tile.safe_zones) {
    const to = nearestNode(tile, sz.center);
    if (!to) continue;
    const r = astar(tile, from, to, effects);
    if (!r) continue;
    const path: LngLat[] = [];
    for (const e of r.edges) {
      for (const [i, c] of e.geom.entries()) {
        if (i === 0 && path.length > 0) continue;
        path.push(c);
      }
    }
    if (path.length === 0) {
      const p = tile.nodes[from];
      if (p) path.push(p);
    }
    const result: RouteResult = {
      nodes: r.nodes,
      edgeIds: r.edges.map((e) => e.id),
      path,
      lengthM: polylineLengthM(path),
      costS: r.costS,
      safeZoneId: sz.id,
    };
    if (!best || result.costS < best.costS) best = result;
  }
  return best;
}
