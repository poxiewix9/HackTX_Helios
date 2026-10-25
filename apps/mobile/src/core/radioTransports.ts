/**
 * BEST-EFFORT radio transports. Both implement `Transport` over a tiny `RadioAdapter`
 * so the sync layer is identical to the simulated / relay paths; what differs is whether
 * the platform can supply an adapter at all:
 *
 *  - BLE: `react-native-ble-plx` is *central-only* — a phone can scan and connect but
 *    cannot advertise a GATT service, so two stock phones cannot discover each other
 *    with it. A real BLE mesh needs a peripheral-mode native module (and on iOS,
 *    background advertising is heavily restricted: no custom service UUIDs in the
 *    advertisement while backgrounded, overflow area only). Expo Go has no BLE at all;
 *    a dev build is required.
 *  - Wi-Fi Direct: Android only (`WifiP2pManager`); iOS does not expose Wi-Fi Direct,
 *    the closest thing is Multipeer Connectivity, which is a different API.
 *
 * Therefore: without an adapter these transports report `available=false` with the
 * reason, and `start()` is a no-op. The demo runs on `SimulatedMeshTransport` (labelled
 * SIMULATED) and the dev relay. See `apps/mobile/README.md`.
 */
import { TransportEvents } from "./transport";
import type { PeerId, Transport, TransportInfo } from "./transport";

/** What a native module has to provide for a radio transport to work. */
export interface RadioAdapter {
  /** Start advertising + scanning; `onPeer(id, reachable)` as peers come and go. */
  start(
    onPeer: (id: PeerId, reachable: boolean) => void,
    onData: (from: PeerId, data: Uint8Array) => void,
  ): Promise<void>;
  stop(): Promise<void>;
  /** Write bytes to a connected peer (MTU chunking is the adapter's job). */
  write(to: PeerId, data: Uint8Array): Promise<void>;
}

export interface Availability {
  available: boolean;
  reason: string | null;
}

abstract class AdapterTransport implements Transport {
  abstract readonly info: TransportInfo;
  private readonly events = new TransportEvents();
  private readonly reachable = new Set<PeerId>();
  private running = false;

  constructor(
    readonly id: PeerId,
    protected readonly adapter: RadioAdapter | null,
    private readonly unavailableReason: string,
  ) {}

  availability(): Availability {
    return this.adapter
      ? { available: true, reason: null }
      : { available: false, reason: this.unavailableReason };
  }

  async start(): Promise<void> {
    if (!this.adapter || this.running) return;
    this.running = true;
    await this.adapter.start(
      (peer, up) => {
        if (up) this.reachable.add(peer);
        else this.reachable.delete(peer);
        this.events.emitPeers(this.peers());
      },
      (from, data) => {
        this.events.emitMessage(from, data);
      },
    );
  }

  async stop(): Promise<void> {
    if (!this.adapter || !this.running) return;
    this.running = false;
    await this.adapter.stop();
    this.reachable.clear();
    this.events.emitPeers([]);
  }

  peers(): PeerId[] {
    return this.running ? [...this.reachable] : [];
  }

  send(to: PeerId, data: Uint8Array): void {
    if (!this.adapter || !this.running || !this.reachable.has(to)) return;
    void this.adapter.write(to, data).catch(() => {
      /* dropped; gossip repairs */
    });
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
}

export class BleTransport extends AdapterTransport {
  readonly info: TransportInfo = { kind: "ble", simulated: false };
  constructor(id: PeerId, adapter: RadioAdapter | null = null) {
    super(
      id,
      adapter,
      "BLE mesh needs a peripheral-mode native module (react-native-ble-plx is central-only) and a dev build; not available in Expo Go.",
    );
  }
}

export class WifiDirectTransport extends AdapterTransport {
  readonly info: TransportInfo = { kind: "wifi-direct", simulated: false };
  constructor(id: PeerId, adapter: RadioAdapter | null = null) {
    super(
      id,
      adapter,
      "Wi-Fi Direct is Android-only (WifiP2pManager via a native module); iOS has no Wi-Fi Direct API.",
    );
  }
}
