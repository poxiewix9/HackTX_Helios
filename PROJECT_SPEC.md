# ARCO — Autonomous Relief Corridor Orchestrator — PROJECT SPEC

This is the authoritative specification. Every agent reads it before working.
Build phase-by-phase. Never leave placeholder TODOs in core logic; if something
cannot be built for real (e.g. real hardware), build a working simulated version
behind a clean interface and mark it clearly.

## 1. PROJECT SUMMARY

ARCO is a decentralized, edge-optimized evacuation routing system. It ingests two
visual streams (top-down satellite/aerial = **macro**, ground-level street cameras =
**micro**), converts detected hazards into geospatial polygons, stores them in a shared
spatial "blackboard" (PostGIS), and uses a multi-agent system to sever/penalize road
graph edges and compute safe, crowd-dispersing evacuation routes. A community mesh lets
neighbors sync routes and report hazards peer-to-peer without cloud or cellular. A
Next.js + Three.js dashboard demonstrates everything live and in real time.

The demo is **simulated on real past-disaster geometry** (Section 9). Be honest in the
UI: label it "Simulated on real event geometry", not "live inference replay" unless
inference is truly running on those frames.

## 2. TECH STACK

**Monorepo:** pnpm workspaces (JS/TS) + `uv` (Python). Docker Compose for local infra.

**Edge + base-station ML (Python 3.11):**
- PyTorch, Ultralytics YOLOv8 (nano/small) and torchvision MobileNetV2
- ONNX + ONNX Runtime (CPU and CUDA EPs); TensorRT optional, auto-detected with ORT fallback
- Quantization: PyTorch post-training static INT8 and ONNX Runtime quantization tools
- NumPy, OpenCV, Pillow
- FastAPI + Uvicorn + WebSockets; msgpack binary framing for feature tensors; zstd compression

**Spatial and data:**
- PostgreSQL 16 + PostGIS 3 + pgRouting
- SQLAlchemy 2 + GeoAlchemy2 + Alembic; Shapely, pyproj, GeoPandas
- OSMnx for road graphs from OpenStreetMap
- Redis (pub/sub for blackboard events); Postgres LISTEN/NOTIFY fallback so Redis is optional

**Agents:**
- LangGraph (multi-agent graph with shared state), NetworkX for in-memory routing graph
- Local SLM via Ollama or llama.cpp (default ~1-3B instruct: Qwen2.5 or Llama 3.2),
  speculative decoding where supported. The SLM is used for route *explanation*,
  tie-breaking and dispersal reasoning, NOT as the core pathfinder. Core pathfinding is
  deterministic (A*/Dijkstra with penalty weights) and works with the SLM off.

**Frontend dashboard:**
- Next.js 14+ (App Router, TypeScript), Tailwind, shadcn/ui
- Three.js via React Three Fiber + drei
- MapLibre GL JS + deck.gl for polygons and route vectors
- Zustand, WebSocket client, uPlot for 30 FPS telemetry plots

**Mobile / community client:**
- React Native (Expo) with TypeScript
- P2P: BLE via `react-native-ble-plx`, Wi-Fi Direct via native module where available;
  all behind a `Transport` interface
- CRDT sync (Yjs) for hazard pins and routes
- A **simulated mesh transport** (in-process / WebSocket relay that drops packets and
  simulates cellular failure) for the demo

**Testing and tooling:** pytest, Vitest, Playwright, Ruff, mypy, ESLint, Prettier,
pre-commit, GitHub Actions CI.

## 3. REPOSITORY STRUCTURE

