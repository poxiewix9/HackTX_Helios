import { describe, expect, it } from "vitest";

import { EDGE_OPEN, EDGE_PENALIZED, EDGE_SEVERED, hazardColor, hexToRgba } from "@/lib/colors";
import { pathLengthM, squareAround } from "@/lib/geo";
import { SNAP_ANIM_MS } from "@/store/world";

import { edgeColor, edgeWidth, snapProgress, snapWidthScale } from "@/components/map/layers";

import { edge } from "./fixtures";

describe("snap animation math", () => {
  it("runs from 0 to 1 over SNAP_ANIM_MS (< 500 ms) and settles at width 1", () => {
    expect(SNAP_ANIM_MS).toBeLessThan(500);
    expect(snapProgress(1000, 1000)).toBe(0);
    expect(snapProgress(1000, 1000 + SNAP_ANIM_MS / 2)).toBeCloseTo(0.5);
    expect(snapProgress(1000, 1000 + SNAP_ANIM_MS + 1)).toBe(1);
    expect(snapProgress(null, 5)).toBe(1);
    expect(snapWidthScale(0)).toBeGreaterThan(2);
    expect(snapWidthScale(1)).toBe(1);
    expect(snapWidthScale(0.5)).toBeGreaterThan(snapWidthScale(0.9));
  });
});

describe("edge styling", () => {
  it("severed edges flash (brighter than steady red) only while the flash window is open", () => {
    const e = edge(1, "severed");
    const steady = edgeColor(e, undefined, 10_000);
    expect(steady).toEqual(EDGE_SEVERED);
    const flashing = edgeColor(e, 12_000, 10_000);
    expect(flashing[1]).toBeGreaterThanOrEqual(EDGE_SEVERED[1]); // mixes toward white
    expect(edgeWidth(e, 12_000, 10_000)).toBeGreaterThan(edgeWidth(e, undefined, 10_000));
    expect(edgeColor(e, 9_000, 10_000)).toEqual(EDGE_SEVERED); // window closed
    expect(edgeColor(edge(2, "penalized"), undefined, 0)).toEqual(EDGE_PENALIZED);
    expect(edgeColor(edge(3), undefined, 0)).toEqual(EDGE_OPEN);
  });
});

describe("colours come from the shared taxonomy", () => {
  it("maps hazard classes to their contract colour", () => {
    expect(hexToRgba("#ff3b30")).toEqual([255, 59, 48, 255]);
    expect(hexToRgba("#fff", 10)).toEqual([255, 255, 255, 10]);
    expect(hazardColor("fire_front", 100)[3]).toBe(100);
    expect(hazardColor("fire_front").slice(0, 3)).toEqual(hexToRgba("#ff3b30").slice(0, 3));
  });
});

describe("geo helpers", () => {
  it("builds a closed square of the requested size around a point", () => {
    const sq = squareAround([-156.67, 20.87], 60);
    const ring = sq.coordinates[0];
    expect(ring.length).toBe(5);
    expect(ring[0]).toEqual(ring[4]);
    const side = pathLengthM([
      [ring[0][0], ring[0][1]],
      [ring[1][0], ring[1][1]],
    ]);
    expect(side).toBeGreaterThan(115);
    expect(side).toBeLessThan(125);
  });
});
