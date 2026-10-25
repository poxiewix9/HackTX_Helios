/**
 * On-device fallback router on the real Lahaina tile (exported from the same OSM sample
 * the blackboard ingests) and the arrow guidance math.
 */
import { readFileSync } from "node:fs";

import { angleDiffDeg, bearingDeg, haversineM } from "@/core/geo";
import type { LngLat } from "@/core/geo";
import { classifyTurn, guidance, snapToPath } from "@/core/guidance";
import { MeshNode } from "@/core/node";
import {
  DEFAULT_POLICY,
  hazardEffects,
  isGraphTile,
  nearestNode,
  routeToSafety,
} from "@/core/router";
import type { GraphTile } from "@/core/router";
import { DeviceKey } from "@/core/signing";

const raw: unknown = JSON.parse(
  readFileSync(new URL("../assets/tiles/lahaina_2023.json", import.meta.url), "utf8"),
);
if (!isGraphTile(raw)) throw new Error("tile fixture invalid");
const tile: GraphTile = raw;

// Front Street, south Lahaina (a population cell in the scenario).
const FRONT_ST_SOUTH: LngLat = [-156.6725, 20.866];
const key = DeviceKey.fromSeed("router-test");

describe("graph tile", () => {
  it("is the committed Lahaina OSM sample with its licence", () => {
    expect(tile.scenario_id).toBe("lahaina_2023");
    expect(tile.source.license).toMatch(/ODbL/);
    expect(tile.edges.length).toBe(637);
    expect(Object.keys(tile.nodes).length).toBe(266);
    expect(tile.safe_zones.map((s) => s.id)).toContain("exit-north-honoapiilani");
  });
});

describe("routeToSafety", () => {
  it("finds a route from Front St to a safe zone with no hazards", () => {
    const r = routeToSafety(tile, FRONT_ST_SOUTH, []);
    expect(r).not.toBeNull();
    if (!r) return;
    expect(r.path.length).toBeGreaterThan(5);
    expect(r.lengthM).toBeGreaterThan(500);
    const end = r.path[r.path.length - 1];
    const sz = tile.safe_zones.find((s) => s.id === r.safeZoneId);
    if (!end || !sz) throw new Error("setup");
    expect(haversineM(end, sz.center)).toBeLessThan(400);
  });

  it("reroutes around a hazard pinned on the current route", () => {
    const before = routeToSafety(tile, FRONT_ST_SOUTH, []);
    if (!before) throw new Error("no baseline route");
    // Pin a hazard a quarter of the way along the current route (Front St). Further
    // along, the route passes the single junction onto Honoapiilani Hwy that every exit
    // shares; pinning *that* correctly yields "no route" rather than a path through fire.
    const mid = before.path[Math.floor(before.path.length / 4)];
    if (!mid) throw new Error("setup");
    const pin = key.makeReport({
      lon: mid[0],
      lat: mid[1],
      report_type: "hazard_pin",
      hazard_class: "fire_front",
    });
    const effects = hazardEffects(tile, [pin]);
    const severed = [...effects.values()].filter((e) => e.state === "severed").map((e) => e.edgeId);
    expect(severed.length).toBeGreaterThan(0);
    expect(before.edgeIds.some((id) => severed.includes(id))).toBe(true);

    const after = routeToSafety(tile, FRONT_ST_SOUTH, [pin]);
    expect(after).not.toBeNull();
    if (!after) return;
    // The new route uses none of the severed edges and keeps clear of the pin.
    expect(after.edgeIds.some((id) => severed.includes(id))).toBe(false);
    const minDist = Math.min(...after.path.map((p) => haversineM(p, mid)));
    expect(minDist).toBeGreaterThan(DEFAULT_POLICY.severRadiusM);
    expect(after.edgeIds).not.toEqual(before.edgeIds);
  });

  it("denials and all-clears do not sever anything", () => {
    const pin = key.makeReport({
      lon: FRONT_ST_SOUTH[0],
      lat: FRONT_ST_SOUTH[1],
      report_type: "all_clear",
      target_hazard_id: "00000000-0000-4000-8000-000000000000",
    });
    expect(hazardEffects(tile, [pin]).size).toBe(0);
  });

  it("returns null when every safe zone is cut off", () => {
    const from = nearestNode(tile, FRONT_ST_SOUTH);
    if (!from) throw new Error("setup");
    // Sever everything by pinning a hazard on every edge: no route can exist.
    const pins = tile.edges.map((e) => {
      const c = e.geom[0];
      if (!c) throw new Error("setup");
      return key.makeReport({
        lon: c[0],
        lat: c[1],
        report_type: "hazard_pin",
        hazard_class: "debris",
      });
    });
    expect(routeToSafety(tile, FRONT_ST_SOUTH, pins)).toBeNull();
  });
});

