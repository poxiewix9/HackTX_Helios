# ARCO HANDOFFS

Agents communicate only through this file, `TASKS.md`, and `packages/shared-schemas`.
Append; never rewrite history.

## BLOCKERS
(none)

## CONTRACT CHANGE REQUESTS (to Architect)
- 2026-10-24 agents → architect — **APPLIED** (schema 0.2.1): `RoadEdge.capacity_factor: float = 1.0` (0 < x ≤ 1), the Cartographer's
  capacity reduction for `crowd_bottleneck` so the Navigator's congestion term sees it without a side channel. Blackboard column +
  migration `0003_edge_capacity_factor`, `convert` round-trip, `make schemas` regenerated (JSON + TS). Non-breaking (default 1.0).
- 2026-10-24 vision → architect — **REQUESTED**: `PipelineKind` currently is `Literal["fp32_full", "int8_split"]`. The base station
  runs the synthetic detector through the split topology and has to label its `TelemetrySample.pipeline` as `int8_split`; the
  `detector="synthetic"` field on `DetectionsMessage` / `/health` is the honest label today. Proposal (non-breaking): add
  `"synthetic"` to `PipelineKind` so the dashboard can badge telemetry without joining it to the detections stream. Alternative:
  `TelemetrySample.detector: str | None = None`.
- 2026-10-24 vision → architect — **FYI, no change needed now**: `FeaturePacketHeader` has no `capture_ms`, so the base station reports
  `capture = 0.0`. The edge measures it (`EdgeStats.mean_capture_ms`); if Phase 5 wants it in the per-frame sample, add
  `capture_ms: float = 0.0` to the header.

## HANDOFFS

### [architect -> reviewer, blackboard] 2026-10-24 14:20  1.1, 1.2, 1.3
What I built: monorepo scaffold (uv workspace + pnpm workspace, Ruff/mypy-strict/ESLint/Prettier/pre-commit, Makefile, CI);
`packages/shared-schemas` (Pydantic models for taxonomy, GeoJSON, blackboard rows, vision packets, telemetry, scenario,
WebSocket messages + BusEnvelope; exporter to JSON Schema; TS codegen with Ajv-backed tests); `docker-compose.yml`
(PostGIS 3.5 + pgRouting 3.8, Redis, `blackboard-migrate` one-shot); `services/blackboard` skeleton (settings, engine,
structured logging, Alembic `0001` enabling postgis + pgrouting, Dockerfile, integration tests that skip without a DB);
`.cursor` rules/commands/hook for the multi-agent workflow; `docs/ARCHITECTURE.md`.
How to run/test:
  make bootstrap && make lint
  make schemas && make schemas-check && make test
  docker compose up -d && make healthcheck
Interfaces I expose / changed:
  - Python: `from arco_schemas import Hazard, RoadEdge, EdgeHazardLink, CommunityReport, SafeZone, Route, AgentEvent,
    HazardClass, HAZARD_CLASSES, HazardEvent, EdgeEvent, GraphUpdatedEvent, ReportEvent, RoutesUpdatedMessage,
    TelemetrySample, BenchmarkResult, Scenario, BusEnvelope, AnyMessage, SCHEMA_VERSION`
  - TS: `import type { Hazard, AnyMessage, ... } from "@arco/schemas"; import { HAZARD_CLASSES, isMessageOfType, CHANNELS } from "@arco/schemas"`
  - Wire naming: `Hazard.hazard_class` (DB column may be `class`); `HazardSource` includes `simulated`;
    `TelemetrySample.mode` / `BenchmarkResult.mode` are required (`live`|`recorded`).
  - Bus: Redis channel `blackboard.<topic>` / NOTIFY channel `blackboard_events`, payload = `BusEnvelope` JSON.
  - `services/blackboard`: `arco_blackboard.db.Base` (declarative base for 2.1 tables), `get_engine()`, `session_scope()`,
    `arco_blackboard.logging.configure_logging()/get_logger()`, `arco_blackboard.settings.Settings`.
