# @arco/dashboard (Phase 8)

Operations dashboard for ARCO. A Next.js 15 (App Router, React 19) client of the
blackboard bus: MapLibre basemap (dark vector style, optional ESRI imagery), deck.gl
overlays (hazard polygons, road edges with severed-flash, routes with the **snap**
animation, flow trails, population and community pins), a three.js custom layer
(extruded hazard volumes, crowd particles, safe-zone beacons), a synthetic street-camera
panel bound to the simulator feed, a uPlot telemetry strip with the `LIVE` / `RECORDED`
badge, and the agents' reasoning feed.

Everything on screen is derived from `@arco/schemas` messages — the dashboard never
hand-writes a message type and never runs its own clock while the backend is connected.
The status bar always shows `Simulated on real event geometry` while a scenario plays.

## Run

```bash
# from the repo root, with the compose stack up (blackboard :8001, agents :8003, simulator :8004, benchmark :8005)
make dev-dashboard                 # http://localhost:${DASHBOARD_PORT:-3000}
DASHBOARD_PORT=3100 make dev-dashboard
```

REST calls go through same-origin rewrites (`/api/bb`, `/api/sim`, `/api/agents`,
`/api/base`, `/api/bench` → `*_URL`, default `http://localhost:800x`). WebSockets connect
directly from the browser to `NEXT_PUBLIC_BLACKBOARD_WS`, `NEXT_PUBLIC_BENCHMARK_WS` and
`NEXT_PUBLIC_BASE_STATION_WS` (see `.env.example`). Every socket reconnects with
exponential backoff + jitter and shows its state and attempt counter in the status bar.

Controls: scenario select, play/pause, scrub, speed (1×–200×), **chaos** (drop a hazard of
any class where you click, or kill/restore cellular), satellite / 3D / labels toggles,
**present** (or `Ctrl+P`) for a map-only presentation layout. Click any hazard, edge,
route or pin for its details.

## Offline fallback

If the blackboard socket is not open after `NEXT_PUBLIC_OFFLINE_FALLBACK_MS` (default
6 s) the dashboard loads `public/recorded/<scenario>.replay.json`, switches the mode badge
to **RECORDED REPLAY**, disables the transport controls (there is no backend clock to
drive) and plays the recording through the same reducer at 20× while the sockets keep
retrying. When the stack comes back the replay stops and the live snapshot is reloaded.

Re-record after playing a scenario on the live stack:

```bash
make play-scenario SCENARIO=lahaina_2023 SPEED=200
make record-replay      # -> apps/dashboard/public/recorded/lahaina_2023.replay.json
```

The file format is `arco.replay/1`; its `notes` state that edges are normalised to `open`
at t=0 and that the content is simulated on real event geometry.

## Tests

```bash
make test-dashboard     # vitest: reducer, reconnect/backoff, replay player, layer math, components
make e2e-dashboard      # playwright: live scenario + snap animation (needs the stack), backend-killed replay
```

Playwright starts two dev servers itself: `:3200` against the real stack and `:3201` with
every URL pointed at a dead port. The live spec skips (not passes) if the simulator is
unreachable. The three.js FPS the test measured is attached as an annotation — under
headless SwiftShader it is a software-rendering number, not the laptop's.

## Layout

```
src/app/            layout, page, globals.css
src/components/     Dashboard, StatusBar, ControlBar, StreetFeed, TelemetryStrip, AgentPanel, ui/
src/components/map/ MapView (MapLibre + deck.gl overlay), layers.ts (pure layer builders), ThreeLayer.ts
src/hooks/          useLiveFeeds (sockets, snapshot, offline fallback)
src/lib/            api, config, ws (ReconnectingSocket), replay (ReplayPlayer), geo, colors, utils
src/store/          world.ts — Zustand store with the pure applyMessage reducer
scripts/            record-replay.mjs
public/recorded/    committed replay for the default scenario
tests/, e2e/        vitest and Playwright specs
```
