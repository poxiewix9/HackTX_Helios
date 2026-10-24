"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";

import type { HazardClass } from "@arco/schemas";

import { AgentPanel } from "@/components/AgentPanel";
import { ControlBar, useChaosPlacement } from "@/components/ControlBar";
import { StatusBar } from "@/components/StatusBar";
import { StreetFeed } from "@/components/StreetFeed";
import { TelemetryStrip } from "@/components/TelemetryStrip";
import { useLiveFeeds } from "@/hooks/useLiveFeeds";

// MapLibre / deck.gl / three touch WebGL: client-only.
const MapView = dynamic(() => import("@/components/map/MapView").then((m) => m.MapView), {
  ssr: false,
  loading: () => (
    <div className="flex h-full items-center justify-center font-mono text-xs text-ops-dim">
      loading map…
    </div>
  ),
});

export function Dashboard() {
  useLiveFeeds(true);
  const [satellite, setSatellite] = useState(true);
  const [three, setThree] = useState(true);
  const [labels, setLabels] = useState(true);
  const [presentation, setPresentation] = useState(false);
  const [placing, setPlacing] = useState<HazardClass | null>(null);
  const onPick = useChaosPlacement(placing, setPlacing);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "p" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setPresentation((v) => !v);
      }
      if (e.key === "Escape") setPlacing(null);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  return (
    <main
      className="flex h-screen w-screen flex-col overflow-hidden"
      data-presentation={presentation}
    >
      <StatusBar />
      <div
        className="grid min-h-0 flex-1 gap-1 p-1"
        style={{ gridTemplateColumns: presentation ? "1fr" : "minmax(0,1fr) 26rem" }}
      >
        <div
          className="grid min-h-0 gap-1"
          style={{ gridTemplateRows: presentation ? "1fr" : "minmax(0,1fr) 9rem" }}
        >
          <div className="panel relative min-h-0 overflow-hidden">
            <MapView
              satellite={satellite}
              three={three}
              labels={labels}
              onPick={placing ? onPick : null}
            />
          </div>
          {!presentation && <TelemetryStrip />}
        </div>
        {!presentation && (
          <div className="grid min-h-0 gap-1" style={{ gridTemplateRows: "16rem minmax(0,1fr)" }}>
            <StreetFeed />
            <AgentPanel />
          </div>
        )}
      </div>
      <ControlBar
        satellite={satellite}
        setSatellite={setSatellite}
        three={three}
        setThree={setThree}
        labels={labels}
        setLabels={setLabels}
        presentation={presentation}
        setPresentation={setPresentation}
        placing={placing}
        setPlacing={setPlacing}
      />
    </main>
  );
}