What I need from you:
  - Reviewer: run `/review` on 1.1-1.3.
  - Blackboard (2.1): subclass `Base`, add Alembic `0002_tables` (GiST indexes), keep columns aligned with the shared models.
    Request any model change via CONTRACT CHANGE REQUESTS; do not fork types.
Known limitations:
  - `pgrouting/pgrouting:16-3.4-3.6` does not exist; compose uses `16-3.5-3.8` (PostGIS 3.5.2, pgRouting 3.8.0).
  - CI workflow is written but has not run on GitHub yet (no remote configured).
  - Dev machine runs Docker via Colima (no GPU passthrough); vision work will measure CPU numbers here.

### [blackboard -> reviewer, agents, simulator, dashboard] 2026-10-24 15:50  2.1, 2.2, 2.3
What I built: `services/blackboard` — ORM tables + Alembic `0002_tables` (GiST on every geometry), `convert` (arco_schemas ⇄ rows),
`repo` (spatial queries), `bus` (transactional outbox + `pg_notify` + Redis mirror after commit; Notify/Redis subscribers; outbox catch-up),
`service` (`BlackboardService`: every write publishes its event in the same transaction), `api` (FastAPI REST + `/ws/blackboard`),
`ingest` (OSMnx → `road_edges`, cost/capacity from `config/ingest.yaml`), the committed Lahaina OSM sample, compose `blackboard` service.
How to run/test:
  docker compose up -d --wait && make healthcheck
  make ingest-sample
  make test-blackboard && make test-blackboard-events && make test-blackboard-all
Interfaces I expose / changed:
  - Schema 0.2.0 (`arco_schemas`): new `EdgeOverlap{edge_id, overlap_fraction}`; `GraphUpdatedEvent.full_rebuild: bool = False`.
    Regenerated bundle + TS (`make schemas`), `schemas-check` clean.
  - REST on :8001 (see services/blackboard/README.md). Key ones for agents: `POST /edges/intersecting {geom}` → `EdgeOverlap[]`
    (fraction is metric), `GET /edges?bbox=&status=&ids=`, `GET /graph/version`, `GET /routes`, `GET /events?since_seq=&topics=`.
  - WS `/ws/blackboard?since_seq=&topics=`: one `BlackboardMessage` JSON per frame; `since_seq` present ⇒ replay outbox then live
    (dedup by seq); absent ⇒ live only. Topics: hazard, edge, graph, report, safe_zone, agent, routes.
  - Python (in-process use by agents/simulator): `BlackboardService(engine, publisher)` with `upsert_hazard`, `set_hazard_status`,
    `edges_intersecting(geom)`, `apply_edge_updates(updates, links=, unlink_hazard_ids=, scenario_t=)` → emits `edge.updated` +
    `graph.updated` in one transaction, `publish_routes(routes, trigger=, compute_ms=, max_edge_load_ratio=)`, `append_agent_event`.
    `bus.make_subscriber(settings).events(topics)` yields `BusEnvelope`s.
  - Semantics: `road_edges.current_cost` is what Navigator must read (base_cost × penalty, or ∞/severed); `base_cost` is seconds at
    evacuation speed for the highway class; `graph_version` increments on every edge state change and on reload.
  - Default scenario_t when none is given is -1.0 (`NO_SCENARIO`); the simulator should always pass the clock.
What I need from you:
  - Reviewer: `/review` 2.1-2.3.
  - Agents (3.1): subscribe to topic `hazard`; use `edges_intersecting` + `apply_edge_updates`; do not write `road_edges` directly.
  - Simulator (7.1): inject hazards via `POST /hazards?scenario_t=` (or `BlackboardService.upsert_hazard`), source=`simulated`.
  - Dashboard (8.1): connect to `/ws/blackboard?since_seq=0` for initial state + live; `GET /edges?bbox` for the base graph.
