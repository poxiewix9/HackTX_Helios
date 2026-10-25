import { pointAlong, step } from "@/phone/walker";
import { haversineM } from "@/core/geo";
import type { LngLat } from "@/core/geo";

const path: LngLat[] = [
  [-156.6725, 20.866],
  [-156.6725, 20.868], // ~221 m north
  [-156.6745, 20.868], // ~208 m west
];

describe("simulated walker", () => {
  it("interpolates along the path with the segment heading", () => {
    const p0 = pointAlong(path, 0);
    expect(p0?.position).toEqual(path[0]);
    expect(p0?.headingDeg).toBeCloseTo(0, 0);
    const p1 = pointAlong(path, 110);
    if (!p1 || !path[0]) throw new Error("setup");
    expect(haversineM(path[0], p1.position)).toBeCloseTo(110, 0);
    const p2 = pointAlong(path, 300);
    expect(p2?.headingDeg).toBeCloseTo(270, 0);
    const end = pointAlong(path, 10_000);
    expect(end?.position).toEqual(path[2]);
  });

  it("advances at walking speed and re-anchors when the route changes", () => {
    const start = path[0];
    if (!start) throw new Error("setup");
    let s = { position: start, headingDeg: 0, alongM: 0 };
    for (let i = 0; i < 60; i++) s = step(s, path, 1, 1.4);
    expect(s.alongM).toBeCloseTo(84, 0);
    if (!path[0]) throw new Error("setup");
    expect(haversineM(path[0], s.position)).toBeCloseTo(84, 0);
    // New route starting where we are: progress is measured along the new path.
    const detour: LngLat[] = [s.position, [s.position[0] + 0.002, s.position[1]]];
    const s2 = step(s, detour, 1, 1.4);
    expect(s2.alongM).toBeCloseTo(1.4, 1);
    expect(s2.headingDeg).toBeCloseTo(90, 0);
  });
});
