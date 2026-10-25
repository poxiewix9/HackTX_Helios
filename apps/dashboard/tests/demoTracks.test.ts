import { describe, expect, it } from "vitest";

import { DEMO_CAMERAS, coveredRect, tracksAt } from "@/components/camera/demoTracks";

describe("demo camera tracks", () => {
  it("keeps a dense set of in-frame boxes on both cameras", () => {
    for (const camera of DEMO_CAMERAS) {
      const boxes = tracksAt(camera.id, 3.2);
      expect(boxes.length).toBeGreaterThan(12);
      const ids = new Set<string>();
      for (const box of boxes) {
        expect(box.x).toBeGreaterThanOrEqual(0);
        expect(box.y).toBeGreaterThanOrEqual(0);
        expect(box.x + box.w).toBeLessThanOrEqual(1.001);
        expect(box.y + box.h).toBeLessThanOrEqual(1.001);
        expect(box.confidence).toBeGreaterThan(0.5);
        expect(box.confidence).toBeLessThanOrEqual(1);
        ids.add(box.id);
      }
      expect(ids.size).toBe(boxes.length);
    }
  });

  it("moves a crosswalk track as the clip advances", () => {
    const early = tracksAt("cam-front", 1).find((box) => box.id === "P-09");
    const later = tracksAt("cam-front", 3).find((box) => box.id === "P-09");
    expect(early).toBeDefined();
    expect(later).toBeDefined();
    if (!early || !later) return;
    expect(Math.abs(early.x - later.x) + Math.abs(early.y - later.y)).toBeGreaterThan(0.01);
  });

  it("covers a wide video the way object-cover does", () => {
    const rect = coveredRect(1600, 900, 100, 100);
    expect(rect.h).toBeCloseTo(100);
    expect(rect.w).toBeGreaterThan(100);
    expect(rect.x).toBeLessThan(0);
    expect(rect.y).toBeCloseTo(0);
  });
});
