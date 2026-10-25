/**
 * `Transport` is the ONLY seam between the CRDT sync layer and the network. Everything
 * above it (MeshDoc, MeshSync, the UI) speaks bytes to peer ids and never knows whether
 * the bytes travel over BLE, Wi-Fi Direct, a dev relay or an in-process simulation.
 */

export type PeerId = string;

/** What the device can currently reach. Shown in the UI as cell | mesh | isolated. */
export type Connectivity = "cell" | "mesh" | "isolated";

export interface TransportInfo {
  /** Human label, e.g. "simulated-mesh", "ws-relay", "ble", "wifi-direct". */
  kind: string;
  /** True for transports that only exist for demos/tests. Surfaced in the UI. */
  simulated: boolean;
}

export type MessageHandler = (from: PeerId, data: Uint8Array) => void;
export type PeersHandler = (peers: PeerId[]) => void;

export interface Transport {
  readonly id: PeerId;
  readonly info: TransportInfo;
  start(): Promise<void> | void;
  stop(): Promise<void> | void;
  /** Currently reachable peers (one hop for mesh transports, room members for a relay). */
  peers(): PeerId[];
  /** Send to one peer; silently dropped if unreachable (the sync layer tolerates loss). */
  send(to: PeerId, data: Uint8Array): void;
  /** Send to every reachable peer. */
  broadcast(data: Uint8Array): void;
  onMessage(handler: MessageHandler): () => void;
  onPeers(handler: PeersHandler): () => void;
}

/** Shared listener plumbing for transport implementations. */
export class TransportEvents {
  private readonly messageHandlers = new Set<MessageHandler>();
  private readonly peerHandlers = new Set<PeersHandler>();

  onMessage(h: MessageHandler): () => void {
    this.messageHandlers.add(h);
    return () => this.messageHandlers.delete(h);
  }

  onPeers(h: PeersHandler): () => void {
    this.peerHandlers.add(h);
    return () => this.peerHandlers.delete(h);
  }

  emitMessage(from: PeerId, data: Uint8Array): void {
    for (const h of this.messageHandlers) h(from, data);
  }

  emitPeers(peers: PeerId[]): void {
    for (const h of this.peerHandlers) h(peers);
  }
}

/**
 * Connectivity from the device's point of view: a base station / blackboard reachable
 * over cellular or Wi-Fi counts as `cell`; otherwise peers over any transport = `mesh`;
 * nothing at all = `isolated`.
 */
export function connectivityOf(cellularUp: boolean, peerCount: number): Connectivity {
  if (cellularUp) return "cell";
  return peerCount > 0 ? "mesh" : "isolated";
}
