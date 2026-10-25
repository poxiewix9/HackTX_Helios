/**
 * MeshSync — binds one `MeshDoc` to one or more `Transport`s.
 *
 * Wire protocol (1 tag byte + payload):
 *   0x01 SV      my Yjs state vector            ("what do I have?")
 *   0x02 UPDATE  a Yjs update                   (diff for your SV, or a fresh local change)
 *
 * Loss tolerance comes from anti-entropy rather than acks: every `gossipEveryMs` each
 * device sends its state vector to one random neighbour (or all, when `fanout` is 0);
 * the neighbour answers with exactly the missing part. A lost UPDATE is therefore repaired
 * on the next gossip round, and a device that joins late catches up on its first round.
 * `Transport` only has to deliver *some* frames *sometimes*.
 *
 * A device may run MeshSync over several transports at once (e.g. simulated mesh + WS
 * relay); such a device acts as a bridge because the CRDT merges whatever arrives.
 */
import type { MeshDoc } from "./crdt";
import type { PeerId, Transport } from "./transport";

export const TAG_SV = 0x01;
export const TAG_UPDATE = 0x02;

export function encodeFrame(tag: number, payload: Uint8Array): Uint8Array {
  const out = new Uint8Array(payload.length + 1);
  out[0] = tag;
  out.set(payload, 1);
  return out;
}

export function decodeFrame(data: Uint8Array): { tag: number; payload: Uint8Array } | null {
  if (data.length < 1) return null;
  return { tag: data[0] ?? 0, payload: data.subarray(1) };
}

export interface MeshSyncOptions {
  gossipEveryMs: number;
  /** Peers per gossip round; 0 = every peer. */
  fanout: number;
  random: () => number;
  setInterval: (fn: () => void, ms: number) => unknown;
  clearInterval: (h: unknown) => void;
}

export interface SyncStats {
  framesIn: number;
  framesOut: number;
  updatesApplied: number;
  bytesIn: number;
  bytesOut: number;
}

export class MeshSync {
  readonly stats: SyncStats = {
    framesIn: 0,
    framesOut: 0,
    updatesApplied: 0,
    bytesIn: 0,
    bytesOut: 0,
  };
  private readonly opts: MeshSyncOptions;
  private readonly transports: Transport[] = [];
  private readonly unsubs: (() => void)[] = [];
  private timer: unknown = null;
  private running = false;

  constructor(
    readonly doc: MeshDoc,
    opts: Partial<MeshSyncOptions> = {},
  ) {
    this.opts = {
      gossipEveryMs: opts.gossipEveryMs ?? 1500,
      fanout: opts.fanout ?? 1,
      random: opts.random ?? Math.random,
      setInterval: opts.setInterval ?? ((fn, ms) => setInterval(fn, ms)),
      clearInterval:
        opts.clearInterval ??
        ((h) => {
          clearInterval(h as ReturnType<typeof setInterval>);
        }),
    };
  }

  addTransport(t: Transport): void {
    this.transports.push(t);
    this.unsubs.push(
      t.onMessage((from, data) => {
        this.receive(t, from, data);
      }),
      t.onPeers(() => {
        // Topology changed (new neighbour, partition healed): announce immediately.
        if (this.running) this.announce(t);
      }),
    );
  }

  peers(): PeerId[] {
    const all = new Set<PeerId>();
    for (const t of this.transports) for (const p of t.peers()) all.add(p);
    return [...all];
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    this.unsubs.push(
      this.doc.onLocalUpdate((update) => {
        this.broadcastAll(encodeFrame(TAG_UPDATE, update));
      }),
    );
    for (const t of this.transports) this.announce(t);
    if (this.opts.gossipEveryMs > 0) {
      this.timer = this.opts.setInterval(() => {
        this.gossip();
      }, this.opts.gossipEveryMs);
    }
  }

  stop(): void {
    this.running = false;
    if (this.timer !== null) this.opts.clearInterval(this.timer);
    this.timer = null;
    for (const u of this.unsubs) u();
    this.unsubs.length = 0;
  }

  /** One anti-entropy round: send my state vector to `fanout` random peers per transport. */
  gossip(): void {
    if (!this.running) return;
    const sv = encodeFrame(TAG_SV, this.doc.stateVector());
    for (const t of this.transports) {
      const peers = t.peers();
      if (peers.length === 0) continue;
      if (this.opts.fanout <= 0 || this.opts.fanout >= peers.length) {
        for (const p of peers) this.sendTo(t, p, sv);
        continue;
      }
      const chosen = new Set<PeerId>();
      while (chosen.size < this.opts.fanout) {
        const p = peers[Math.floor(this.opts.random() * peers.length)];
        if (p !== undefined) chosen.add(p);
      }
      for (const p of chosen) this.sendTo(t, p, sv);
    }
  }

  private announce(t: Transport): void {
    const sv = encodeFrame(TAG_SV, this.doc.stateVector());
    for (const p of t.peers()) this.sendTo(t, p, sv);
  }

  private broadcastAll(frame: Uint8Array): void {
    for (const t of this.transports) {
      for (const p of t.peers()) this.sendTo(t, p, frame);
    }
  }

  private sendTo(t: Transport, to: PeerId, frame: Uint8Array): void {
    this.stats.framesOut += 1;
    this.stats.bytesOut += frame.length;
    t.send(to, frame);
  }

  private receive(t: Transport, from: PeerId, data: Uint8Array): void {
    if (!this.running) return;
    this.stats.framesIn += 1;
    this.stats.bytesIn += data.length;
    const f = decodeFrame(data);
    if (!f) return;
    if (f.tag === TAG_SV) {
      const diff = this.doc.diffFor(f.payload);
      // An "empty" Yjs update is 2 bytes; skip it to keep gossip quiet when in sync.
      if (diff.length > 2) this.sendTo(t, from, encodeFrame(TAG_UPDATE, diff));
    } else if (f.tag === TAG_UPDATE) {
      const before = this.doc.stateVector();
      this.doc.applyUpdate(f.payload, `peer:${from}`);
      this.stats.updatesApplied += 1;
      // Multi-hop flooding: forward only if this frame taught us something, which is
      // what terminates the flood (a second copy changes nothing and is not re-sent).
      if (!bytesEqual(before, this.doc.stateVector())) {
        for (const tr of this.transports) {
          for (const p of tr.peers()) {
            if (p !== from) this.sendTo(tr, p, data);
          }
        }
      }
    }
  }
}

export function bytesEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}
