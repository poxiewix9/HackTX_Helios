/**
 * SIMULATED position: the phone "walks" along the active route at walking speed. Real
 * GPS/compass would replace `position`/`heading` here (expo-location is the obvious
 * provider; it is deliberately not wired so the demo runs identically in a simulator).
 * Pure math, unit-tested.
 */
import { bearingDeg, haversineM, toLocalM } from "@/core/geo";
import type { LngLat } from "@/core/geo";

export interface WalkerState {
  position: LngLat;
  headingDeg: number;
  /** Metres travelled along the current path. */
  alongM: number;
}

/** Point `alongM` metres along `path` plus the heading of that segment. */
export function pointAlong(
  path: LngLat[],
  alongM: number,
): { position: LngLat; headingDeg: number } | null {
  if (path.length === 0) return null;
  const first = path[0];
  if (!first) return null;
  if (path.length === 1) return { position: first, headingDeg: 0 };
  let remaining = Math.max(0, alongM);
  for (let i = 0; i + 1 < path.length; i++) {
    const a = path[i];
    const b = path[i + 1];
    if (!a || !b) continue;
    const seg = haversineM(a, b);
    if (remaining <= seg || i + 2 === path.length) {
      const t = seg === 0 ? 0 : Math.min(1, remaining / seg);
      return {
        position: [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t],
        headingDeg: bearingDeg(a, b),
      };
    }
    remaining -= seg;
  }
  const last = path[path.length - 1];
  const prev = path[path.length - 2];
  if (!last || !prev) return null;
  return { position: last, headingDeg: bearingDeg(prev, last) };
}

/** Advance `dtS` seconds at `speedMps` along `path`, re-anchoring if the path changed. */
export function step(
  state: WalkerState,
  path: LngLat[],
  dtS: number,
  speedMps: number,
): WalkerState {
  if (path.length === 0) return state;
  // Re-anchor: find how far along the (possibly new) path our position is.
  let best = { d: Infinity, along: 0 };
  let acc = 0;
  for (let i = 0; i + 1 < path.length; i++) {
    const a = path[i];
    const b = path[i + 1];
    if (!a || !b) continue;
    const seg = haversineM(a, b);
    // Exact projection of the position onto the segment (local metres).
    const [px, py] = toLocalM(state.position, a);
    const [bx, by] = toLocalM(b, a);
    const len2 = bx * bx + by * by;
    const t = len2 === 0 ? 0 : Math.max(0, Math.min(1, (px * bx + py * by) / len2));
    const d = Math.hypot(px - t * bx, py - t * by);
    if (d < best.d) best = { d, along: acc + seg * t };
    acc += seg;
  }
  const along = best.along + dtS * speedMps;
  const pt = pointAlong(path, along);
  if (!pt) return state;
  return { position: pt.position, headingDeg: pt.headingDeg, alongM: along };
}
