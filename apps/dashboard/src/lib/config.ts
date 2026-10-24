/**
 * Runtime configuration. REST goes through same-origin `/api/*` rewrites (see
 * `next.config.mjs`); WebSockets connect straight to the services. Everything is
 * overridable through `NEXT_PUBLIC_*` so a laptop, a LAN base station and the e2e
 * "backend killed" run all use the same build.
 */
export interface DashboardConfig {
  /** WebSocket origins (ws://host:port). */
  blackboardWs: string;
  baseStationWs: string;
  benchmarkWs: string;
  /** Same-origin REST prefixes. */
  rest: { bb: string; base: string; agents: string; sim: string; bench: string };
  /** MapLibre style URL for the dark basemap and the satellite raster tile template. */
  mapStyle: string;
  satelliteTiles: string;
  satelliteAttribution: string;
  /** Default scenario id used for the recorded fallback file. */
  defaultScenario: string;
  /** Seconds after which an unconnected live stack triggers the recorded replay fallback. */
  offlineFallbackAfterMs: number;
}

/**
 * Next.js only inlines `NEXT_PUBLIC_*` variables into the client bundle when
 * they are accessed as literal `process.env.NAME` properties, so every lookup
 * below must spell the name out (no `process.env[name]` indirection).
 */
function pick(value: string | undefined, fallback: string): string {
  return value !== undefined && value !== "" ? value : fallback;
}

export const config: DashboardConfig = {
  blackboardWs: pick(process.env.NEXT_PUBLIC_BLACKBOARD_WS, "ws://localhost:8001"),
  baseStationWs: pick(process.env.NEXT_PUBLIC_BASE_STATION_WS, "ws://localhost:8002"),
  benchmarkWs: pick(process.env.NEXT_PUBLIC_BENCHMARK_WS, "ws://localhost:8005"),
  rest: {
    bb: "/api/bb",
    base: "/api/base",
    agents: "/api/agents",
    sim: "/api/sim",
    bench: "/api/bench",
  },
  mapStyle: pick(process.env.NEXT_PUBLIC_MAP_STYLE, "https://tiles.openfreemap.org/styles/dark"),
  satelliteTiles: pick(
    process.env.NEXT_PUBLIC_SATELLITE_TILES,
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  ),
  satelliteAttribution:
    "Imagery © Esri, Maxar, Earthstar Geographics | © OpenStreetMap contributors",
  defaultScenario: pick(process.env.NEXT_PUBLIC_DEFAULT_SCENARIO, "lahaina_2023"),
  offlineFallbackAfterMs: Number(pick(process.env.NEXT_PUBLIC_OFFLINE_FALLBACK_MS, "6000")),
};
