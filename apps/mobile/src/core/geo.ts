/** Small geodesy helpers shared by the router and the arrow guidance. */

export type LngLat = [number, number];

const R = 6371008.8;
const toRad = (d: number) => (d * Math.PI) / 180;
const toDeg = (r: number) => (r * 180) / Math.PI;

export function haversineM(a: LngLat, b: LngLat): number {
  const dLat = toRad(b[1] - a[1]);
  const dLng = toRad(b[0] - a[0]);
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(a[1])) * Math.cos(toRad(b[1])) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(s)));
}

/** Initial bearing from a to b in degrees clockwise from true north, [0, 360). */
export function bearingDeg(a: LngLat, b: LngLat): number {
  const φ1 = toRad(a[1]);
  const φ2 = toRad(b[1]);
  const Δλ = toRad(b[0] - a[0]);
  const y = Math.sin(Δλ) * Math.cos(φ2);
  const x = Math.cos(φ1) * Math.sin(φ2) - Math.sin(φ1) * Math.cos(φ2) * Math.cos(Δλ);
  return (toDeg(Math.atan2(y, x)) + 360) % 360;
}

/** Signed smallest difference `to - from` in degrees, (-180, 180]. */
export function angleDiffDeg(from: number, to: number): number {
  let d = ((to - from) % 360) + 360;
  d %= 360;
  return d > 180 ? d - 360 : d;
}

/** Local equirectangular metres relative to `origin` (fine for a town-sized area). */
export function toLocalM(p: LngLat, origin: LngLat): [number, number] {
  const kx = (Math.PI / 180) * R * Math.cos(toRad(origin[1]));
  const ky = (Math.PI / 180) * R;
  return [(p[0] - origin[0]) * kx, (p[1] - origin[1]) * ky];
}

/** Distance in metres from point p to segment ab (all lon/lat). */
export function pointToSegmentM(p: LngLat, a: LngLat, b: LngLat): number {
  const [px, py] = toLocalM(p, a);
  const [bx, by] = toLocalM(b, a);
  const len2 = bx * bx + by * by;
  if (len2 === 0) return Math.hypot(px, py);
  const t = Math.max(0, Math.min(1, (px * bx + py * by) / len2));
  return Math.hypot(px - t * bx, py - t * by);
}

export function pointToPolylineM(p: LngLat, line: LngLat[]): number {
  let best = Infinity;
  for (let i = 0; i + 1 < line.length; i++) {
    const a = line[i];
    const b = line[i + 1];
    if (a && b) best = Math.min(best, pointToSegmentM(p, a, b));
  }
  if (line.length === 1 && line[0]) best = haversineM(p, line[0]);
  return best;
}

export function polylineLengthM(line: LngLat[]): number {
  let total = 0;
  for (let i = 0; i + 1 < line.length; i++) {
    const a = line[i];
    const b = line[i + 1];
    if (a && b) total += haversineM(a, b);
  }
  return total;
}
