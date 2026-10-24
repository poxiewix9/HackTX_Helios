import { describe, expect, it } from "vitest";

import { ReconnectingSocket, backoffDelay } from "@/lib/ws";
import type { ConnState, WebSocketLike } from "@/lib/ws";

class FakeSocket implements WebSocketLike {
  onopen: ((ev: unknown) => void) | null = null;
  onclose: ((ev: unknown) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  readyState = 0;
  closed = false;
  close(): void {
    this.closed = true;
  }
}

function harness() {
  const sockets: FakeSocket[] = [];
  const timers: { fn: () => void; ms: number }[] = [];
  const states: { state: ConnState; attempt: number }[] = [];
  const messages: string[] = [];
  const rs = new ReconnectingSocket({
    url: "ws://test/ws/blackboard",
    onMessage: (d) => messages.push(d),
    onState: (state, attempt) => states.push({ state, attempt }),
    baseDelayMs: 100,
    maxDelayMs: 1000,
    jitter: 0,
    random: () => 0,
    factory: () => {
      const s = new FakeSocket();
      sockets.push(s);
      return s;
    },
    setTimeoutFn: (fn, ms) => {
      timers.push({ fn, ms });
      return timers.length;
    },
    clearTimeoutFn: () => undefined,
  });
  const fire = () => {
    const t = timers.shift();
    t?.fn();
    return t?.ms;
  };
  return { rs, sockets, timers, states, messages, fire };
}

describe("backoffDelay", () => {
  it("doubles from the base and is capped", () => {
    expect(backoffDelay(1, 500, 10_000, 0, () => 0)).toBe(500);
    expect(backoffDelay(2, 500, 10_000, 0, () => 0)).toBe(1000);
    expect(backoffDelay(5, 500, 10_000, 0, () => 0)).toBe(8000);
    expect(backoffDelay(9, 500, 10_000, 0, () => 0)).toBe(10_000);
    expect(backoffDelay(1, 500, 10_000, 0.3, () => 1)).toBe(650);
  });
});

describe("ReconnectingSocket", () => {
  it("reports connecting -> open and forwards text frames", () => {
    const h = harness();
    h.rs.start();
    expect(h.states.at(-1)?.state).toBe("connecting");
    h.sockets[0]?.onopen?.({});
    expect(h.states.at(-1)).toEqual({ state: "open", attempt: 0 });
    h.sockets[0]?.onmessage?.({ data: '{"type":"clock.tick"}' });
    h.sockets[0]?.onmessage?.({ data: new ArrayBuffer(2) });
    expect(h.messages).toEqual(['{"type":"clock.tick"}']);
  });

  it("reconnects with exponential backoff and resets the attempt counter on success", () => {
    const h = harness();
    h.rs.start();
    h.sockets[0]?.onclose?.({});
    expect(h.states.at(-1)?.state).toBe("reconnecting");
    expect(h.fire()).toBe(100); // attempt 1 -> 100 ms
    h.sockets[1]?.onclose?.({});
    expect(h.fire()).toBe(200); // attempt 2 -> 200 ms
    h.sockets[2]?.onclose?.({});
    expect(h.fire()).toBe(400);
    h.sockets[3]?.onclose?.({});
    expect(h.fire()).toBe(800);
    h.sockets[4]?.onclose?.({});
    expect(h.fire()).toBe(1000); // capped
    expect(h.sockets.length).toBe(6);
    h.sockets[5]?.onopen?.({});
    expect(h.rs.attempt).toBe(0);
    expect(h.rs.state).toBe("open");
    // a later drop starts the ladder again from the base delay
    h.sockets[5]?.onclose?.({});
    expect(h.fire()).toBe(100);
  });

  it("stop() closes the socket, cancels retries and ignores late close events", () => {
    const h = harness();
    h.rs.start();
    const s0 = h.sockets[0];
    h.rs.stop();
    expect(s0?.closed).toBe(true);
    expect(h.rs.state).toBe("closed");
    s0?.onclose?.({});
    expect(h.timers.length).toBe(0);
    expect(h.sockets.length).toBe(1);
  });

  it("a throwing message handler does not kill the connection", () => {
    const h = harness();
    const bad = new ReconnectingSocket({
      url: "ws://x",
      onMessage: () => {
        throw new Error("boom");
      },
      factory: () => h.sockets[0] ?? new FakeSocket(),
    });
    const s = new FakeSocket();
    h.sockets.push(s);
    bad.start();
    s.onopen?.({});
    expect(() => s.onmessage?.({ data: "x" })).not.toThrow();
    expect(bad.state).toBe("open");
  });
});
