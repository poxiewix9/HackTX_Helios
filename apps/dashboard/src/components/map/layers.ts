/**
 * deck.gl layer construction for one frame. Pure function of (world state, now) so it is
 * unit-testable without WebGL: the snap animation progress, severed-edge flash colour and
 * class colours are all computed here.
 */
import type { Layer } from "@deck.gl/core";
import { TripsLayer } from "@deck.gl/geo-layers";
import { PathLayer, PolygonLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";

import { HAZARD_CLASSES } from "@arco/schemas";
import type {
  CommunityReport,
  Hazard,
  PopulationCell,
  RoadEdge,
  Route,
  SafeZone,
} from "@arco/schemas";

import {
  EDGE_OPEN,
  EDGE_PENALIZED,
  EDGE_SEVERED,
  POPULATION,
  REPORT_DENY,
  REPORT_PIN,
  ROUTE,
  ROUTE_SNAP,
  SAFE_ZONE,
  hazardColor,
  mix,
} from "@/lib/colors";
import type { RGBA } from "@/lib/colors";
import {
  cellCenter,
  lineCoords,
  outerRings,
  pointLngLat,
  ringCentroid,
  safeZoneCenter,
} from "@/lib/geo";
import type { LngLat } from "@/lib/geo";
import { SNAP_ANIM_MS } from "@/store/world";

export interface LayerInputs {
  hazards: Hazard[];
  edges: RoadEdge[];
  edgeFlash: Record<number, number>;
  routes: Route[];
  snapAt: number | null;
  safeZones: SafeZone[];
  populationCells: PopulationCell[];
  reports: CommunityReport[];
  now: number;
  showLabels: boolean;
  /** bumps whenever the edge set changes (so attributes are not recomputed every frame) */
  edgesRev: number;
}

/** 0 at snap start -> 1 when the snap animation has finished. */
export function snapProgress(snapAt: number | null, now: number): number {
  if (snapAt === null) return 1;
  return Math.max(0, Math.min(1, (now - snapAt) / SNAP_ANIM_MS));
}

/** Route width multiplier: starts fat & white, settles to 1 (ease-out cubic). */
export function snapWidthScale(p: number): number {
  const e = 1 - (1 - p) ** 3;
  return 1 + 1.6 * (1 - e);
}

export function edgeColor(edge: RoadEdge, flashUntil: number | undefined, now: number): RGBA {
  if (edge.status === "severed") {
    if (flashUntil !== undefined && flashUntil > now) {
      const phase = (Math.sin(now / 90) + 1) / 2; // ~5.5 Hz flash
      return mix(EDGE_SEVERED, [255, 255, 255, 255], phase * 0.7);
    }
    return EDGE_SEVERED;
  }
  if (edge.status === "penalized") return EDGE_PENALIZED;
  return EDGE_OPEN;
}

export function edgeWidth(edge: RoadEdge, flashUntil: number | undefined, now: number): number {
  if (edge.status === "severed") return flashUntil !== undefined && flashUntil > now ? 7 : 4;
  if (edge.status === "penalized") return 3;
  return 1.5;
}

function hazardLabel(h: Hazard): string {
  const info = HAZARD_CLASSES[h.hazard_class];
  const src =
    h.source === "simulated"
      ? "SIM"
      : h.source === "community"
        ? "COMMUNITY"
        : h.source.toUpperCase();
  const status = (h.status ?? "active") === "active" ? "" : ` · ${h.status ?? ""}`;
  return `${info.label} ${(h.confidence * 100).toFixed(0)}% · ${src}${status}`;
}

interface HazardRing {
  hazard: Hazard;
  ring: LngLat[];
}

export function buildLayers(inp: LayerInputs): Layer[] {
  const p = snapProgress(inp.snapAt, inp.now);
  const widthScale = snapWidthScale(p);
  const routeColor = mix(ROUTE_SNAP, ROUTE, p);
  const activeHazards = inp.hazards.filter((h) => (h.status ?? "active") !== "resolved");
  const rings: HazardRing[] = activeHazards.flatMap((h) =>
    outerRings(h.geom).map((ring) => ({ hazard: h, ring })),
  );
  const routePaths = inp.routes.map((r) => ({ route: r, path: lineCoords(r.path) }));
  const trips = routePaths.map(({ route, path }) => ({
    route,
    path,
    timestamps: path.map((_, i) => (i / Math.max(1, path.length - 1)) * 100),
  }));
  const loopT = (inp.now / 25) % 140; // flowing dash position
  const flashing = Object.values(inp.edgeFlash).some((until) => until > inp.now);
  // While an edge is flashing recompute colours ~16x/s; otherwise only when edges change.
  const edgeTrigger = `${inp.edgesRev}:${flashing ? Math.floor(inp.now / 60) : 0}`;

  const layers: Layer[] = [
    new PolygonLayer<SafeZone>({
      id: "safe-zones",
      data: inp.safeZones,
      getPolygon: (z) => outerRings(z.geom)[0] ?? [],
      getFillColor: SAFE_ZONE,
      getLineColor: [47, 210, 127, 220],
      lineWidthMinPixels: 2,
      stroked: true,
      filled: true,
      pickable: true,
    }),
    new PathLayer<RoadEdge>({
      id: "edges",
      data: inp.edges,
      getPath: (e) => lineCoords(e.geom),
      getColor: (e) => edgeColor(e, inp.edgeFlash[e.id], inp.now),
      getWidth: (e) => edgeWidth(e, inp.edgeFlash[e.id], inp.now),
      widthUnits: "pixels",
      widthMinPixels: 1,
      capRounded: true,
      jointRounded: true,
      pickable: true,
      updateTriggers: { getColor: [edgeTrigger], getWidth: [edgeTrigger] },
    }),
    new PolygonLayer<HazardRing>({
      id: "hazards",
      data: rings,
      getPolygon: (d) => d.ring,
      getFillColor: (d) =>
        hazardColor(d.hazard.hazard_class, (d.hazard.status ?? "active") === "disputed" ? 50 : 110),
      getLineColor: (d) => hazardColor(d.hazard.hazard_class, 240),
      lineWidthMinPixels: 2,
      getLineWidth: 2,
      lineWidthUnits: "pixels",
      stroked: true,
      filled: true,
      pickable: true,
      updateTriggers: { getFillColor: [rings.length], getLineColor: [rings.length] },
    }),
    new PathLayer<{ route: Route; path: LngLat[] }>({
      id: "routes",
      data: routePaths,
      getPath: (d) => d.path,
      getColor: routeColor,
      getWidth: 5,
      widthScale,
      widthUnits: "pixels",
      widthMinPixels: 2,
      capRounded: true,
      jointRounded: true,
      pickable: true,
      updateTriggers: { getColor: [p], widthScale: [widthScale] },
    }),
    new TripsLayer<{ route: Route; path: LngLat[]; timestamps: number[] }>({
      id: "route-flow",
      data: trips,
      getPath: (d) => d.path,
      getTimestamps: (d) => d.timestamps,
      getColor: [255, 255, 255, 220],
      currentTime: loopT,
      trailLength: 35,
      widthMinPixels: 3,
      capRounded: true,
      fadeTrail: true,
    }),
    new ScatterplotLayer<PopulationCell>({
      id: "population",
      data: inp.populationCells,
      getPosition: (c) => cellCenter(c),
      getRadius: (c) => Math.max(25, Math.sqrt(c.population) * 2),
      radiusUnits: "meters",
      getFillColor: [POPULATION[0], POPULATION[1], POPULATION[2], 60],
      getLineColor: POPULATION,
      lineWidthMinPixels: 1.5,
      stroked: true,
      pickable: true,
    }),
    new ScatterplotLayer<CommunityReport>({
      id: "reports",
      data: inp.reports,
      getPosition: (r) => pointLngLat(r.geom),
      getRadius: 6,
      radiusUnits: "pixels",
      getFillColor: (r) =>
        r.report_type === "deny" || r.report_type === "all_clear" ? REPORT_DENY : REPORT_PIN,
      getLineColor: [7, 9, 13, 255],
      lineWidthMinPixels: 1,
      stroked: true,
      pickable: true,
    }),
  ];

  if (inp.showLabels) {
    layers.push(
      new TextLayer<HazardRing>({
        id: "hazard-labels",
        data: rings,
        getPosition: (d) => ringCentroid(d.ring),
        getText: (d) => hazardLabel(d.hazard),
        getColor: [214, 221, 232, 255],
        getSize: 11,
        fontFamily: "ui-monospace, Menlo, monospace",
        background: true,
        getBackgroundColor: [7, 9, 13, 190],
        backgroundPadding: [4, 2, 4, 2],
        getTextAnchor: "middle",
        getAlignmentBaseline: "center",
      }),
      new TextLayer<SafeZone>({
        id: "safe-zone-labels",
        data: inp.safeZones,
        getPosition: (z) => safeZoneCenter(z),
        getText: (z) => `${z.name} · ${z.current_load ?? 0}/${z.capacity}`,
        getColor: [47, 210, 127, 255],
        getSize: 11,
        fontFamily: "ui-monospace, Menlo, monospace",
        background: true,
        getBackgroundColor: [7, 9, 13, 190],
        backgroundPadding: [4, 2, 4, 2],
        getPixelOffset: [0, -18],
      }),
    );
  }
  return layers;
}

/** Used by tooltips: one line per picked object. */
export function describePicked(obj: unknown): string | null {
  if (typeof obj !== "object" || obj === null) return null;
  if ("hazard" in obj && "ring" in obj) {
    const d = obj as HazardRing;
    return `${hazardLabel(d.hazard)}\nid ${d.hazard.id.slice(0, 8)}`;
  }
  if ("route" in obj) {
    const r = (obj as { route: Route }).route;
    const src = r.explanation_source === "slm" ? "SLM rewrite" : "deterministic text";
    const why = r.rationale ? `\nwhy: ${r.rationale}` : "";
    return `${r.origin_cell_id} → ${r.safe_zone_id}\n${(r.length_m / 1000).toFixed(2)} km · ${r.group_size} people\n${r.instruction}${why}\n[${src}]`;
  }
  if ("current_cost" in obj && "u" in obj) {
    const e = obj as RoadEdge;
    return `${e.name ?? e.highway ?? "road"} #${e.id}\n${e.status ?? "open"} · cost ${e.current_cost.toFixed(1)} (base ${e.base_cost.toFixed(1)})`;
  }
  if ("capacity" in obj && "name" in obj) {
    const z = obj as SafeZone;
    return `${z.name}\nload ${z.current_load ?? 0} / ${z.capacity}`;
  }
  if ("population" in obj) {
    const c = obj as { id: string; population: number };
    return `${c.id}\n${c.population} people`;
  }
  if ("report_type" in obj) {
    const r = obj as CommunityReport;
    return `${r.report_type}${r.hazard_class ? ` · ${r.hazard_class}` : ""}\n${r.note ?? ""}`.trim();
  }
  return null;
}

export const ROUTE_LAYER_IDS = ["routes", "route-flow"] as const;