Known limitations:
  - `edges_intersecting` is a plain `ST_Intersects` + overlap fraction; time-expanding buffers are the Cartographer's job (3.1).
  - WS fan-out is single-process (one subscription per API process); fine for the demo, not for horizontal scaling.
  - Lahaina sample is present-day OSM geometry (may differ from Aug 2023); scenario files must say so.
  - `make schemas-check` compares against the git index, so it reports "stale" while the regenerated files are uncommitted — it is clean after commit.

### [agents -> reviewer, simulator, dashboard, mobile] 2026-10-24 16:35  3.1, 3.2
What I built: `services/agents` — `penalties.yaml` policy (all 12 HazardClass values, math in comments) + `Cartographer`
(buffer → overlap → sever/penalize/capacity → `apply_edge_updates`, recomputed from links on apply/release/expire/regrow);
`Navigator` (NetworkX mirror, A*, k-diverse paths, Frank–Wolfe crowd dispersal with BPR congestion, integer crowd groups,
measured `compute_ms`, edge loads written back); `Explainer` protocol (deterministic template / Ollama SLM with fallback /
cache); LangGraph pipeline with per-node fault isolation; `AgentsRuntime` (bus consumer + tick) and FastAPI on :8003 with
`/ws/routes`; compose `agents` service; demo population file `data/samples/lahaina/demo_population.json`
("Simulated on real event geometry", approximate). Plus blackboard additions listed below.
How to run/test:
  docker compose up -d --wait && make healthcheck
  make test-cartographer && make test-navigator        (or make test-agents)
  websocat ws://localhost:8003/ws/routes   # then POST a hazard to :8001 and watch routes.updated
Interfaces I expose / changed:
  - REST :8003: `GET /health` (graph_version, routes, last_compute_ms, last_max_edge_load_ratio, recent_errors), `GET /routes`,
    `GET /agent-events?agent=&limit=`, `POST /navigator/population` (Scenario or {population_cells, safe_zones}),
    `POST /navigator/recompute`; `WS /ws/routes` streams `routes.updated` + `agent.event` messages (same JSON as the blackboard WS).
  - Bus: consumes `hazard.*`, `graph.updated`, `edge.updated`, `clock.*`; produces `edge.updated`/`graph.updated` (via blackboard
    service), `routes.updated{trigger, compute_ms, max_edge_load_ratio}`, `agent.event` kinds `hazard.applied[.noop]`,
    `hazard.resolved`, `hazard.expired`, `routes.recomputed[.overload]`.
  - Blackboard (schema 0.2.1 / migration 0003): `RoadEdge.capacity_factor`; `BlackboardService.apply_edge_updates(..., capacity_factors=)`,
    `links_for_hazard(id)`, `links_for_edges(ids)`; `Settings.arco_bus_prefix` (`ARCO_BUS_PREFIX`) threaded through
    `Publisher`/`RedisSubscriber`; `arco_blackboard.testing` (`use_test_database`, `truncate_all`, `reset_edges`, `make_hazard`, `square`).
  - Python: `arco_agents.penalties.PenaltyPolicy`, `arco_agents.cartographer.Cartographer`, `arco_agents.navigator.Navigator`,
    `arco_agents.explain.{Explainer, DeterministicExplainer, SlmExplainer, CachedExplainer}`, `arco_agents.pipeline.Pipeline`.
  - Test isolation: every suite now runs in `<db>_test` + Redis prefix `blackboard-test.`; `make test` no longer wipes the dev DB.
What I need from you:
  - Reviewer: `/review` 3.1, 3.2 (and the blackboard additions); move 3.1/3.2 to DONE if green.
  - Simulator (7.1): drive hazards with `POST :8001/hazards?scenario_t=` and publish `clock` events; the Navigator reads the
    population/safe zones from the scenario file (`ARCO_POPULATION_PATH`) or `POST :8003/navigator/population`.
  - Dashboard (8.1): subscribe to `ws://…:8003/ws/routes` (or blackboard WS topics `routes`,`agent`); `Route.explanation_source`
    tells you whether text is SLM or template; `road_edges.current_load` + `capacity_factor` are there for load colouring.
  - Mobile (9.1): `Route.instruction` is the one-liner to show; `path` is a LineString; `version` per `crowd_group_id`.
  - Agents 6.1 / 10.1 (future me): Consensus goes in front of `Cartographer.on_hazard_event`; the SLM slot is `SlmExplainer`.
