/**
 * MapLibre custom layer rendering with three.js, camera-locked to the map:
 *   - extruded hazard volumes (height ∝ severity, colour = class colour)
 *   - crowd flow: one InstancedMesh of particles streaming along every route
 *   - pulsing safe-zone beacons (instanced rings)
 * Frame rate is *measured* from the render callback (rolling 1 s window) and reported
 * through `onFps`; the layer requests a repaint every frame so the number is real.
 */
import type { CustomLayerInterface, Map as MaplibreMap } from "maplibre-gl";
import { MercatorCoordinate } from "maplibre-gl";
import * as THREE from "three";

import type { Hazard, Route, SafeZone } from "@arco/schemas";

import { hazardHex } from "@/lib/colors";
import { lineCoords, outerRings, safeZoneCenter } from "@/lib/geo";
import type { LngLat } from "@/lib/geo";

export interface ThreeLayerData {
  hazards: Hazard[];
  routes: Route[];
  safeZones: SafeZone[];
}

interface RouteTrack {
  points: THREE.Vector3[];
  cum: number[]; // cumulative length (m)
  total: number;
  count: number; // particles on this route
}

const MAX_PARTICLES = 6000;
const PARTICLE_SPEED_MPS = 28; // visual flow speed, not a physical claim

export class ThreeLayer implements CustomLayerInterface {
  readonly id: string;
  readonly type = "custom" as const;
  readonly renderingMode = "3d" as const;

  private map: MaplibreMap | null = null;
  private renderer: THREE.WebGLRenderer | null = null;
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.Camera();
  private origin: MercatorCoordinate;
  private scale = 1;
  private readonly hazardGroup = new THREE.Group();
  private readonly beaconGroup = new THREE.Group();
  private particles: THREE.InstancedMesh | null = null;
  private tracks: RouteTrack[] = [];
  private particleTrack: Uint16Array = new Uint16Array(0);
  private particlePhase: Float32Array = new Float32Array(0);
  private beacons: THREE.InstancedMesh | null = null;
  private beaconCount = 0;
  private hazardKey = "";
  private routeKey = "";
  private zoneKey = "";
  private frameTimes: number[] = [];
  private lastFpsReport = 0;
  private readonly tmpObj = new THREE.Object3D();
  private pending: ThreeLayerData = { hazards: [], routes: [], safeZones: [] };
  private readonly onFps: ((fps: number) => void) | undefined;
  enabled = true;

  /** What is currently in the scene (reported in the UI next to the measured FPS). */
  stats(): { volumes: number; particles: number; beacons: number } {
    return {
      volumes: this.hazardGroup.children.length,
      particles: this.particles?.count ?? 0,
      beacons: this.beaconCount,
    };
  }

  constructor(id: string, originLngLat: LngLat, onFps?: (fps: number) => void) {
    this.id = id;
    this.origin = MercatorCoordinate.fromLngLat({ lng: originLngLat[0], lat: originLngLat[1] }, 0);
    this.onFps = onFps;
    this.scene.add(this.hazardGroup, this.beaconGroup);
    this.scene.add(new THREE.AmbientLight(0xffffff, 0.9));
    const sun = new THREE.DirectionalLight(0xffffff, 1.2);
    sun.position.set(300, -600, 900);
    this.scene.add(sun);
  }

  setOrigin(lngLat: LngLat): void {
    this.origin = MercatorCoordinate.fromLngLat({ lng: lngLat[0], lat: lngLat[1] }, 0);
    this.scale = this.origin.meterInMercatorCoordinateUnits();
    this.hazardKey = this.routeKey = this.zoneKey = "";
    this.rebuild();
  }

  update(data: ThreeLayerData): void {
    this.pending = data;
    this.rebuild();
  }

  onAdd(map: MaplibreMap, gl: WebGLRenderingContext | WebGL2RenderingContext): void {
    this.map = map;
    this.scale = this.origin.meterInMercatorCoordinateUnits();
    this.renderer = new THREE.WebGLRenderer({
      canvas: map.getCanvas(),
      context: gl,
      antialias: true,
    });
    this.renderer.autoClear = false;
    this.rebuild();
  }

  onRemove(): void {
    this.scene.clear();
    this.renderer?.dispose();
    this.renderer = null;
    this.map = null;
  }

