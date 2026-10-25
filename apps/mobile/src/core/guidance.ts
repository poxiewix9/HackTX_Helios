/**
 * Arrow guidance: from the device position + heading and a route polyline, compute the
 * arrow rotation (relative to the phone's heading), remaining distance and a one-line
 * instruction. Pure math; the UI renders the result and triggers haptics/speech when the
 * `turn` changes.
 */
import { angleDiffDeg, bearingDeg, haversineM, toLocalM } from "./geo";
import type { LngLat } from "./geo";

export type Turn =
  | "straight"
  | "slight-left"
  | "left"
  | "sharp-left"
  | "slight-right"
  | "right"
  | "sharp-right"
  | "u-turn"
  | "arrive";

export interface Guidance {
  /** Degrees to rotate the arrow clockwise from "phone up" so it points along the route. */
  arrowDeg: number;
  /** True bearing of the next leg. */
  bearingDeg: number;
  /** Metres to the next manoeuvre point. */
  nextM: number;
  /** Metres remaining to the end of the route. */
  remainingM: number;
  turn: Turn;
  instruction: string;
  /** Index of the next vertex on the path. */
  nextIndex: number;
  /** Metres off the route (for "return to route" messaging). */
  offRouteM: number;
}

export const ARRIVE_M = 25;
export const OFF_ROUTE_M = 60;

export function classifyTurn(deltaDeg: number): Turn {
  const a = Math.abs(deltaDeg);
  if (a < 20) return "straight";
  if (a > 160) return "u-turn";
  const side = deltaDeg < 0 ? "left" : "right";
  if (a < 50) return `slight-${side}`;
  if (a < 120) return side;
  return `sharp-${side}`;
}

function fmtM(m: number): string {
  if (m < 1000) return `${Math.max(5, Math.round(m / 5) * 5)} m`;
  return `${(m / 1000).toFixed(1)} km`;
}

export function instructionFor(
  turn: Turn,
  nextM: number,
  street: string | null,
  remainingM: number,
): string {
  if (turn === "arrive") return `Safe zone ahead · ${fmtM(remainingM)}`;
  const where = street ? ` onto ${street}` : "";
  switch (turn) {
    case "straight":
      return `Continue ${fmtM(nextM)}`;
    case "u-turn":
      return `Turn around in ${fmtM(nextM)}`;
    default:
      return `In ${fmtM(nextM)} ${turn.replace("-", " ")}${where}`;
  }
}

/** Nearest point on the path to `pos` → index of the segment it lies on and the distance. */
export function snapToPath(pos: LngLat, path: LngLat[]): { segment: number; offM: number } {
  let best = { segment: 0, offM: Infinity };
  for (let i = 0; i + 1 < path.length; i++) {
    const a = path[i];
    const b = path[i + 1];
    if (!a || !b) continue;
    const [px, py] = toLocalM(pos, a);
    const [bx, by] = toLocalM(b, a);
    const len2 = bx * bx + by * by;
    const t = len2 === 0 ? 0 : Math.max(0, Math.min(1, (px * bx + py * by) / len2));
    const d = Math.hypot(px - t * bx, py - t * by);
    if (d < best.offM) best = { segment: i, offM: d };
  }
  if (path.length === 1 && path[0]) best = { segment: 0, offM: haversineM(pos, path[0]) };
  return best;
}

export function guidance(
  pos: LngLat,
  headingDeg: number,
  path: LngLat[],
  streetAt?: (vertexIndex: number) => string | null,
): Guidance | null {
  if (path.length === 0) return null;
  const last = path[path.length - 1];
  if (!last) return null;
  const remainingToEnd = haversineM(pos, last);
  if (path.length === 1 || remainingToEnd <= ARRIVE_M) {
    const b = bearingDeg(pos, last);
    return {
      arrowDeg: angleDiffDeg(headingDeg, b),
      bearingDeg: b,
      nextM: remainingToEnd,
      remainingM: remainingToEnd,
      turn: "arrive",
      instruction: instructionFor("arrive", remainingToEnd, null, remainingToEnd),
      nextIndex: path.length - 1,
      offRouteM: 0,
    };
  }
  const { segment, offM } = snapToPath(pos, path);
  let nextIndex = segment + 1;
  // Skip vertices we are already on top of.
  while (nextIndex < path.length - 1) {
    const v = path[nextIndex];
    if (!v || haversineM(pos, v) > 8) break;
    nextIndex += 1;
  }
  const next = path[nextIndex] ?? last;
  const legBearing = bearingDeg(pos, next);
  let remaining = haversineM(pos, next);
  for (let i = nextIndex; i + 1 < path.length; i++) {
    const a = path[i];
    const b = path[i + 1];
    if (a && b) remaining += haversineM(a, b);
  }
  // Manoeuvre at `next`: compare this leg's bearing with the following leg's.
  let turn: Turn = "straight";
  let nextM = haversineM(pos, next);
  const after = path[nextIndex + 1];
  if (after) {
    const delta = angleDiffDeg(legBearing, bearingDeg(next, after));
    turn = classifyTurn(delta);
    // Merge consecutive near-straight vertices so the instruction names the real turn.
    let j = nextIndex;
    while (turn === "straight" && j + 2 < path.length) {
      const a = path[j];
      const b = path[j + 1];
      const c = path[j + 2];
      if (!a || !b || !c) break;
      nextM += haversineM(b, c);
      turn = classifyTurn(angleDiffDeg(bearingDeg(a, b), bearingDeg(b, c)));
      j += 1;
      if (turn !== "straight") {
        nextIndex = j + 1;
        break;
      }
    }
    if (turn === "straight") {
      turn = "arrive";
      nextM = remaining;
      nextIndex = path.length - 1;
    }
  } else {
    turn = "arrive";
    nextM = remaining;
  }
  const street = streetAt ? streetAt(nextIndex) : null;
  const instruction =
    offM > OFF_ROUTE_M
      ? `Return to route · ${fmtM(offM)} off`
      : instructionFor(turn, nextM, street, remaining);
  return {
    arrowDeg: angleDiffDeg(headingDeg, legBearing),
    bearingDeg: legBearing,
    nextM,
    remainingM: remaining,
    turn,
    instruction,
    nextIndex,
    offRouteM: offM,
  };
}