describe("MeshNode.activeRoute", () => {
  it("prefers the Navigator route for my crowd group and falls back to on-device A*", () => {
    const node = new MeshNode({ id: "p", tile, sync: { gossipEveryMs: 0 } });
    node.position = FRONT_ST_SOUTH;
    node.crowdGroupId = "g-front-st";
    const fb = node.activeRoute();
    expect(fb?.source).toBe("on-device-fallback");
    expect(fb?.instruction).toMatch(/not the Navigator/);
    node.ingestRoutes([
      {
        id: "r-nav",
        crowd_group_id: "g-front-st",
        origin_cell_id: "cell-front-st-south",
        safe_zone_id: "exit-north-honoapiilani",
        edge_ids: [1],
        path: { type: "LineString", coordinates: [FRONT_ST_SOUTH, [-156.6849, 20.895]] },
        length_m: 3300,
        cost: 500,
        group_size: 1400,
        instruction: "Head north on Front St",
        computed_at: "2026-10-24T00:00:00.000Z",
        graph_version: 12,
        version: 1,
      },
    ]);
    const nav = node.activeRoute();
    expect(nav?.source).toBe("navigator");
    expect(nav?.instruction).toBe("Head north on Front St");
    expect(nav?.graphVersion).toBe(12);
    // Pins change the fallback, never the Navigator's route.
    node.dropPin("fire_front");
    expect(node.activeRoute()?.source).toBe("navigator");
  });
});

describe("guidance", () => {
  const path: LngLat[] = [
    [-156.6725, 20.866], // start
    [-156.6725, 20.868], // 220 m north
    [-156.6745, 20.868], // then ~210 m west (left turn)
    [-156.6745, 20.87], // then north again (right turn)
  ];

  it("bearing / angle helpers", () => {
    expect(bearingDeg([0, 0], [0, 1])).toBeCloseTo(0, 5);
    expect(bearingDeg([0, 0], [1, 0])).toBeCloseTo(90, 5);
    expect(angleDiffDeg(350, 10)).toBe(20);
    expect(angleDiffDeg(10, 350)).toBe(-20);
    expect(classifyTurn(-90)).toBe("left");
    expect(classifyTurn(35)).toBe("slight-right");
    expect(classifyTurn(170)).toBe("u-turn");
  });

  it("points the arrow along the route relative to the phone heading and names the next turn", () => {
    const g = guidance([-156.6725, 20.8665], 90, path); // facing east, route goes north
    expect(g).not.toBeNull();
    if (!g) return;
    expect(g.bearingDeg).toBeCloseTo(0, 0);
    expect(g.arrowDeg).toBeCloseTo(-90, 0); // rotate the arrow 90° anticlockwise
    expect(g.turn).toBe("left");
    expect(g.instruction).toMatch(/^In \d+ m left/);
    expect(g.remainingM).toBeGreaterThan(500);
    expect(g.offRouteM).toBeLessThan(5);
  });

  it("announces arrival near the end and off-route when far from the path", () => {
    const end = path[path.length - 1];
    if (!end) throw new Error("setup");
    const arrive = guidance([end[0], end[1] - 0.0001], 0, path);
    expect(arrive?.turn).toBe("arrive");
    expect(arrive?.instruction).toMatch(/Safe zone ahead/);
    const off = guidance([-156.68, 20.866], 0, path);
    expect(off?.offRouteM).toBeGreaterThan(60);
    expect(off?.instruction).toMatch(/Return to route/);
    expect(snapToPath([-156.6725, 20.867], path).segment).toBe(0);
  });
});