```
(repo root)
  README.md
  PROJECT_SPEC.md
  TASKS.md / HANDOFFS.md         # multi-agent shared state
  docker-compose.yml
  Makefile
  .env.example
  packages/
    shared-schemas/        # Pydantic + JSON Schema + generated TS types (single source of truth)
  services/
    edge-capture/          # runs first N conv layers, streams feature maps
    base-station/          # FastAPI: receives features, completes inference, emits detections
    blackboard/            # PostGIS schema, migrations, event bus, spatial API
    agents/                # LangGraph: Cartographer, Navigator, Consensus
    simulator/             # scenario player, synthetic footage + telemetry, hazard injector
    benchmark/             # fp32 vs quantized split-inference benchmark harness
  apps/
    dashboard/             # Next.js + Three.js demo UI
    mobile/                # Expo community app
  data/
    scenarios/             # scenario JSON files per disaster
    samples/               # small sample frames, GeoJSON, test OSM extracts
  scripts/                 # setup, dataset download helpers, export-to-onnx, quantize
  docs/
```

## 4. SERVICE SPECIFICATIONS

### 4.1 Vision pipeline (split inference)

**Models:** two detector/segmenters, one per stream.
- Macro (satellite): `fire_front`, `smoke`, `flood_water`, `collapsed_building`,
  `tornado_swath`, `blocked_road`.
- Micro (street): `debris`, `flooded_road`, `fire`, `smoke`, `crowd_bottleneck`,
  `vehicle_blockage`, `downed_line`.
- Unified class taxonomy lives in `packages/shared-schemas` (`hazard_classes`).

**Split inference design:**
- `scripts/export_split.py` splits the model at a configurable layer index into
  `head.onnx` (INT8, edge) and `tail.onnx` (FP16/INT8, base station). Provide the full
  unsplit FP32 model as baseline.
- `edge-capture` loads `head.onnx`, reads frames from camera/video/file/RTSP, runs the
  head, quantizes activations to int8 + zstd, streams over WebSocket/HTTP to the base
  station with frame ID, stream ID (`macro`|`micro`), timestamp, geo-reference metadata.
- `base-station` decompresses, runs `tail.onnx` (TensorRT EP if available), post-processes
  masks to polygons (OpenCV contours -> simplify -> Shapely), **georeferences into WGS84**
  (homography for street cameras, affine for satellite tiles), writes hazards to the
  blackboard.
- Raw video must never leave the edge/base station. Test that no endpoint uploads raw
  frames externally.
- Target < 80 ms/frame end-to-end on the base station for the dual stream. Instrument
  every stage (capture, head, serialize, network, tail, postprocess, DB write), expose
  Prometheus metrics and a `/telemetry` WebSocket.
- Never fake latency numbers. The simulator may replay *recorded* benchmark results for
  demo stability but must label them "recorded run".

### 4.2 Blackboard (PostGIS)

Tables (SQLAlchemy models + Alembic migrations):
- `hazards`: id, geom (Polygon, 4326), class, confidence, source
  (`macro_vision|micro_vision|community`), stream/camera id, first_seen, last_seen, ttl,
  status (`active|resolved|disputed`), severity (0-1)
- `road_edges`: id, geom (LineString), osm_way_id, u, v, base_cost, current_cost, status
  (`open|penalized|severed`), capacity_estimate
- `edge_hazard_links`: edge_id, hazard_id, penalty_applied
- `community_reports`: id, geom (Point), type, reporter_hash, trust_score, created_at,
  confirms, denies
- `routes`: id, origin, destination/safe_zone_id, path geom, cost, crowd_group_id,
  computed_at, version
- `safe_zones`: id, geom, capacity, current_load
- `agent_events`: append-only audit log of every agent decision with reasoning text

GiST indexes. On hazard insert/update emit on Redis channel `blackboard.hazard` (and
NOTIFY fallback). Thin FastAPI spatial API: GET hazards in bbox, GET edges, GET routes,
POST community report, WebSocket stream of blackboard events.

### 4.3 Agents (LangGraph)

Shared blackboard state, three agents, each its own module with unit tests.

**Cartographer**: subscribes to hazard events; `ST_Intersects` against `road_edges`;
overlap fraction; penalty or sever by class + confidence thresholds (YAML); time-expanding
buffers for fire/flood using last-seen velocity; restore edges on resolve/TTL; writes
`agent_events`, updates `road_edges`, emits `graph.updated`.

