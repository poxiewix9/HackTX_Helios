import { describe, expect, it } from "vitest";

import type { AnyMessage } from "@arco/schemas";

import { ReplayPlayer, isReplayFile, orderForReplay } from "@/lib/replay";

import { clockTick, edge, edgeEvent, graphUpdated, hazard, hazardEvent } from "./fixtures";

function fakeClock() {
  let now = 0;
  const timers: { fn: () => void; ms: number; at: number }[] = [];
  return {
    now: () => now,
    advance(ms: number) {
      now += ms;
      // run everything due, in order
      for (;;) {
        const due = timers.filter((t) => t.at <= now).sort((a, b) => a.at - b.at)[0];
        if (!due) break;
        timers.splice(timers.indexOf(due), 1);
        due.fn();
      }
    },
    schedule: (fn: () => void, ms: number) => {
      const t = { fn, ms, at: now + ms };
      timers.push(t);
      return t;
    },
    cancel: (h: unknown) => {
      const i = timers.indexOf(h as (typeof timers)[number]);
      if (i >= 0) timers.splice(i, 1);
    },
    timers,
  };
}

describe("orderForReplay", () => {
  it("sorts by scenario_t then seq", () => {
    const a = clockTick(0, false);
    const b = hazardEvent("hazard.created", hazard("h"), 30);
    const c = graphUpdated(1, 1, [1], 30);
    const d = clockTick(10, true);
    expect(orderForReplay([c, d, b, a]).map((m) => m.seq)).toEqual([a.seq, d.seq, b.seq, c.seq]);
  });
});

describe("ReplayPlayer", () => {
  it("emits recorded messages at scenario_t / speed on its own clock and loops", () => {
    const clk = fakeClock();
    const emitted: AnyMessage[] = [];
    const msgs: AnyMessage[] = [
      clockTick(0, true, 1),
      hazardEvent("hazard.created", hazard("h1"), 10),
      edgeEvent([edge(1, "severed")], 2, 10),
      clockTick(20, true, 1),
    ];
    const p = new ReplayPlayer({
      messages: msgs,
      emit: (m) => emitted.push(m),
      speed: 10, // 10 scenario seconds per wall second
      now: clk.now,
      schedule: clk.schedule,
      cancel: clk.cancel,
    });
    p.start();
    expect(emitted.length).toBe(1); // t=0 message immediately
    clk.advance(500); // 5 scenario seconds
    expect(emitted.length).toBe(1);
    clk.advance(500); // t = 10 -> two more
    expect(emitted.length).toBe(3);
    clk.advance(1000); // t = 20 -> last
    expect(emitted.length).toBe(4);
    clk.advance(2100); // 2 s hold, then loop from the start
    expect(emitted.length).toBe(5);
    expect(emitted[4]?.type).toBe("clock.tick");
    p.stop();
    const n = emitted.length;
    clk.advance(10_000);
    expect(emitted.length).toBe(n);
  });

  it("validates the file format", () => {
    expect(isReplayFile({ format: "arco.replay/1", messages: [], scenario: {} })).toBe(true);
    expect(isReplayFile({ format: "nope", messages: [] })).toBe(false);
    expect(isReplayFile(null)).toBe(false);
  });
});
