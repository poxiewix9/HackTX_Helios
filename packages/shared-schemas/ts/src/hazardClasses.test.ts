import { describe, expect, it } from "vitest";
import {
  HAZARD_CLASSES,
  HAZARD_CLASS_IDS,
  MACRO_CLASSES,
  MICRO_CLASSES,
  classesForStream,
  hazardClassInfo,
} from "./index";

describe("hazard taxonomy", () => {
  it("has 12 classes with smoke shared across streams", () => {
    expect(HAZARD_CLASS_IDS).toHaveLength(12);
    expect(hazardClassInfo("smoke").streams).toEqual(["macro", "micro"]);
  });

  it("macro/micro lists are consistent with per-class stream metadata", () => {
    for (const id of HAZARD_CLASS_IDS) {
      const info = HAZARD_CLASSES[id];
      expect(MACRO_CLASSES.includes(id)).toBe(info.streams.includes("macro"));
      expect(MICRO_CLASSES.includes(id)).toBe(info.streams.includes("micro"));
    }
    expect(classesForStream("macro")).toBe(MACRO_CLASSES);
  });

  it("every class has a hex color for overlays", () => {
    for (const id of HAZARD_CLASS_IDS) {
      expect(HAZARD_CLASSES[id].color).toMatch(/^#[0-9a-fA-F]{6}$/);
    }
  });
});