**Navigator**: in-memory NetworkX mirror of `road_edges`, incremental rebuild on
`graph.updated`; penalty-weighted A* from populated origin cells to `safe_zones`;
**crowd dispersal** via multi-commodity assignment over k-shortest diverse paths with
capacity and congestion-aware cost (Frank-Wolfe style reweighting) so no edge exceeds a
stampede-density threshold; SLM-generated instructions with deterministic fallback, cached
by route hash; writes `routes`, emits `routes.updated`; reroute < 1 s on demo graph.

**Consensus**: ingests `community_reports`; trust scoring per anonymous reporter;
spatial-temporal DBSCAN clustering; k independent trusted reports at a vision-negative
location create a `community` hazard and mark vision `disputed`; denials lower confidence
but never delete without quorum; rate limits, per-device signing keys, trust weighting;
`agent_events` explain every override.

All agents degrade gracefully: if one is down, the others continue.

### 4.4 Community mesh and mobile client

Expo app: single large directional arrow, distance, one-line instruction; haptic + audio
cue on direction changes. CRDT document of hazard pins and route segments. `Transport`
implementations: `BleTransport`, `WifiDirectTransport` (best effort),
`WebSocketRelayTransport` (dev), `SimulatedMeshTransport` (demo, configurable packet loss
and offline toggle). "Drop hazard pin" creates a signed community report that propagates
P2P and uploads to Consensus when a base station is reachable. Offline-first; show
connectivity state (cell / mesh / isolated). On-device fallback router (precomputed graph
tile + local A*).

### 4.5 Simulator and scenarios

Scenario JSON schema in `packages/shared-schemas`:
```
{ id, name, event, location, bbox, start_time, duration_s,
  road_graph_source, population_cells[], safe_zones[],
  macro_feed: { tiles[], frames[] },
  micro_feeds: [ { camera_id, lat, lon, heading, fov, video_path, calibration } ],
  timeline: [ { t, type, hazard_polygon_geojson, class, confidence, stream } ],
  community_events: [ { t, type, lat, lon, reporter } ],
  expected_blocked_edges[], notes }
```
Player runs in real time (adjustable speed), injects hazards through the same blackboard
path as real inference (never bypass the agents), can run *real* inference on supplied
frames when models are present. At least 3 starter scenarios with OSM road extracts:
**Lahaina 2023** (fire, smoke false-negative + community override), **Joplin 2011**
(tornado swath, perpendicular reroute), **Camp Fire 2018** (crowd bottleneck dispersal);
**Harvey 2017** flood if time permits. Document data source and mark approximations in
each scenario's `notes`. Synthetic street-feed generator composites debris/flood/smoke
overlays at scripted timestamps. Dashboard "chaos button" injects a custom hazard or kills
the cell network.

### 4.6 Benchmark harness

`services/benchmark` runs (A) FP32 full model on both streams, (B) quantized split
pipeline. Measures per-frame latency, FPS, dropped frames, CPU/GPU utilization, memory,
bytes over the edge link. JSON + CSV output + live stream to dashboard telemetry. Honest
reporting with hardware description; recorded-run replay clearly labeled.

## 5. DASHBOARD

Full-screen, dark, tactical-ops aesthetic. Dual-Stream View (MapLibre + deck.gl satellite
map and street camera side-by-side, synchronized to scenario clock). Overlays: boxes,
masks, class-colored hazard polygons, confidence labels, animated routing vectors; severed
edges flash red, new route "snap" animation < 500 ms from graph update. Three.js layer:
extruded hazard volumes, particle crowd flow, pulsing safe-zone beacon (instancing, 60 FPS
target). Agent panel: live `agent_events`. Community mesh panel: device graph, offline
toggle, pin propagation. Telemetry strip: uPlot FP32 vs quantized split at ~30 FPS,
stage-by-stage latency, bytes sent, LIVE vs RECORDED badge. Scenario control bar: pick,
play/pause/scrub/speed, chaos button, presentation mode. Resilience: full local recorded
replay fallback if the backend is unreachable.

