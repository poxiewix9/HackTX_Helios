"use client";

import { MapboxOverlay } from "@deck.gl/mapbox";
import type { PickingInfo } from "@deck.gl/core";
import maplibregl from "maplibre-gl";
import { useEffect, useRef, useState } from "react";

import { SIMULATION_LABEL } from "@arco/schemas";

import { config } from "@/lib/config";
import { bboxCenter } from "@/lib/geo";
import type { LngLat } from "@/lib/geo";
import { selectActiveHazards, useWorld } from "@/store/world";

import { ThreeLayer } from "./ThreeLayer";
import { buildLayers, describePicked } from "./layers";

export interface MapViewProps {
  satellite: boolean;
  three: boolean;
  labels: boolean;
  /** When set, the next map click is reported here (chaos hazard placement). */
  onPick: ((lngLat: LngLat) => void) | null;
}

export function MapView({ satellite, three, labels, onPick }: MapViewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const overlayRef = useRef<MapboxOverlay | null>(null);
  const threeRef = useRef<ThreeLayer | null>(null);
  const pickRef = useRef(onPick);
  const labelsRef = useRef(labels);
  const [tooltip, setTooltip] = useState<{ x: number; y: number; text: string } | null>(null);
  const [mapError, setMapError] = useState<string | null>(null);
  const scenario = useWorld((s) => s.scenario);
  const fittedFor = useRef<string | null>(null);
  pickRef.current = onPick;
  labelsRef.current = labels;

  // Create the map once.
  useEffect(() => {
    const el = containerRef.current;
    if (!el || mapRef.current) return;
    const map = new maplibregl.Map({
      container: el,
      style: config.mapStyle,
      center: [-156.675, 20.88],
      zoom: 13.2,
      pitch: 48,
      bearing: -12,
      attributionControl: false,
      antialias: true,
    });
    map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-right");
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "bottom-right");
    map.on("error", (e: { error?: unknown }) => {
      const msg = e.error instanceof Error ? e.error.message : String(e.error);
      if (/style|tile|fetch|Failed/i.test(msg))
        setMapError("basemap tiles unavailable (offline?) – overlays still render");
    });
    const overlay = new MapboxOverlay({
      interleaved: false,
      layers: [],
      getTooltip: (info: PickingInfo) => {
        const text = describePicked(info.object);
        return text ? { text } : null;
      },
      onHover: (info: PickingInfo) => {
        const text = describePicked(info.object);
        setTooltip(text ? { x: info.x, y: info.y, text } : null);
      },
    });
    map.addControl(overlay);
    map.on("load", () => {
      map.addSource("satellite", {
        type: "raster",
        tiles: [config.satelliteTiles],
        tileSize: 256,
        attribution: config.satelliteAttribution,
        maxzoom: 19,
      });
      const firstSymbol = map.getStyle().layers.find((l) => l.type === "symbol")?.id;
      map.addLayer(
        { id: "satellite", type: "raster", source: "satellite", paint: { "raster-opacity": 0.92 } },
        firstSymbol,
      );
      const threeLayer = new ThreeLayer("three-volumes", [-156.675, 20.88], (fps) => {
        useWorld.getState().setThreeFps(fps, threeLayer.stats());
      });
      map.addLayer(threeLayer);
      threeRef.current = threeLayer;
    });
    map.on("click", (e) => {
      const pick = pickRef.current;
      if (pick) pick([e.lngLat.lng, e.lngLat.lat]);
    });
    mapRef.current = map;
    overlayRef.current = overlay;
    // Debug / e2e handle (not used by application code).
    (window as unknown as { __arcoMap?: maplibregl.Map }).__arcoMap = map;
    return () => {
      delete (window as unknown as { __arcoMap?: maplibregl.Map }).__arcoMap;
      threeRef.current = null;
      overlayRef.current = null;
      mapRef.current = null;
      map.remove();
    };
  }, []);

  // Fit to the scenario once per scenario; re-origin the three.js scene.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !scenario || fittedFor.current === scenario.id) return;
    fittedFor.current = scenario.id;
    const center = bboxCenter(scenario.bbox);
    map.fitBounds(
      [
        [scenario.bbox[0], scenario.bbox[1]],
        [scenario.bbox[2], scenario.bbox[3]],
      ],
      { padding: 60, pitch: 48, bearing: -12, duration: 1200 },
    );
    const apply = () => {
      threeRef.current?.setOrigin(center);
    };
    if (threeRef.current) apply();
    else void map.once("idle", apply);
  }, [scenario]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      if (map.getLayer("satellite"))
        map.setLayoutProperty("satellite", "visibility", satellite ? "visible" : "none");
    };
    if (map.isStyleLoaded()) apply();
    else void map.once("idle", apply);
  }, [satellite]);

  useEffect(() => {
    if (threeRef.current) threeRef.current.enabled = three;
  }, [three]);

  useEffect(() => {
    const el = mapRef.current?.getCanvas();
    if (el) el.style.cursor = onPick ? "crosshair" : "";
  }, [onPick]);

  // Per-frame layer refresh (snap animation, flashes, flow).
  useEffect(() => {
    let raf = 0;
    let lastEdges: unknown = null;
    let edgesRev = 0;
    const frame = () => {
      raf = requestAnimationFrame(frame);
      const overlay = overlayRef.current;
      if (!overlay) return;
      const s = useWorld.getState();
      if (s.edges !== lastEdges) {
        lastEdges = s.edges;
        edgesRev += 1;
      }
      const now = performance.now();
      const layers = buildLayers({
        hazards: selectActiveHazards(s),
        edges: Object.values(s.edges),
        edgeFlash: s.edgeFlash,
        routes: s.routes,
        snapAt: s.snap?.at ?? null,
        safeZones: Object.values(s.safeZones),
        populationCells: s.scenario?.population_cells ?? [],
        reports: s.reports,
        now,
        showLabels: labelsRef.current,
        edgesRev,
      });
      overlay.setProps({ layers });
      threeRef.current?.update({
        hazards: Object.values(s.hazards),
        routes: s.routes,
        safeZones: Object.values(s.safeZones),
      });
    };
    raf = requestAnimationFrame(frame);
    return () => {
      cancelAnimationFrame(raf);
    };
  }, []);

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="absolute inset-0" data-testid="map" />
      <div className="pointer-events-none absolute left-2 top-2 z-10 flex flex-col gap-1">
        <span className="rounded-sm border border-ops-warn/60 bg-ops-bg/80 px-2 py-0.5 font-mono text-[10px] uppercase tracking-widest text-ops-warn">
          {SIMULATION_LABEL}
        </span>
        {mapError && (
          <span className="rounded-sm border border-ops-edge bg-ops-bg/80 px-2 py-0.5 font-mono text-[10px] text-ops-dim">
            {mapError}
          </span>
        )}
      </div>
      {tooltip && (
        <div
          className="pointer-events-none absolute z-20 max-w-xs whitespace-pre-line rounded border border-ops-edge bg-ops-bg/95 px-2 py-1 font-mono text-[11px] text-ops-text"
          style={{ left: tooltip.x + 12, top: tooltip.y + 12 }}
        >
          {tooltip.text}
        </div>
      )}
    </div>
  );
}
