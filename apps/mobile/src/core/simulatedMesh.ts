/**
 * SIMULATED mesh transport (labelled as such everywhere it is shown).
 *
 * A `SimulatedMeshNetwork` holds the devices, their positions and the radio range, and
 * delivers frames between devices that are within range of each other, with configurable
 * packet loss, latency and a partition switch. Each device attaches a
 * `SimulatedMeshTransport`, which implements `Transport` and knows nothing about the
 * network's internals beyond the `deliver` callback — the same `MeshSync` that runs over
 * the WebSocket relay or BLE runs over this.
 *
 * Time is injectable (`now` / `schedule`) so tests run deterministically without timers.
 */
import { TransportEvents } from "./transport";
import type { PeerId, Transport, TransportInfo } from "./transport";

export interface SimulatedDevice {
  id: PeerId;
  /** Metres on a flat local plane (good enough for a few hundred metres). */
  x: number;
  y: number;
  /** Device-level cellular switch; the network's `cellular` flag ANDs with it. */
  cellular: boolean;
}

export interface SimulatedMeshOptions {
  rangeM: number;
  /** Probability [0,1] that a frame is lost on a link. */
  loss: number;
  /** One-way latency in ms; 0 delivers synchronously on the next `flush()`. */
  latencyMs: number;
  /** Deterministic RNG in [0,1). */
  random: () => number;
  now: () => number;
}

export interface Frame {
  from: PeerId;
  to: PeerId;
  data: Uint8Array;
  deliverAt: number;
}

export interface LinkState {
  a: PeerId;
  b: PeerId;
  /** false when out of range or separated by the partition. */
  up: boolean;
}

