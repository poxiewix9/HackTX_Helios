import type { Hazard, LineString, Point, Polygon, PopulationCell, SafeZone } from "@arco/schemas";

export type LngLat = [number, number];
export type BBox = [number, number, number, number];

export function bboxCenter(bbox: BBox): LngLat {
  return [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2];
}

/** Outer ring of a hazard geometry (MultiPolygon -> every outer ring). */
export function outerRings(geom: Hazard["geom"] | Polygon): LngLat[][] {
  if (geom.type === "Polygon") {
    const ring = geom.coordinates[0];
    return [ring.map((c) => [c[0], c[1]] as LngLat)];
  }
  return geom.coordinates.map((poly) => (poly[0] ?? []).map((c) => [c[0], c[1]] as LngLat));
}

export function ringCentroid(ring: LngLat[]): LngLat {
  const n =
    ring.length > 1 && sameCoord(ring[0], ring[ring.length - 1]) ? ring.length - 1 : ring.length;
  if (n === 0) return [0, 0];
  let x = 0;
  let y = 0;
  for (let i = 0; i < n; i += 1) {
    const c = ring[i];
    if (!c) continue;
    x += c[0];
    y += c[1];
  }
  return [x / n, y / n];
}

export function polygonCentroid(poly: Polygon): LngLat {
  return ringCentroid(outerRings(poly)[0] ?? []);
}

export function safeZoneCenter(z: SafeZone): LngLat {
  return polygonCentroid(z.geom);
}

export function cellCenter(c: PopulationCell): LngLat {
  if (c.geom.type === "Point") return [c.geom.coordinates[0], c.geom.coordinates[1]];
  return polygonCentroid(c.geom);
}

export function pointLngLat(p: Point): LngLat {
  return [p.coordinates[0], p.coordinates[1]];
}

export function lineCoords(line: LineString): LngLat[] {
  return line.coordinates.map((c) => [c[0], c[1]] as LngLat);
}

function sameCoord(a: LngLat | undefined, b: LngLat | undefined): boolean {
  return !!a && !!b && a[0] === b[0] && a[1] === b[1];
}

/** Approximate metres per degree at a latitude (good enough for a ~50 m hazard pin). */
export function metresToDegrees(m: number, lat: number): { dLng: number; dLat: number } {
  const dLat = m / 111_320;
  const dLng = m / (111_320 * Math.cos((lat * Math.PI) / 180));
  return { dLng, dLat };
}

/** Square polygon of `halfSideM` metres around a point: what the chaos button injects. */
export function squareAround(center: LngLat, halfSideM: number): Polygon {
  const { dLng, dLat } = metresToDegrees(halfSideM, center[1]);
  const [x, y] = center;
  return {
    type: "Polygon",
    coordinates: [
      [
        [x - dLng, y - dLat],
        [x + dLng, y - dLat],
        [x + dLng, y + dLat],
        [x - dLng, y + dLat],
        [x - dLng, y - dLat],
      ],
    ],
  };
}

/** Haversine length of a lng/lat path in metres. */
export function pathLengthM(coords: LngLat[]): number {
  let total = 0;
  for (let i = 1; i < coords.length; i += 1) {
    const a = coords[i - 1];
    const b = coords[i];
    if (!a || !b) continue;
    total += haversineM(a, b);
  }
  return total;
}

export function haversineM(a: LngLat, b: LngLat): number {
  const R = 6_371_000;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(b[1] - a[1]);
  const dLng = toRad(b[0] - a[0]);
  const la1 = toRad(a[1]);
  const la2 = toRad(b[1]);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(la1) * Math.cos(la2) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}
