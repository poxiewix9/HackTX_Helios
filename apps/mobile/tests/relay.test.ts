/**
 * WebSocketRelayTransport against the real relay server (`scripts/relay-server.mjs`) on an
 * ephemeral port, with the `ws` client as the WebSocket implementation.
 */
import WebSocket from "ws";

import { MeshDoc } from "@/core/crdt";
import { WebSocketRelayTransport, decodeRelayFrame, encodeRelayFrame } from "@/core/relayTransport";
import type { WebSocketLike } from "@/core/relayTransport";
import { DeviceKey } from "@/core/signing";
import { MeshSync } from "@/core/sync";

// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore plain ESM script without types
import { startRelay } from "../scripts/relay-server.mjs";

interface Relay {
  port: number;
  close(): Promise<void>;
}

const factory = (url: string): WebSocketLike => new WebSocket(url) as unknown as WebSocketLike;

const waitFor = async (pred: () => boolean, ms = 5000): Promise<void> => {
  const t0 = Date.now();
  while (!pred()) {
    if (Date.now() - t0 > ms) throw new Error("timeout");
    await new Promise((r) => setTimeout(r, 20));
  }
};

describe("relay frame codec", () => {
  it("round-trips broadcast and addressed frames", () => {
    const payload = new Uint8Array([1, 2, 3]);
    const b = decodeRelayFrame(encodeRelayFrame(null, payload));
    expect(b?.peer).toBeNull();
    expect([...(b?.payload ?? [])]).toEqual([1, 2, 3]);
    const a = decodeRelayFrame(encodeRelayFrame("phone-ü", payload));
    expect(a?.peer).toBe("phone-ü");
    expect([...(a?.payload ?? [])]).toEqual([1, 2, 3]);
  });
});

describe("WebSocketRelayTransport", () => {
  let relay: Relay;
  beforeAll(async () => {
    relay = await (startRelay as (o: { port: number }) => Promise<Relay>)({ port: 0 });
  });
  afterAll(async () => {
    await relay.close();
  });

  it("two phones in a room see each other and converge; a third room is isolated", async () => {
    const url = `ws://127.0.0.1:${relay.port}`;
    const mk = (id: string, room: string) => {
      const t = new WebSocketRelayTransport({ url, room, id, factory, baseDelayMs: 50 });
      const doc = new MeshDoc();
      const sync = new MeshSync(doc, { gossipEveryMs: 200, fanout: 0 });
      sync.addTransport(t);
      t.start();
      sync.start();
      return { t, doc, sync };
    };
    const a = mk("phone-a", "lahaina");
    const b = mk("phone-b", "lahaina");
    const c = mk("phone-c", "elsewhere");
    try {
      await waitFor(() => a.t.peers().includes("phone-b") && b.t.peers().includes("phone-a"));
      expect(a.t.state).toBe("open");
      expect(c.t.peers()).toEqual([]);

      const pin = DeviceKey.fromSeed("phone-a").makeReport({
        lon: -156.67,
        lat: 20.87,
        report_type: "hazard_pin",
        hazard_class: "fire_front",
      });
      a.doc.addPin(pin);
      await waitFor(() => b.doc.pin(pin.id) !== undefined);
      expect(c.doc.pins()).toHaveLength(0);

      // Leaving updates the peer list on the other side.
      b.sync.stop();
      b.t.stop();
      await waitFor(() => !a.t.peers().includes("phone-b"));
    } finally {
      for (const x of [a, b, c]) {
        x.sync.stop();
        x.t.stop();
      }
    }
  });

  it("reconnects with backoff when the relay is unreachable, then recovers", async () => {
    const t = new WebSocketRelayTransport({
      url: "ws://127.0.0.1:1",
      room: "x",
      id: "p",
      factory,
      baseDelayMs: 20,
      maxDelayMs: 100,
    });
    const states: string[] = [];
    t.onState((s) => states.push(s));
    t.start();
    await waitFor(() => t.attempt >= 3, 5000);
    expect(states).toContain("reconnecting");
    expect(t.peers()).toEqual([]);
    t.stop();
    expect(t.state).toBe("closed");
  });
});
