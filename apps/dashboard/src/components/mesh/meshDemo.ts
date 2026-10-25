/**
 * The dashboard's SIMULATED community mesh: a handful of virtual phones running the real
 * `@arco/mobile/core` stack (signed pins, Yjs CRDT, gossip sync) over
 * `SimulatedMeshTransport`. One device is the gateway: when the backhaul ("cellular") is
 * up it uploads pins to the real blackboard through `/api/bb/reports`, where Consensus
 * treats them exactly like pins from a physical phone. Optionally the gateway also joins
 * the dev relay so a real phone running `apps/mobile` participates in the same mesh.
 *
 * Framework-free so it can be unit-tested; `MeshPanel.tsx` renders it.
 */
import type { CommunityReport, HazardClass, Route } from "@arco/schemas";
import {
  MeshNode,
  SimulatedMeshNetwork,
  WebSocketRelayTransport,
  seededRandom,
} from "@arco/mobile/core";
import type { Connectivity, LngLat, SimulatedMeshTransport } from "@arco/mobile/core";

export interface DemoDeviceSpec {
  id: string;
  x: number;
  y: number;
  /** Device-level cellular capability (the gateway has it; most phones in the fire do not). */
  cellular: boolean;
  /** Where the device is in the world (pins are dropped here). */
  lngLat: LngLat;
}

export interface MeshDemoOptions {
  devices: DemoDeviceSpec[];
  gatewayId: string;
  blackboardUrl: string;
  rangeM?: number;
  loss?: number;
  latencyMs?: number;
  relay?: { url: string; room: string } | null;
  fetchFn?: typeof fetch;
  now?: () => number;
  scenarioT?: () => number | null;
  seed?: number;
}

export interface DeviceSnapshot {
  id: string;
  x: number;
  y: number;
  connectivity: Connectivity;
  peers: string[];
  pins: number;
  pendingUpload: number;
  isGateway: boolean;
  lngLat: LngLat;
}

export interface MeshSnapshot {
  devices: DeviceSnapshot[];
  links: { a: string; b: string; up: boolean }[];
  cellular: boolean;
  partitioned: boolean;
  loss: number;
  stats: { sent: number; delivered: number; dropped: number; unreachable: number };
  uploaded: number;
  relayState: string | null;
  relayPeers: string[];
  totalPins: number;
}

export class MeshDemo {
  readonly net: SimulatedMeshNetwork;
  readonly nodes = new Map<string, MeshNode>();
  readonly specs = new Map<string, DemoDeviceSpec>();
  readonly gatewayId: string;
  readonly relay: WebSocketRelayTransport | null;
  private readonly transports = new Map<string, SimulatedMeshTransport>();
  private readonly listeners = new Set<() => void>();
  private uploaded = 0;
  private flushing = false;

  constructor(opts: MeshDemoOptions) {
    this.gatewayId = opts.gatewayId;
    this.net = new SimulatedMeshNetwork({
      rangeM: opts.rangeM ?? 90,
      loss: opts.loss ?? 0.1,
      latencyMs: opts.latencyMs ?? 120,
      random: seededRandom(opts.seed ?? 7),
      now: opts.now ?? (() => Date.now()),
    });
    this.relay = opts.relay
      ? new WebSocketRelayTransport({
          url: opts.relay.url,
          room: opts.relay.room,
          id: `dashboard-${opts.gatewayId}`,
        })
      : null;
    for (const spec of opts.devices) {
      this.specs.set(spec.id, spec);
      const t = this.net.addDevice({ id: spec.id, x: spec.x, y: spec.y, cellular: spec.cellular });
      this.transports.set(spec.id, t);
      const isGateway = spec.id === opts.gatewayId;
      const node = new MeshNode({
        id: spec.id,
        transports: isGateway && this.relay ? [t, this.relay] : [t],
        sync: { gossipEveryMs: 1000, fanout: 1 },
        uplink: isGateway
          ? {
              blackboardUrl: opts.blackboardUrl,
              fetchFn: opts.fetchFn,
              scenarioT: opts.scenarioT,
            }
          : undefined,
      });
      node.position = spec.lngLat;
      this.nodes.set(spec.id, node);
    }
    this.net.onChange(() => {
      this.emit();
    });
  }

  start(): void {
    for (const t of this.transports.values()) t.start();
    this.relay?.start();
    for (const n of this.nodes.values()) {
      n.start();
      n.doc.onPins(() => {
        this.emit();
      });
    }
    this.refreshCellular();
  }

