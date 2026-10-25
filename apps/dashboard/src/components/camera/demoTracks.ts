/**
 * Hardcoded multi-object tracks for the demo cameras.
 * The clips are local Mixkit stock (intersection, boulevard). Boxes are scripted so the
 * dashboard can show dense person / vehicle tracking without a detector on these files.
 */

export type TrackKind = "person" | "vehicle" | "cyclist" | "bus";

export interface DemoCamera {
  id: string;
  name: string;
  place: string;
  src: string;
}

export interface TrackBox {
  id: string;
  kind: TrackKind;
  confidence: number;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface FrameRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export const TRACK_COLOR: Record<TrackKind, string> = {
  person: "#49D6FF",
  vehicle: "#FFC14A",
  cyclist: "#D7A6FF",
  bus: "#FF8A5B",
};

export const TRACK_LABEL: Record<TrackKind, string> = {
  person: "person",
  vehicle: "vehicle",
  cyclist: "cycle",
  bus: "bus",
};

const FRONT_CAM: DemoCamera = {
  id: "cam-front",
  name: "CAM-01",
  place: "Front St",
  src: "/camera/front-st.mp4",
};

const BLVD_CAM: DemoCamera = {
  id: "cam-blvd",
  name: "CAM-02",
  place: "Boulevard",
  src: "/camera/boulevard.mp4",
};

export const DEMO_CAMERAS: readonly DemoCamera[] = [FRONT_CAM, BLVD_CAM];

export function cameraById(id: string | null): DemoCamera {
  return DEMO_CAMERAS.find((camera) => camera.id === id) ?? FRONT_CAM;
}

/** object-cover rectangle of a video inside a frame, in frame pixels. */
export function coveredRect(vw: number, vh: number, cw: number, ch: number): FrameRect {
  if (vw <= 0 || vh <= 0 || cw <= 0 || ch <= 0)
    return { x: 0, y: 0, w: Math.max(0, cw), h: Math.max(0, ch) };
  const scale = Math.max(cw / vw, ch / vh);
  const w = vw * scale;
  const h = vh * scale;
  return { x: (cw - w) / 2, y: (ch - h) / 2, w, h };
}

interface Spec {
  id: string;
  kind: TrackKind;
  confidence: number;
  motion: "slide" | "hold";
  period: number;
  phase: number;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  w: number;
  h: number;
  /** Fraction of the cycle a sliding track stays on screen. */
  visible: number;
}

function slide(
  id: string,
  kind: TrackKind,
  confidence: number,
  period: number,
  phase: number,
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  w: number,
  h: number,
  visible = 0.86,
): Spec {
  return { id, kind, confidence, motion: "slide", period, phase, x0, y0, x1, y1, w, h, visible };
}

function hold(
  id: string,
  kind: TrackKind,
  confidence: number,
  x: number,
  y: number,
  w: number,
  h: number,
  phase: number,
): Spec {
  return {
    id,
    kind,
    confidence,
    motion: "hold",
    period: 10,
    phase,
    x0: x - 0.008,
    y0: y - 0.012,
    x1: x + 0.008,
    y1: y + 0.012,
    w,
    h,
    visible: 1,
  };
}

/** Intersection: sidewalk crowd, crosswalk flow, lanes of traffic. */
const FRONT: readonly Spec[] = [
  hold("P-01", "person", 0.96, 0.03, 0.16, 0.042, 0.085, 0.2),
  hold("P-02", "person", 0.91, 0.09, 0.22, 0.04, 0.08, 0.9),
  hold("P-03", "person", 0.88, 0.035, 0.34, 0.04, 0.09, 1.4),
  hold("P-04", "person", 0.94, 0.11, 0.4, 0.038, 0.08, 2.1),
  hold("P-05", "person", 0.9, 0.04, 0.55, 0.042, 0.09, 2.8),
  hold("P-06", "person", 0.86, 0.1, 0.66, 0.04, 0.085, 3.3),
  hold("P-07", "person", 0.92, 0.045, 0.78, 0.04, 0.08, 4.0),
  hold("P-08", "person", 0.89, 0.12, 0.84, 0.038, 0.075, 4.6),
  hold("P-19", "person", 0.9, 0.9, 0.4, 0.034, 0.07, 0.4),
  hold("P-20", "person", 0.87, 0.94, 0.48, 0.032, 0.07, 1.1),
  hold("P-21", "person", 0.93, 0.88, 0.52, 0.034, 0.075, 1.8),
  slide("P-09", "person", 0.95, 10, 0.0, 0.2, 0.47, 0.84, 0.5, 0.036, 0.08),
  slide("P-10", "person", 0.91, 9.2, 0.18, 0.82, 0.5, 0.18, 0.53, 0.034, 0.078),
  slide("P-11", "person", 0.88, 11, 0.33, 0.22, 0.54, 0.8, 0.56, 0.036, 0.082),
  slide("P-12", "person", 0.97, 8.4, 0.47, 0.78, 0.48, 0.24, 0.46, 0.034, 0.076),
  slide("P-13", "person", 0.9, 10.5, 0.62, 0.26, 0.58, 0.86, 0.6, 0.038, 0.084),
  slide("P-14", "person", 0.86, 9.6, 0.08, 0.84, 0.56, 0.2, 0.58, 0.032, 0.074),
  slide("P-15", "person", 0.93, 12, 0.41, 0.18, 0.51, 0.7, 0.49, 0.04, 0.09),
  slide("P-16", "person", 0.89, 8.8, 0.73, 0.72, 0.6, 0.28, 0.57, 0.034, 0.08),
  slide("P-17", "person", 0.94, 10.8, 0.25, 0.3, 0.44, 0.88, 0.47, 0.036, 0.078),
  slide("P-18", "person", 0.87, 9.4, 0.55, 0.86, 0.62, 0.22, 0.6, 0.033, 0.076),
  slide("C-01", "cyclist", 0.84, 7.5, 0.15, 0.12, 0.46, 0.55, 0.52, 0.055, 0.07, 0.7),
  slide("V-01", "vehicle", 0.98, 8, 0.05, 0.34, 0.02, 0.3, 0.36, 0.13, 0.13),
  slide("V-02", "vehicle", 0.96, 7.2, 0.4, 0.5, 0.0, 0.46, 0.34, 0.14, 0.14),
  slide("V-03", "vehicle", 0.94, 9, 0.2, 0.64, 0.04, 0.6, 0.38, 0.12, 0.13),
  slide("V-04", "vehicle", 0.97, 6.8, 0.62, 0.78, 0.02, 0.74, 0.36, 0.12, 0.12),
  slide("V-05", "vehicle", 0.91, 8.6, 0.78, 0.88, 0.0, 0.84, 0.28, 0.1, 0.1),
  slide("V-06", "vehicle", 0.93, 11, 0.12, 0.42, 0.18, 0.36, 0.58, 0.15, 0.14, 0.72),
  slide("V-07", "vehicle", 0.9, 7.8, 0.88, 0.7, 0.22, 0.66, 0.62, 0.12, 0.12, 0.7),
  slide("B-01", "bus", 0.99, 14, 0.35, 0.36, -0.02, 0.32, 0.34, 0.2, 0.2, 0.55),
];

/** Boulevard: jammed lanes, near traffic, both sidewalks. */
const BLVD: readonly Spec[] = [
  hold("V-11", "vehicle", 0.95, 0.4, 0.48, 0.042, 0.055, 0.2),
  hold("V-12", "vehicle", 0.92, 0.48, 0.5, 0.046, 0.06, 0.6),
  hold("V-13", "vehicle", 0.9, 0.56, 0.52, 0.044, 0.058, 1.1),
  hold("V-14", "vehicle", 0.94, 0.63, 0.55, 0.05, 0.065, 1.7),
  hold("V-15", "vehicle", 0.88, 0.42, 0.58, 0.05, 0.07, 2.2),
  hold("V-16", "vehicle", 0.93, 0.51, 0.62, 0.052, 0.072, 2.8),
  hold("V-17", "vehicle", 0.91, 0.59, 0.66, 0.055, 0.075, 3.4),
  hold("V-18", "vehicle", 0.96, 0.46, 0.72, 0.06, 0.08, 4.0),
  hold("V-19", "vehicle", 0.89, 0.56, 0.76, 0.062, 0.082, 4.5),
  hold("V-20", "vehicle", 0.97, 0.64, 0.8, 0.07, 0.09, 5.1),
  slide("V-21", "vehicle", 0.9, 5.5, 0.0, 0.1, 0.5, 0.02, 0.82, 0.07, 0.06, 0.62),
  slide("V-22", "vehicle", 0.86, 4.8, 0.35, 0.16, 0.58, 0.06, 0.9, 0.08, 0.065, 0.58),
  slide("V-23", "vehicle", 0.92, 6.4, 0.2, 0.48, 0.84, 0.42, 0.96, 0.07, 0.08, 0.7),
  slide("V-24", "vehicle", 0.94, 7, 0.55, 0.58, 0.86, 0.52, 0.98, 0.075, 0.085, 0.66),
  slide("V-25", "vehicle", 0.88, 5.2, 0.72, 0.36, 0.9, 0.3, 0.99, 0.08, 0.07, 0.6),
  slide("V-26", "vehicle", 0.95, 8.5, 0.15, 0.66, 0.42, 0.62, 0.7, 0.05, 0.06),
  hold("P-31", "person", 0.9, 0.82, 0.58, 0.028, 0.06, 0.3),
  hold("P-32", "person", 0.86, 0.86, 0.62, 0.026, 0.055, 0.9),
  hold("P-33", "person", 0.92, 0.9, 0.66, 0.028, 0.06, 1.4),
  hold("P-34", "person", 0.88, 0.84, 0.72, 0.026, 0.055, 2.0),
  hold("P-35", "person", 0.91, 0.89, 0.78, 0.028, 0.06, 2.6),
  hold("P-36", "person", 0.87, 0.93, 0.84, 0.026, 0.055, 3.2),
  hold("P-37", "person", 0.93, 0.2, 0.5, 0.026, 0.055, 1.2),
  hold("P-38", "person", 0.89, 0.24, 0.56, 0.024, 0.05, 2.4),
  hold("P-39", "person", 0.85, 0.18, 0.62, 0.024, 0.05, 3.6),
  slide("C-02", "cyclist", 0.82, 9, 0.4, 0.78, 0.7, 0.95, 0.86, 0.03, 0.05, 0.75),
  slide("B-02", "bus", 0.96, 12, 0.5, 0.5, 0.4, 0.46, 0.62, 0.07, 0.09, 0.5),
];

const SETS: Record<string, readonly Spec[]> = {
  [FRONT_CAM.id]: FRONT,
  [BLVD_CAM.id]: BLVD,
};

function frac(n: number): number {
  const u = n % 1;
  return u < 0 ? u + 1 : u;
}

function place(spec: Spec, timeS: number): TrackBox | null {
  let rawX: number;
  let rawY: number;
  if (spec.motion === "hold") {
    rawX =
      (spec.x0 + spec.x1) / 2 +
      Math.sin(timeS * 0.65 + spec.phase * 6.2) * Math.abs(spec.x1 - spec.x0);
    rawY =
      (spec.y0 + spec.y1) / 2 +
      Math.cos(timeS * 0.5 + spec.phase * 4.1) * Math.abs(spec.y1 - spec.y0);
  } else {
    const u = frac(timeS / spec.period + spec.phase);
    if (u > spec.visible) return null;
    const p = u / Math.max(spec.visible, 1e-6);
    rawX = spec.x0 + (spec.x1 - spec.x0) * p;
    rawY = spec.y0 + (spec.y1 - spec.y0) * p;
  }
  const x = Math.min(Math.max(0, rawX), 1 - spec.w);
  const y = Math.min(Math.max(0, rawY), 1 - spec.h);
  const confidence = Math.min(
    1,
    Math.max(0, spec.confidence + Math.sin(timeS * 1.6 + spec.phase * 9) * 0.01),
  );
  return { id: spec.id, kind: spec.kind, confidence, x, y, w: spec.w, h: spec.h };
}

export function tracksAt(cameraId: string, timeS: number): TrackBox[] {
  const specs = SETS[cameraId] ?? FRONT;
  const boxes: TrackBox[] = [];
  for (const spec of specs) {
    const placed = place(spec, timeS);
    if (placed) boxes.push(placed);
  }
  return boxes;
}

export function countKinds(boxes: readonly TrackBox[]): Record<TrackKind, number> {
  const counts: Record<TrackKind, number> = { person: 0, vehicle: 0, cyclist: 0, bus: 0 };
  for (const box of boxes) counts[box.kind] += 1;
  return counts;
}