  render(_gl: WebGLRenderingContext | WebGL2RenderingContext, matrix: ArrayLike<number>): void {
    if (!this.renderer || !this.map) return;
    const now = performance.now();
    this.trackFps(now);
    if (this.enabled) {
      this.animate(now);
      const m = new THREE.Matrix4().fromArray(Array.from(matrix));
      const l = new THREE.Matrix4()
        .makeTranslation(this.origin.x, this.origin.y, this.origin.z)
        .scale(new THREE.Vector3(this.scale, -this.scale, this.scale));
      this.camera.projectionMatrix = m.multiply(l);
      this.renderer.resetState();
      this.renderer.render(this.scene, this.camera);
    }
    this.map.triggerRepaint();
  }

  // ---------------------------------------------------------------- internals

  private toLocal(ll: LngLat, z = 0): THREE.Vector3 {
    const p = MercatorCoordinate.fromLngLat({ lng: ll[0], lat: ll[1] }, 0);
    return new THREE.Vector3(
      (p.x - this.origin.x) / this.scale,
      (this.origin.y - p.y) / this.scale,
      z,
    );
  }

  private rebuild(): void {
    const { hazards, routes, safeZones } = this.pending;
    const active = hazards.filter((h) => (h.status ?? "active") !== "resolved");
    const hk = active.map((h) => `${h.id}:${h.last_seen}:${h.status ?? ""}`).join("|");
    if (hk !== this.hazardKey) {
      this.hazardKey = hk;
      this.buildHazards(active);
    }
    const rk = routes.map((r) => `${r.id}:${r.version}`).join("|");
    if (rk !== this.routeKey) {
      this.routeKey = rk;
      this.buildParticles(routes);
    }
    const zk = safeZones.map((z) => `${z.id}:${z.current_load ?? 0}`).join("|");
    if (zk !== this.zoneKey) {
      this.zoneKey = zk;
      this.buildBeacons(safeZones);
    }
  }

  private buildHazards(hazards: Hazard[]): void {
    disposeGroup(this.hazardGroup);
    for (const h of hazards) {
      const color = new THREE.Color(hazardHex(h.hazard_class));
      const disputed = h.status === "disputed";
      const mat = new THREE.MeshStandardMaterial({
        color,
        transparent: true,
        opacity: disputed ? 0.12 : 0.28,
        depthWrite: false,
        side: THREE.DoubleSide,
        emissive: color,
        emissiveIntensity: 0.25,
      });
      const height = 40 + 140 * h.severity * h.confidence;
      for (const ring of outerRings(h.geom)) {
        const pts = ring.map((c) => this.toLocal(c));
        if (pts.length < 3) continue;
        const shape = new THREE.Shape(pts.map((p) => new THREE.Vector2(p.x, p.y)));
        const geom = new THREE.ExtrudeGeometry(shape, { depth: height, bevelEnabled: false });
        const mesh = new THREE.Mesh(geom, mat);
        this.hazardGroup.add(mesh);
      }
    }
  }

  private buildParticles(routes: Route[]): void {
    if (this.particles) {
      this.scene.remove(this.particles);
      this.particles.geometry.dispose();
      (this.particles.material as THREE.Material).dispose();
      this.particles = null;
    }
    this.tracks = [];
    let total = 0;
    for (const r of routes) {
      const pts = lineCoords(r.path).map((c) => this.toLocal(c, 6));
      if (pts.length < 2) continue;
      const cum = [0];
      for (let i = 1; i < pts.length; i += 1) {
        const a = pts[i - 1];
        const b = pts[i];
        cum.push((cum[i - 1] ?? 0) + (a && b ? a.distanceTo(b) : 0));
      }
      const len = cum[cum.length - 1] ?? 0;
      const count = Math.max(12, Math.min(900, Math.round(r.group_size / 6)));
      this.tracks.push({ points: pts, cum, total: len, count });
      total += count;
    }
    total = Math.min(total, MAX_PARTICLES);
    if (total === 0) return;
    const geom = new THREE.SphereGeometry(3.2, 6, 5);
    const mat = new THREE.MeshBasicMaterial({ color: 0x36c4ff, transparent: true, opacity: 0.95 });
    const mesh = new THREE.InstancedMesh(geom, mat, total);
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    this.particleTrack = new Uint16Array(total);
    this.particlePhase = new Float32Array(total);
    let idx = 0;
    for (let t = 0; t < this.tracks.length && idx < total; t += 1) {
      const track = this.tracks[t];
      if (!track) continue;
      for (let k = 0; k < track.count && idx < total; k += 1) {
        this.particleTrack[idx] = t;
        this.particlePhase[idx] = Math.random();
        idx += 1;
      }
    }
    this.particles = mesh;
    this.scene.add(mesh);
  }