  stop(): void {
    for (const n of this.nodes.values()) n.stop();
    for (const t of this.transports.values()) t.stop();
    this.relay?.stop();
  }

  onChange(l: () => void): () => void {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  }

  private emit(): void {
    for (const l of this.listeners) l();
  }

  /** Deliver in-flight frames; call from a timer (panel) or by hand (tests). */
  tick(now?: number): void {
    this.net.flush(now);
  }

  /** Network-wide backhaul switch — the "offline" toggle in the panel. */
  setCellular(on: boolean): void {
    this.net.setCellular(on);
    this.refreshCellular();
  }

  private refreshCellular(): void {
    for (const n of this.nodes.values()) n.cellularUp = this.net.hasCellular(n.id);
    this.emit();
  }

  setPartition(on: boolean, group?: string[]): void {
    if (on) {
      const ids = group ?? [...this.nodes.keys()].slice(0, Math.ceil(this.nodes.size / 2));
      this.net.partitionDevices(ids);
    } else {
      this.net.heal();
    }
  }

  setLoss(loss: number): void {
    this.net.setLoss(loss);
  }

  dropPin(
    deviceId: string,
    hazardClass: HazardClass,
    note: string | null = null,
  ): CommunityReport | null {
    const n = this.nodes.get(deviceId);
    if (!n) return null;
    const r = n.dropPin(hazardClass, note);
    this.emit();
    return r;
  }

  /** Routes heard from the agents enter the mesh through the gateway. */
  ingestRoutes(routes: Route[]): void {
    this.nodes.get(this.gatewayId)?.ingestRoutes(routes);
  }

  /** Upload pending pins through the gateway if the backhaul is up. */
  async flushUplink(): Promise<number> {
    if (this.flushing) return 0;
    this.flushing = true;
    try {
      const gw = this.nodes.get(this.gatewayId);
      if (!gw) return 0;
      const n = await gw.flushUplink();
      if (n > 0) {
        this.uploaded += n;
        this.emit();
      }
      return n;
    } finally {
      this.flushing = false;
    }
  }

  snapshot(): MeshSnapshot {
    const devices: DeviceSnapshot[] = [];
    const seen = new Set<string>();
    for (const [id, n] of this.nodes) {
      const d = this.net.device(id);
      const spec = this.specs.get(id);
      if (!d || !spec) continue;
      for (const p of n.doc.pins()) seen.add(p.id);
      devices.push({
        id,
        x: d.x,
        y: d.y,
        connectivity: n.connectivity(),
        peers: n.sync.peers(),
        pins: n.doc.pins().length,
        pendingUpload: n.doc.pendingUpload().length,
        isGateway: id === this.gatewayId,
        lngLat: spec.lngLat,
      });
    }
    return {
      devices,
      links: this.net.links(),
      cellular: this.net.cellular,
      partitioned: this.net.isPartitioned(),
      loss: this.net.opts.loss,
      stats: { ...this.net.stats },
      uploaded: this.uploaded,
      relayState: this.relay?.state ?? null,
      relayPeers: this.relay?.peers() ?? [],
      totalPins: seen.size,
    };
  }
}

/**
 * Default layout: two clusters of phones on Front St / Lahainaluna Rd joined by a
 * bridge device, plus one gateway with cellular. Metres on a local plane for the radio
 * model; `lngLat` places pins on the real map (offsets from the scenario bbox centre
 * passed in by the panel, so no coordinates live in code).
 */
export function defaultDevices(center: LngLat): DemoDeviceSpec[] {
  const dLng = 0.0009; // ~90 m at 20.9°N
  const dLat = 0.00085;
  const at = (ex: number, ey: number): LngLat => [center[0] + ex * dLng, center[1] + ey * dLat];
  return [
    { id: "gateway", x: 0, y: 0, cellular: true, lngLat: at(0, 0) },
    { id: "phone-1", x: 70, y: 20, cellular: false, lngLat: at(0.8, 0.2) },
    { id: "phone-2", x: 130, y: -30, cellular: false, lngLat: at(1.4, -0.3) },
    { id: "phone-3", x: 190, y: 30, cellular: false, lngLat: at(2.1, 0.3) },
    { id: "bridge", x: 250, y: -10, cellular: false, lngLat: at(2.8, -0.1) },
    { id: "phone-4", x: 320, y: 20, cellular: false, lngLat: at(3.5, 0.2) },
    { id: "phone-5", x: 380, y: -40, cellular: false, lngLat: at(4.2, -0.4) },
  ];
}