Known limitations:
  - Dispersal is a static Frank–Wolfe assignment, not a time-expanded simulation; overload is flagged not solved.
  - Population cells snap to the nearest node; the demo population is approximate.
  - SLM path is implemented and unit-tested for fallback, but not exercised against a running Ollama here.
  - `graph_edges` in `/health` is the mirror's edge count (parallel edges collapsed), not `road_edges` row count.

### [vision -> reviewer, benchmark, simulator, dashboard] 2026-10-24 21:30  4.1, 4.2
What I built: `services/edge-capture` (`arco_edge`) — model-agnostic ONNX split with a privacy guard (cuts only after the last use of the
image input, single-tensor boundaries), ORT static INT8 QDQ quantization of the head, measured FP32/INT8 tolerance in `manifest.json`,
feature-packet codec (uint8 + zstd + msgpack, `FeaturePacketHeader` from the contract), deterministic letterbox, frame sources
(OpenCV / image dir / **SIMULATED** synthetic), `WebSocketLink` with a private-destination guard, `EdgeNode` + `arco-edge-run`.
`services/base-station` (`arco_base`) — `SplitDetector` (tail), `FullDetector` (unsplit baseline, in-process only), `SyntheticDetector`
(**SIMULATED**, `config/synthetic.yaml`), YOLOv8-seg postprocess (NMS, mask → Shapely polygon), `config/class_map.yaml` (identity +
labelled COCO proxy with `min_count`), georef (normalised-DLT homography with horizon rejection, GDAL tile affine), `HazardWriter`
(uuid5 identity per `source_id:class`, change/interval re-post), `TelemetryHub` (9 stages, drops, FPS, Prometheus), FastAPI on :8002.
`scripts/export_split.py` (list cuts / export / quantize / verify, non-zero exit on tolerance miss), `scripts/finetune.py`. Compose:
`base-station` (default up, `ARCO_DETECTOR=synthetic`, `./models` mounted ro) and `edge-capture` (profile `vision`). Sample georefs in
`data/samples/vision/georef/` ("Simulated on real event geometry": hypothetical camera, real Lahaina coordinates).
How to run/test:
  docker compose up -d --wait && make healthcheck          # base-station :8002 now part of the stack
  make test-split && make test-vision-e2e                  # 24 + 11 tests; the latter needs the DB (isolated <db>_test)
  make run-edge FRAMES=25                                  # host edge node -> container base station -> blackboard -> agents reroute
  uv sync --package arco-edge-capture --extra export && make export-split WEIGHTS=models/yolov8n-seg.pt   # real model (torch, ~2 GB)
  ARCO_DETECTOR=onnx_split make serve-base-station         # then ARCO_DETECTOR=onnx_split make run-edge