## 6. REAL-TIME AND PROTOCOL REQUIREMENTS

WebSocket channels `/ws/detections`, `/ws/blackboard`, `/ws/routes`, `/ws/telemetry`
with typed messages generated from shared schemas and auto-reconnect with backoff. Scenario
clock is the single source of truth; every message carries `scenario_t` and wall-clock
timestamps. All inter-service contracts defined once in `packages/shared-schemas`
(Pydantic + JSON Schema -> generated TypeScript). No hand-duplicated types.

## 7. ENGINEERING RULES

- Type everything (mypy strict on services, TS strict on apps).
- Config via `.env` and YAML; no hardcoded secrets or coordinates in code.
- Every service: `Dockerfile`, health endpoint, structured JSON logging, README.
- `docker compose up` brings up Postgres/PostGIS, Redis, blackboard, base-station,
  agents, simulator, dashboard. `make demo` loads the default scenario and opens the
  dashboard.
- Every phase ends with passing tests and a runnable demo.
- Small clear modules; comment the non-obvious math (homography, penalty weights,
  congestion cost).
- `docs/ARCHITECTURE.md` with mermaid data-flow diagram and honest "Limitations".

## 8. BUILD PHASES

1. **Foundations:** monorepo scaffold, shared schemas, Docker Compose, PostGIS +
   migrations, hazard class taxonomy, CI.
2. **Blackboard + spatial API:** tables, events, REST + WebSocket, OSMnx ingestion, tests.
3. **Cartographer + Navigator (deterministic):** sever/penalty, A*, crowd dispersal,
   route writing; SLM stubbed behind an interface.
4. **Vision pipeline:** export, split head/tail, quantization, edge-capture,
   base-station, georeferencing, per-stage metrics; pretrained weights + documented
   fine-tune script + synthetic fallback detector.
5. **Benchmark harness:** FP32 vs quantized split with real measurements and recorded replay.
6. **Consensus Agent + community reports:** trust, clustering, override logic, tests
   (incl. smoke-false-negative case).
7. **Simulator + scenarios:** schema, player, Lahaina/Joplin/Camp Fire, street-feed generator.
8. **Dashboard:** dual-stream, overlays, snap animation, Three.js, agent panel,
   telemetry, controls, offline fallback.
9. **Mobile app + mesh:** arrow UI, CRDT, Transport abstractions, simulated mesh, pins,
   on-device router, mesh panel in dashboard.
10. **SLM + polish:** Ollama/llama.cpp explanations with speculative decoding, tuning,
    presentation mode, README, demo script, final E2E test.

## 9. REAL EVENTS TO MODEL

- Lahaina 2023: blocked evacuation roads, smoke obscuring cameras, community knowledge
  beating sensors
- Joplin 2011 and Moore 2013 tornadoes: long narrow damage swaths, debris on roads (xBD,
  NWS surveys, NOAA imagery)
- Camp Fire 2018: evacuation gridlock on limited roads
- Hurricane Harvey 2017: progressive flooding severing edges (FloodNet, NOAA, Copernicus)

Document each scenario's data source and license. Never claim a scenario is a replay of
real inference output.

## 10. DEFINITION OF DONE

- Injecting a hazard polygon that intersects a route's road edge penalizes/severs that
  edge and reroutes the affected group within 1 s, visible with a snap animation.
- Two or more community reports on a vision-negative location produce a community hazard
  and a reroute, with the override logged and explained.
- The quantized split pipeline demonstrably outperforms the FP32 baseline (real numbers),
  targeting < 80 ms/frame and ~30 FPS; if hardware misses the target, report actual
  numbers and the bottleneck.
- With simulated cellular off, a phone still receives pins and reroutes via mesh.
- No raw video uploaded outside the local network (tested).
- `make demo` runs the full Lahaina scenario end to end with no manual steps.
