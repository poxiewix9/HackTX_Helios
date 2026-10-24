import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { StatusBar } from "@/components/StatusBar";
import { parseMessage } from "@/hooks/useLiveFeeds";
import { useWorld } from "@/store/world";

import { clockTick, graphUpdated, network, route, routesUpdated } from "./fixtures";

describe("StatusBar", () => {
  beforeEach(() => {
    useWorld.getState().reset();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows CONNECTING, then LIVE / RECORDED REPLAY, and the snap indicator fires on a route change", () => {
    render(<StatusBar />);
    expect(screen.getByTestId("mode-badge")).toHaveTextContent("CONNECTING");
    act(() => {
      useWorld.getState().setMode("live");
    });
    expect(screen.getByTestId("mode-badge")).toHaveTextContent("LIVE STACK");
    act(() => {
      useWorld.getState().applyMessage(graphUpdated(3, 2, [1, 2]), 1000);
      useWorld.getState().applyMessage(routesUpdated([route("g1", [7])], 3), 1120);
    });
    const snap = screen.getByTestId("snap-indicator");
    expect(snap).toHaveAttribute("data-state", "firing");
    expect(snap).toHaveAttribute("data-count", "1");
    expect(snap).toHaveTextContent("120 ms");
    act(() => {
      vi.advanceTimersByTime(500);
    });
    expect(snap).toHaveAttribute("data-state", "fired");
    act(() => {
      useWorld.getState().setMode("replay");
      useWorld.getState().setReplayInfo({
        recordedAt: "2026-10-24T20:00:00Z",
        scenarioId: "lahaina_2023",
        messages: 10,
      });
      useWorld.getState().applyMessage(network(false), 2000);
    });
    expect(screen.getByTestId("mode-badge")).toHaveTextContent("RECORDED REPLAY");
    expect(screen.getByTestId("replay-banner")).toHaveTextContent("backend unreachable");
    expect(screen.getByTestId("cellular-badge")).toHaveTextContent("DOWN");
  });
});

describe("parseMessage", () => {
  it("accepts bus messages and rejects anything else", () => {
    expect(parseMessage(JSON.stringify(clockTick(1, true)))?.type).toBe("clock.tick");
    expect(parseMessage('{"hello":1}')).toBeNull();
    expect(parseMessage("not json")).toBeNull();
  });
});