Interfaces I expose / changed:
  - Base station :8002: `GET /health` (detector, model_id, providers, frames per stream, drops, `last_total_ms`, `last_error`),
    `GET /metrics` (Prometheus: `arco_stage_ms{stage,stream,pipeline}`, `arco_frame_total_ms`, `arco_frames_total`,
    `arco_frames_dropped_total`, `arco_edge_bytes_total`, `arco_fps`), `POST /features` (one msgpack packet → `DetectionsMessage`),
    `WS /ws/features` (binary packets in), `WS /ws/detections` (`DetectionsMessage` JSON), `WS /ws/telemetry` (`TelemetryMessage` JSON,
    `sample.mode == "live"`). Hazards reach everyone else through the blackboard as before (`source` = `micro_vision` / `macro_vision` /
    `simulated`, `camera_id` = `source_id`, `model_id` = manifest id or `synthetic`).
  - Wire format `arco_edge.packet.encode/decode`: msgpack `{"h": <FeaturePacketHeader JSON>, "p": zstd(uint8 tensor)}`; quant params and
    tensor shape live in the header's `shape`/`dtype`/`scale`/`zero_point`/`compression` fields (contract 0.2.1, unchanged).
  - Python: `arco_edge.onnx_tools.{single_cut_points, split_model, quantize_static_int8, compare_split, SplitManifest}`,
    `arco_edge.sources.{SyntheticSource, CaptureSource, ImageDirSource}`, `arco_edge.main.EdgeNode`; `arco_base.detectors.{SplitDetector,
    FullDetector, SyntheticDetector}`, `arco_base.georef.{CameraHomography, TileAffine}`, `arco_base.postprocess.decode`,
    `arco_base.hazards.HazardWriter`, `arco_base.telemetry.TelemetryHub`, `arco_base.api.create_app`.
  - Env: `ARCO_DETECTOR=synthetic|onnx_split` (edge & base; `onnx_full` is in-process only), `ARCO_MODELS_DIR`, `ARCO_MODEL_ID`,
    `ARCO_ORT_PROVIDERS`, `ARCO_EDGE_*`, `ARCO_BASE_STATION_URL`, `ARCO_ALLOW_REMOTE_BASE` (default off), `ARCO_HAZARD_*`.
  - Makefile: `test-split`, `test-vision-e2e`, `test-vision`, `export-split`, `serve-base-station`, `run-edge`; mypy now runs per service.
What I need from you:
  - Reviewer: `/review` 4.1, 4.2. Things worth poking: the privacy guard (`single_cut_points` + `test_no_raw_frame_leaves_the_edge`),
    homography sign/horizon handling, `HazardWriter` identity semantics (one row per `source_id:class`; a second debris pile seen by the
    same camera would merge into the first until instance tracking exists — documented limitation), `Broadcaster.serve` disconnect fix.
  - Benchmark (5.1): pipeline A = `FullDetector.detect_frame(frame)` in-process (FP32 unsplit); pipeline B = `OnnxHead` + `encode` →
    `decode` + `SplitDetector`. Both return per-stage ms already; `TelemetryHub.record` builds the `TelemetrySample`. Local CPU numbers
    (not claims): head_int8 ≈ 6 ms, tail ≈ 61 ms, full ≈ 66–68 ms, packet ≈ 131 kB. Expect "tail ≈ full" on CPU; a later cut
    (`--list-cuts`) moves compute to the edge — that trade-off is yours to measure.
  - Simulator (7.1): `SyntheticDetector` keys on `scenario_t` when the edge sends it (`ARCO_EDGE_*` has no scenario clock yet — if you want
    vision hazards to follow the scenario clock, publish `clock` events and I will pace the edge from them; otherwise inject hazards
    directly as planned).
  - Dashboard (8.1): `ws://…:8002/ws/telemetry` gives the stage bar per frame (`sample.stage_ms`, `total_ms`, `fps`, `dropped_frames`,
    `bytes_sent`); `ws://…:8002/ws/detections` gives boxes/polygons in source pixels for the video overlay. Badge `detector`.
  - Architect: see CONTRACT CHANGE REQUESTS (`PipelineKind` "synthetic").
Known limitations:
  - Pretrained COCO weights + proxy class map exercise the mechanics; they are not a hazard detector until `scripts/finetune.py` is run on
    hazard data. INT8 calibration defaults to seeded noise (recorded in the manifest).
  - On this CPU the split does not reduce base-station compute (tail ≈ full); gains are bandwidth and edge offload. GPU/TensorRT untested here.
  - `network` stage = wall-clock difference between edge and base; clock skew is clamped and counted, not corrected. In the burst-mode test
    it is dominated by queueing behind the single worker, in paced runs ≈ 75 ms through Colima port forwarding.
  - One hazard row per `source_id:class` (no per-instance tracking); `capture_ms` not transmitted; telemetry `pipeline` cannot say
    `synthetic` until the contract changes.
  - `edge-capture` in compose uses the synthetic source; a real camera needs the host (device passthrough is not available under Colima).
