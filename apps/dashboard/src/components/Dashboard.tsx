"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";

import type { HazardClass } from "@arco/schemas";

import { AgentPanel } from "@/components/AgentPanel";
import { ControlBar, useChaosPlacement } from "@/components/ControlBar";
import type { MapViewProps } from "@/components/map/MapView";
import { MeshPanel } from "@/components/mesh/MeshPanel";
import { StatusBar } from "@/components/StatusBar";
import { StreetFeed } from "@/components/StreetFeed";
import { TelemetryStrip } from "@/components/TelemetryStrip";
import { useLiveFeeds } from "@/hooks/useLiveFeeds";

// MapLibre / deck.gl / three touch WebGL: client-only.
const MapView = dynamic<MapViewProps>(
  () => import("@/components/map/MapView").then((m) => m.MapView),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-full items-center justify-center font-mono text-xs text-ops-dim">
        loading map…
      </div>
    ),
  },
);

export function Dashboard() {
  useLiveFeeds(true);
  const [satellite, setSatellite] = useState(true);
  const [three, setThree] = useState(true);
  const [labels, setLabels] = useState(true);
  const [presentation, setPresentation] = useState(false);
  const [placing, setPlacing] = useState<HazardClass | null>(null);
  const onPick = useChaosPlacement(placing, setPlacing);

  // `?present=1` opens straight into presentation mode (used by `make demo`).
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    if (q.get("present") === "1") setPresentation(true);
  }, []);

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
      className="flex h-screen w-screen flex-col overflow-hidden bg-ops-bg"
      data-presentation={presentation}
    >
      <StatusBar />
      <div
        className="grid min-h-0 flex-1 gap-2 p-2"
        style={{
          gridTemplateColumns: presentation ? "1fr" : "minmax(0, 1.25fr) minmax(24rem, 0.95fr)",
          gridTemplateRows: presentation ? "1fr" : "minmax(0, 1fr) minmax(13.5rem, 16rem)",
        }}
      >
        <div className="panel relative min-h-0">
          <MapView
            satellite={satellite}
            three={three}
            labels={labels}
            onPick={placing ? onPick : null}
          />
        </div>
        {!presentation && <StreetFeed />}
        {!presentation && (
          <div
            className="grid min-h-0 gap-2"
            style={{
              gridColumn: "1 / -1",
              gridTemplateColumns: "minmax(0, 1.35fr) minmax(15rem, 0.85fr) minmax(17rem, 0.95fr)",
            }}
          >
            <TelemetryStrip />
            <AgentPanel />
            <MeshPanel />
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
