import { HAZARD_CLASSES } from "@arco/schemas";
import type { HazardClass } from "@arco/schemas";

export type RGBA = [number, number, number, number];

export function hexToRgba(hex: string, alpha = 255): RGBA {
  const h = hex.replace("#", "");
  const full =
    h.length === 3
      ? h
          .split("")
          .map((c) => c + c)
          .join("")
      : h;
  const n = Number.parseInt(full, 16);
  if (Number.isNaN(n) || full.length !== 6) return [255, 255, 255, alpha];
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255, alpha];
}

export function hazardColor(cls: HazardClass, alpha = 255): RGBA {
  return hexToRgba(HAZARD_CLASSES[cls].color, alpha);
}

export function hazardHex(cls: HazardClass): string {
  return HAZARD_CLASSES[cls].color;
}

export const EDGE_OPEN: RGBA = [90, 110, 140, 90];
export const EDGE_PENALIZED: RGBA = [255, 176, 32, 200];
export const EDGE_SEVERED: RGBA = [255, 59, 48, 230];
export const ROUTE: RGBA = [54, 196, 255, 230];
export const ROUTE_SNAP: RGBA = [255, 255, 255, 255];
export const SAFE_ZONE: RGBA = [47, 210, 127, 90];
export const POPULATION: RGBA = [214, 221, 232, 200];
export const REPORT_PIN: RGBA = [255, 214, 10, 230];
export const REPORT_DENY: RGBA = [120, 140, 170, 200];

export const AGENT_COLORS: Record<"cartographer" | "navigator" | "consensus", string> = {
  cartographer: "#ffb020",
  navigator: "#36c4ff",
  consensus: "#c77dff",
};

export function mix(a: RGBA, b: RGBA, t: number): RGBA {
  const k = Math.max(0, Math.min(1, t));
  return [
    Math.round(a[0] + (b[0] - a[0]) * k),
    Math.round(a[1] + (b[1] - a[1]) * k),
    Math.round(a[2] + (b[2] - a[2]) * k),
    Math.round(a[3] + (b[3] - a[3]) * k),
  ];
}