/** Small deterministic PRNG (mulberry32) so tests can reproduce a loss pattern. */
export function seededRandom(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export class SimulatedMeshNetwork {
  readonly opts: SimulatedMeshOptions;
  private readonly devices = new Map<PeerId, SimulatedDevice>();
  private readonly transports = new Map<PeerId, SimulatedMeshTransport>();
  private readonly queue: Frame[] = [];
  private partition: Set<PeerId> | null = null;
  /** Network-wide cellular backhaul (the dashboard "offline" toggle). */
  cellular = true;
  /** Counters for the dashboard mesh panel. */
  stats = { sent: 0, delivered: 0, dropped: 0, unreachable: 0 };
  private readonly listeners = new Set<() => void>();

  constructor(opts: Partial<SimulatedMeshOptions> = {}) {
    this.opts = {
      rangeM: opts.rangeM ?? 120,
      loss: opts.loss ?? 0,
      latencyMs: opts.latencyMs ?? 0,
      random: opts.random ?? seededRandom(1),
      now: opts.now ?? (() => Date.now()),
    };
  }

  /** Subscribe to topology / stats changes (the panel re-renders on this). */
  onChange(l: () => void): () => void {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  }

  private changed(): void {
    for (const l of this.listeners) l();
  }

  addDevice(d: SimulatedDevice): SimulatedMeshTransport {
    this.devices.set(d.id, { ...d });
    const t = new SimulatedMeshTransport(d.id, this);
    this.transports.set(d.id, t);
    this.notifyPeers();
    return t;
  }

  removeDevice(id: PeerId): void {
    this.devices.delete(id);
    this.transports.delete(id);
    this.notifyPeers();
  }

  device(id: PeerId): SimulatedDevice | undefined {
    return this.devices.get(id);
  }

  allDevices(): SimulatedDevice[] {
    return [...this.devices.values()];
  }

  moveDevice(id: PeerId, x: number, y: number): void {
    const d = this.devices.get(id);
    if (!d) return;
    d.x = x;
    d.y = y;
    this.notifyPeers();
  }

  setDeviceCellular(id: PeerId, on: boolean): void {
    const d = this.devices.get(id);
    if (!d) return;
    d.cellular = on;
    this.changed();
  }

  setCellular(on: boolean): void {
    this.cellular = on;
    this.changed();
  }

  /** True when this device can reach the base station / blackboard. */
  hasCellular(id: PeerId): boolean {
    return this.cellular && (this.devices.get(id)?.cellular ?? false);
  }

  /** Split the mesh: devices in `group` can only talk to each other, the rest likewise. */
  partitionDevices(group: PeerId[]): void {
    this.partition = new Set(group);
    this.notifyPeers();
  }

  heal(): void {
    this.partition = null;
    this.notifyPeers();
  }

  isPartitioned(): boolean {
    return this.partition !== null;
  }

  setLoss(loss: number): void {
    this.opts.loss = Math.min(1, Math.max(0, loss));
    this.changed();
  }

  linkUp(a: PeerId, b: PeerId): boolean {
    if (a === b) return false;
    const da = this.devices.get(a);
    const db = this.devices.get(b);
    if (!da || !db) return false;
    if (this.partition && this.partition.has(a) !== this.partition.has(b)) return false;
    return Math.hypot(da.x - db.x, da.y - db.y) <= this.opts.rangeM;
  }

  links(): LinkState[] {
    const ids = [...this.devices.keys()];
    const out: LinkState[] = [];
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) {
        const a = ids[i];
        const b = ids[j];
        if (a === undefined || b === undefined) continue;
        const da = this.devices.get(a);
        const db = this.devices.get(b);
        if (!da || !db) continue;
        const inRange = Math.hypot(da.x - db.x, da.y - db.y) <= this.opts.rangeM;
        if (inRange) out.push({ a, b, up: this.linkUp(a, b) });
      }
    }
    return out;
  }

  neighbours(id: PeerId): PeerId[] {
    return [...this.devices.keys()].filter((o) => this.linkUp(id, o));
  }

  /** Called by transports. */
  enqueue(from: PeerId, to: PeerId, data: Uint8Array): void {
    this.stats.sent += 1;
    if (!this.linkUp(from, to)) {
      this.stats.unreachable += 1;
      return;
    }
    if (this.opts.loss > 0 && this.opts.random() < this.opts.loss) {
      this.stats.dropped += 1;
      return;
    }
    this.queue.push({ from, to, data, deliverAt: this.opts.now() + this.opts.latencyMs });
  }

  /** Deliver every frame whose time has come. Returns the number delivered. */
  flush(now: number = this.opts.now()): number {
    let n = 0;
    // Deliveries can enqueue more frames (sync replies); loop until quiescent for `now`.
    for (;;) {
      const ready = this.queue.filter((f) => f.deliverAt <= now);
      if (ready.length === 0) break;
      for (const f of ready) this.queue.splice(this.queue.indexOf(f), 1);
      for (const f of ready) {
        // Re-check the link at delivery time: a partition that happened in flight drops it.
        if (!this.linkUp(f.from, f.to)) {
          this.stats.unreachable += 1;
          continue;
        }
        this.transports.get(f.to)?.receive(f.from, f.data);
        this.stats.delivered += 1;
        n += 1;
      }
    }
    if (n > 0) this.changed();
    return n;
  }

  pending(): number {
    return this.queue.length;
  }

  private notifyPeers(): void {
    for (const t of this.transports.values()) t.peersChanged();
    this.changed();
  }
}

export class SimulatedMeshTransport implements Transport {
  readonly info: TransportInfo = { kind: "simulated-mesh", simulated: true };
  private readonly events = new TransportEvents();
  private running = false;

  constructor(
    readonly id: PeerId,
    private readonly net: SimulatedMeshNetwork,
  ) {}

  start(): void {
    this.running = true;
    this.events.emitPeers(this.peers());
  }

  stop(): void {
    this.running = false;
    this.events.emitPeers([]);
  }

  peers(): PeerId[] {
    return this.running ? this.net.neighbours(this.id) : [];
  }

  send(to: PeerId, data: Uint8Array): void {
    if (!this.running) return;
    this.net.enqueue(this.id, to, data);
  }

  broadcast(data: Uint8Array): void {
    for (const p of this.peers()) this.send(p, data);
  }

  onMessage(h: (from: PeerId, data: Uint8Array) => void): () => void {
    return this.events.onMessage(h);
  }

  onPeers(h: (peers: PeerId[]) => void): () => void {
    return this.events.onPeers(h);
  }

  /** @internal network → transport */
  receive(from: PeerId, data: Uint8Array): void {
    if (this.running) this.events.emitMessage(from, data);
  }

  /** @internal */
  peersChanged(): void {
    if (this.running) this.events.emitPeers(this.peers());
  }
}
