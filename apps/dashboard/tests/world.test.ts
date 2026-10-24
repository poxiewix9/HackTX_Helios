import { describe, expect, it } from "vitest";

import {
  EDGE_FLASH_MS,
  applyMessage,
  displayClockT,
  initialWorld,
  selectSeveredCount,
  useWorld,
} from "@/store/world";
import type { WorldState } from "@/store/world";

import {
  agentEvent,
  clockTick,
  edge,
  edgeEvent,
  graphUpdated,
  hazard,
  hazardEvent,
  network,
  route,
  routesUpdated,
  telemetry,
} from "./fixtures";

function reduce(state: WorldState, patches: Partial<WorldState>[]): WorldState {
  return patches.reduce<WorldState>((s, p) => ({ ...s, ...p }), state);
}

describe("applyMessage", () => {
  it("upserts hazards by id and keeps resolved ones (status drives rendering)", () => {
    let s = initialWorld();
    s = reduce(s, [applyMessage(s, hazardEvent("hazard.created", hazard("h1")), 1000)]);
    s = reduce(s, [
      applyMessage(s, hazardEvent("hazard.updated", hazard("h1", { confidence: 0.97 })), 1001),
    ]);
    expect(s.hazards.h1?.confidence).toBe(0.97);
    // every hazard event carries the full row: the latest one wins wholesale
    s = reduce(s, [
      applyMessage(
        s,
        hazardEvent("hazard.resolved", hazard("h1", { status: "resolved", confidence: 0.97 })),
        1002,
      ),
    ]);
    expect(Object.keys(s.hazards)).toEqual(["h1"]);
    expect(s.hazards.h1?.status).toBe("resolved");
  });

  it("flashes an edge only on the transition to severed, for EDGE_FLASH_MS", () => {
    let s = initialWorld();
    s = reduce(s, [applyMessage(s, edgeEvent([edge(1), edge(2)], 1), 5000)]);
    expect(s.edgeFlash).toEqual({});
    s = reduce(s, [applyMessage(s, edgeEvent([edge(1, "severed")], 2), 6000)]);
    expect(s.edgeFlash[1]).toBe(6000 + EDGE_FLASH_MS);
    expect(selectSeveredCount(s)).toBe(1);
    // a re-send of the same severed edge must not restart the flash
    s = reduce(s, [applyMessage(s, edgeEvent([edge(1, "severed")], 3), 9000)]);
    expect(s.edgeFlash[1]).toBe(6000 + EDGE_FLASH_MS);
    expect(s.graphVersion).toBe(3);
  });

  it("measures graph.updated -> routes.updated latency and counts snaps only when routes change", () => {
    let s = initialWorld();
    s = reduce(s, [applyMessage(s, graphUpdated(7, 3, [1, 2, 3]), 10_000)]);
    s = reduce(s, [applyMessage(s, routesUpdated([route("g1", [4, 5])], 7), 10_250)]);
    expect(s.snap?.count).toBe(1);
    expect(s.snap?.latencyMs).toBe(250);
    expect(s.snap?.graphVersion).toBe(7);
    // identical publication for the same graph version: no new snap
    s = reduce(s, [applyMessage(s, routesUpdated([route("g1", [4, 5])], 7), 10_900)]);
    expect(s.snap?.count).toBe(1);
    // new version with different edges -> snap, latency unknown (no graph.updated observed for v8)
    s = reduce(s, [
      applyMessage(s, routesUpdated([route("g1", [4, 6], { version: 2 })], 8), 11_000),
    ]);
    expect(s.snap?.count).toBe(2);
    expect(s.snap?.latencyMs).toBeNull();
    expect(s.routesMeta?.computeMs).toBe(4.2);
  });

  it("tracks the backend clock and network state; the clock is never advanced locally while paused", () => {
    let s = initialWorld();
    s = reduce(s, [applyMessage(s, clockTick(120, true, 10), 1000)]);
    expect(displayClockT(s.clock, 1500)).toBeCloseTo(125); // 0.5 s wall × 10× = +5 s
    expect(displayClockT(s.clock, 60_000)).toBeLessThanOrEqual(120 + 2 * 10); // extrapolation is capped
    s = reduce(s, [applyMessage(s, clockTick(130, false, 10), 2000)]);
    expect(displayClockT(s.clock, 99_000)).toBe(130);
    s = reduce(s, [applyMessage(s, network(false), 2100)]);
    expect(s.cellularUp).toBe(false);
    expect(s.cellularReason).toBe("chaos");
  });

  it("caps agent events and telemetry ring buffers per pipeline", () => {
    let s = initialWorld();
    for (let i = 0; i < 450; i += 1)
      s = reduce(s, [applyMessage(s, agentEvent("navigator", "routes.recomputed", i), i)]);
    expect(s.agentEvents.length).toBe(400);
    expect(s.agentEvents[0]?.scenario_t).toBe(50);
    for (let i = 0; i < 700; i += 1)
      s = reduce(s, [applyMessage(s, telemetry("fp32_full", 70), i)]);
    s = reduce(s, [applyMessage(s, telemetry("int8_split", 68, "recorded"), 1)]);
    expect(s.telemetry.byPipeline.fp32_full.length).toBe(600);
    expect(s.telemetry.byPipeline.int8_split.length).toBe(1);
    expect(s.telemetry.last?.mode).toBe("recorded");
  });

  it("store action applies patches and loadSnapshot indexes by id", () => {
    const st = useWorld.getState();
    st.reset();
    st.loadSnapshot({
      edges: [edge(1), edge(2, "penalized")],
      hazards: [hazard("h9")],
      routes: [route("g1", [1])],
    });
    expect(useWorld.getState().edges[2]?.status).toBe("penalized");
    expect(useWorld.getState().hazards.h9).toBeDefined();
    expect(useWorld.getState().routesMeta?.trigger).toBe("snapshot");
    useWorld.getState().applyMessage(edgeEvent([edge(2, "severed")], 4), 100);
    expect(useWorld.getState().edges[2]?.status).toBe("severed");
    expect(useWorld.getState().graphVersion).toBe(4);
  });
});