  private buildBeacons(zones: SafeZone[]): void {
    if (this.beacons) {
      this.beaconGroup.remove(this.beacons);
      this.beacons.geometry.dispose();
      (this.beacons.material as THREE.Material).dispose();
      this.beacons = null;
    }
    this.beaconCount = zones.length;
    if (zones.length === 0) return;
    const geom = new THREE.TorusGeometry(30, 3, 8, 48);
    const mat = new THREE.MeshBasicMaterial({ color: 0x2fd27f, transparent: true, opacity: 0.8 });
    const mesh = new THREE.InstancedMesh(geom, mat, zones.length);
    zones.forEach((z, i) => {
      const c = this.toLocal(safeZoneCenter(z), 4);
      this.tmpObj.position.copy(c);
      this.tmpObj.rotation.set(0, 0, 0);
      this.tmpObj.scale.set(1, 1, 1);
      this.tmpObj.updateMatrix();
      mesh.setMatrixAt(i, this.tmpObj.matrix);
    });
    this.beacons = mesh;
    this.beaconGroup.add(mesh);
  }

  private animate(now: number): void {
    const t = now / 1000;
    if (this.particles) {
      const n = this.particles.count;
      for (let i = 0; i < n; i += 1) {
        const track = this.tracks[this.particleTrack[i] ?? 0];
        if (!track || track.total <= 0) continue;
        const phase = this.particlePhase[i] ?? 0;
        const d =
          (((phase * track.total + t * PARTICLE_SPEED_MPS) % track.total) + track.total) %
          track.total;
        const pos = sampleTrack(track, d);
        this.tmpObj.position.copy(pos);
        const s = 0.8 + 0.4 * Math.sin(t * 6 + phase * 20);
        this.tmpObj.scale.set(s, s, s);
        this.tmpObj.rotation.set(0, 0, 0);
        this.tmpObj.updateMatrix();
        this.particles.setMatrixAt(i, this.tmpObj.matrix);
      }
      this.particles.instanceMatrix.needsUpdate = true;
    }
    if (this.beacons) {
      const pulse = 1 + 0.9 * ((Math.sin(t * 2.2) + 1) / 2);
      const mat = this.beacons.material as THREE.MeshBasicMaterial;
      mat.opacity = 0.9 - 0.6 * ((pulse - 1) / 0.9);
      for (let i = 0; i < this.beaconCount; i += 1) {
        this.beacons.getMatrixAt(i, this.tmpObj.matrix);
        this.tmpObj.matrix.decompose(
          this.tmpObj.position,
          this.tmpObj.quaternion,
          this.tmpObj.scale,
        );
        this.tmpObj.scale.set(pulse, pulse, 1);
        this.tmpObj.updateMatrix();
        this.beacons.setMatrixAt(i, this.tmpObj.matrix);
      }
      this.beacons.instanceMatrix.needsUpdate = true;
    }
  }

  private trackFps(now: number): void {
    this.frameTimes.push(now);
    while (this.frameTimes.length > 0 && now - (this.frameTimes[0] ?? now) > 1000)
      this.frameTimes.shift();
    if (now - this.lastFpsReport > 500 && this.frameTimes.length > 1) {
      this.lastFpsReport = now;
      const first = this.frameTimes[0] ?? now;
      const span = (now - first) / 1000;
      if (span > 0) this.onFps?.((this.frameTimes.length - 1) / span);
    }
  }
}

function sampleTrack(track: RouteTrack, d: number): THREE.Vector3 {
  const { points, cum } = track;
  let lo = 0;
  let hi = cum.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if ((cum[mid] ?? 0) <= d) lo = mid;
    else hi = mid;
  }
  const a = points[lo];
  const b = points[hi];
  const ca = cum[lo] ?? 0;
  const cb = cum[hi] ?? ca;
  if (!a || !b) return points[0] ?? new THREE.Vector3();
  const f = cb > ca ? (d - ca) / (cb - ca) : 0;
  return new THREE.Vector3().lerpVectors(a, b, Math.max(0, Math.min(1, f)));
}

function disposeGroup(group: THREE.Group): void {
  for (const child of [...group.children]) {
    group.remove(child);
    if (child instanceof THREE.Mesh) {
      const mesh = child as THREE.Mesh;
      mesh.geometry.dispose();
      const m = mesh.material;
      if (Array.isArray(m))
        m.forEach((x) => {
          x.dispose();
        });
      else m.dispose();
    }
  }
}
