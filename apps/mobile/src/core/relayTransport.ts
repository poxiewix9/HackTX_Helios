/**
 * WebSocketRelayTransport — the DEV transport. Phones (and the dashboard) join a room on
 * a tiny relay (`scripts/relay-server.mjs`); the relay forwards binary frames and keeps
 * everyone's peer list current. It is not a mesh: every member sees every other member
 * as a one-hop peer. It exists so two phones on the same Wi-Fi (or a phone and the
 * dashboard's simulated mesh) can exercise the real CRDT path without BLE.
 *
 * Relay wire format (JSON text for control, binary for data):
 *   client → relay : {"t":"hello","room":"…","id":"…"}
 *   relay  → client: {"t":"peers","peers":["…"]}
 *   client → relay : binary  [ 1 byte toLen ][ to utf8 ][ payload ]   (toLen 0 = broadcast)
 *   relay  → client: binary  [ 1 byte fromLen ][ from utf8 ][ payload ]
 *
 * The WebSocket constructor is injectable so node tests use `ws` and the app uses the
 * platform global. Reconnects with exponential backoff.
 */
import { TransportEvents } from "./transport";
import type { PeerId, Transport, TransportInfo } from "./transport";

export interface WebSocketLike {
  binaryType: string;
  readyState: number;
  onopen: ((ev: unknown) => void) | null;
  onclose: ((ev: unknown) => void) | null;
  onerror: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  send(data: string | ArrayBufferLike | Uint8Array): void;
  close(): void;
}

export type WebSocketFactory = (url: string) => WebSocketLike;

export interface RelayOptions {
  url: string;
  room: string;
  id: PeerId;
  factory?: WebSocketFactory;
  baseDelayMs?: number;
  maxDelayMs?: number;
  setTimeoutFn?: (fn: () => void, ms: number) => unknown;
  clearTimeoutFn?: (h: unknown) => void;
}

export type RelayState = "connecting" | "open" | "reconnecting" | "closed";

const enc = new TextEncoder();
const dec = new TextDecoder();

export function encodeRelayFrame(to: PeerId | null, payload: Uint8Array): Uint8Array {
  const toBytes = to === null ? new Uint8Array(0) : enc.encode(to);
  if (toBytes.length > 255) throw new Error("peer id too long");
  const out = new Uint8Array(1 + toBytes.length + payload.length);
  out[0] = toBytes.length;
  out.set(toBytes, 1);
  out.set(payload, 1 + toBytes.length);
  return out;
}

export function decodeRelayFrame(
  data: Uint8Array,
): { peer: PeerId | null; payload: Uint8Array } | null {
  if (data.length < 1) return null;
  const n = data[0] ?? 0;
  if (data.length < 1 + n) return null;
  const peer = n === 0 ? null : dec.decode(data.subarray(1, 1 + n));
  return { peer, payload: data.subarray(1 + n) };
}

function defaultFactory(url: string): WebSocketLike {
  const Ctor = (globalThis as { WebSocket?: new (u: string) => WebSocketLike }).WebSocket;
  if (!Ctor) throw new Error("no WebSocket implementation available; pass `factory`");
  return new Ctor(url);
}

export class WebSocketRelayTransport implements Transport {
  readonly id: PeerId;
  readonly info: TransportInfo = { kind: "ws-relay", simulated: false };
  state: RelayState = "closed";
  attempt = 0;
  private readonly events = new TransportEvents();
  private readonly opts: Required<RelayOptions>;
  private ws: WebSocketLike | null = null;
  private peerList: PeerId[] = [];
  private timer: unknown = null;
  private running = false;
  private readonly stateListeners = new Set<(s: RelayState) => void>();

  constructor(opts: RelayOptions) {
    this.id = opts.id;
    this.opts = {
      url: opts.url,
      room: opts.room,
      id: opts.id,
      factory: opts.factory ?? defaultFactory,
      baseDelayMs: opts.baseDelayMs ?? 500,
      maxDelayMs: opts.maxDelayMs ?? 10_000,
      setTimeoutFn: opts.setTimeoutFn ?? ((fn, ms) => setTimeout(fn, ms)),
      clearTimeoutFn:
        opts.clearTimeoutFn ??
        ((h) => {
          clearTimeout(h as ReturnType<typeof setTimeout>);
        }),
    };
  }

  onState(l: (s: RelayState) => void): () => void {
    this.stateListeners.add(l);
    return () => this.stateListeners.delete(l);
  }

  private setState(s: RelayState): void {
    this.state = s;
    for (const l of this.stateListeners) l(s);
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    this.connect();
  }

  stop(): void {
    this.running = false;
    if (this.timer !== null) this.opts.clearTimeoutFn(this.timer);
    this.timer = null;
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onclose = null;
      ws.close();
    }
    this.peerList = [];
    this.setState("closed");
    this.events.emitPeers([]);
  }

  peers(): PeerId[] {
    return this.state === "open" ? [...this.peerList] : [];
  }

  send(to: PeerId, data: Uint8Array): void {
    this.rawSend(encodeRelayFrame(to, data));
  }

  broadcast(data: Uint8Array): void {
    if (this.peerList.length === 0) return;
    this.rawSend(encodeRelayFrame(null, data));
  }

  onMessage(h: (from: PeerId, data: Uint8Array) => void): () => void {
    return this.events.onMessage(h);
  }

  onPeers(h: (peers: PeerId[]) => void): () => void {
    return this.events.onPeers(h);
  }

  private rawSend(frame: Uint8Array): void {
    if (!this.ws || this.state !== "open") return; // dropped; sync repairs on gossip
    this.ws.send(frame);
  }

  private connect(): void {
    this.setState(this.attempt === 0 ? "connecting" : "reconnecting");
    let ws: WebSocketLike;
    try {
      ws = this.opts.factory(this.opts.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => {
      this.attempt = 0;
      ws.send(JSON.stringify({ t: "hello", room: this.opts.room, id: this.id }));
      this.setState("open");
    };
    ws.onmessage = (ev) => {
      this.handle(ev.data);
    };
    ws.onerror = () => {
      /* onclose follows */
    };
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.peerList = [];
      this.events.emitPeers([]);
      if (this.running) this.scheduleReconnect();
      else this.setState("closed");
    };
  }

  private scheduleReconnect(): void {
    this.attempt += 1;
    this.setState("reconnecting");
    const delay = Math.min(this.opts.maxDelayMs, this.opts.baseDelayMs * 2 ** (this.attempt - 1));
    this.timer = this.opts.setTimeoutFn(() => {
      this.timer = null;
      if (this.running) this.connect();
    }, delay);
  }

  private handle(data: unknown): void {
    if (typeof data === "string") {
      let msg: unknown;
      try {
        msg = JSON.parse(data);
      } catch {
        return;
      }
      if (typeof msg === "object" && msg !== null && (msg as { t?: unknown }).t === "peers") {
        const peers = (msg as { peers?: unknown }).peers;
        if (Array.isArray(peers)) {
          this.peerList = peers.filter((p): p is string => typeof p === "string" && p !== this.id);
          this.events.emitPeers(this.peers());
        }
      }
      return;
    }
    let bytes: Uint8Array | null = null;
    if (data instanceof ArrayBuffer) bytes = new Uint8Array(data);
    else if (ArrayBuffer.isView(data))
      bytes = new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
    if (!bytes) return;
    const f = decodeRelayFrame(bytes);
    if (f?.peer) this.events.emitMessage(f.peer, f.payload);
  }
}
