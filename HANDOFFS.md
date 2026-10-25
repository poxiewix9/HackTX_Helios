# ARCO HANDOFFS

Agents communicate only through this file, `TASKS.md`, and `packages/shared-schemas`.
Append; never rewrite history.

## BLOCKERS
(none)

## CONTRACT CHANGE REQUESTS (to Architect)
- 2026-10-24 agents → architect — **APPLIED** (schema 0.2.1): `RoadEdge.capacity_factor: float = 1.0` (0 < x ≤ 1), the Cartographer's
  capacity reduction for `crowd_bottleneck` so the Navigator's congestion term sees it without a side channel. Blackboard column +
  migration `0003_edge_capacity_factor`, `convert` round-trip, `make schemas` regenerated (JSON + TS). Non-breaking (default 1.0).
- 2026-10-24 vision → architect — **APPLIED** (schema 0.2.2): `PipelineKind += "synthetic"`; `BenchmarkResult.stream: StreamKind | None = None`
  (benchmark measures per stream). Non-breaking; `make schemas` regenerated JSON + TS; base station now labels the synthetic detector's
  telemetry `pipeline="synthetic"`. Original request kept below for the record.
- 2026-10-24 vision → architect — (superseded, applied above) `PipelineKind` currently is `Literal["fp32_full", "int8_split"]`. The base station
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

### [benchmark -> reviewer, dashboard] 2026-10-24 21:40  5.1
What I built: `services/benchmark` (`arco_benchmark`) — `FullPipeline` (JPEG q90 → unsplit FP32) vs `SplitPipeline` (INT8 head →
feature packet → FP32 tail) on identical seeded frames, `run_pipeline` with an explicit no-queue pacing/drop model, `summarize` →
`BenchmarkResult` (verdict rule `mean ≤ 80 ms ∧ fps ≥ 30`, bottleneck named when missed), `HardwareInfo` read from the OS,
JSON + CSV writer, recorded replay (`mode="recorded"`), `arco-bench run|show|replay|serve`, FastAPI :8005 (`/health`, `/runs`,
`/results`, `/ws/telemetry`, `POST /run`, `POST /replay/{id}`), compose `benchmark` service, committed recorded run
`services/benchmark/recorded/20261024T212059Z-b8ae37.json` (M1 Pro CPU).
How to run/test:
  make bench [FRAMES=60 PACE_FPS=30 RECORD=1]      # needs models/<id>/manifest.json
  make bench-show                                   # table of the committed run
  make test-benchmark                               # 8 tests (fake clock + real short run when models exist)
  docker compose up -d --wait benchmark && websocat ws://localhost:8005/ws/telemetry
Interfaces I expose / changed:
  - `/ws/telemetry` on :8005 carries `TelemetryChannelMessage` = `telemetry.benchmark{result}` then `telemetry.sample{sample}`;
    `sample.mode` / `result.mode` is `recorded` for replays and `live` during `POST /run`. Badge it exactly.
  - `BenchmarkResult.stream` (schema 0.2.2) distinguishes micro/macro rows; `hardware` is inside every result.
  - Python: `arco_benchmark.runner.{run_pipeline, summarize, compare}`, `arco_benchmark.report.{BenchmarkRun, write_run, load_run}`,
    `arco_benchmark.replay.iter_replay`, `arco_benchmark.hardware.read_hardware`.
What I need from you:
  - Reviewer: `/review` 5.1. Check the drop model against the test (`test_paced_run_drops_arrivals_missed_while_busy`) and that no
    number in the README is typed rather than copied from the recorded run file.
  - Dashboard (8.3): telemetry strip = two traces (`fp32_full`, `int8_split`) from :8005; show `hardware.cpu`/`gpu` next to the badge;
    with the backend down, the committed `recorded/*.json` can be bundled as the offline fallback (it is 136 kB).
Known limitations:
  - `network` stage is 0 (single process). CPU/GPU utilisation is this process's `cpu_percent`, not system-wide; GPU % is None (no GPU EP).
  - On the dev machine both pipelines miss the target and the split is not meaningfully faster — recorded and documented, not tuned away.
    A GPU tail / deeper cut is the obvious next experiment; the harness needs no change for it (`--providers`, `make export-split SPLIT_INDEX=`).
  - Synthetic frames: timings are real, detections are not meaningful; `--source` accepts real footage.

### [agents -> reviewer, mobile, simulator, dashboard] 2026-10-24 21:45  6.1
What I built: Consensus agent in `services/agents` — `arco_agents/reports.py` (Ed25519 signing/verification, `DeviceKey`, canonical bytes), `arco_agents/clustering.py` (spatial-temporal DBSCAN), `arco_agents/consensus.py` (trust ledger + rate limit, quorum of k independent trusted reporters at a vision-negative spot → community hazard, corroboration, denial → disputed/resolved, every decision an `AgentEvent(agent="consensus")`), policy `config/consensus.yaml`, LangGraph wiring (`report.*` → consensus → cartographer → navigator in one run, hazard-seq dedupe), runtime topic `report`, `/health` counters.
How to run/test: `make test-consensus` (needs DB), `make test-agents`, live: sign with `DeviceKey.from_seed(b"...")`, `POST :8001/reports?scenario_t=…` twice from two keys at one spot → watch `:8003/agent-events?agent=consensus` and `/ws/routes`.
Interfaces I expose / changed:
- **Signing contract for mobile (9.1) and simulator (7.1):** signature = Ed25519 over `canonical_bytes(...)` = `json.dumps({"id", "lon", "lat", "report_type", "hazard_class", "target_hazard_id", "reporter_hash", "created_at", "note"}, sort_keys=True, separators=(",", ":"))` UTF-8, with `lon`/`lat` rounded to 6 dp, `created_at` as ISO-8601 with milliseconds and `Z`, `hazard_class`/`target_hazard_id`/`note` as `null` when absent (ids as lowercase UUID strings). `reporter_hash = sha256(raw 32-byte pubkey).hexdigest()`; `device_pubkey` = base64 of the raw 32-byte public key; `signature` = base64 of the 64-byte signature. Reference implementation: `arco_agents.reports`; a TS port must produce byte-identical canonical JSON (JSON.stringify with sorted keys and no spaces is equivalent).
- Scenario community events should use deterministic keys (`DeviceKey.from_seed(label.encode())`) so reruns are reproducible; two *distinct* seeds are required to reach quorum.
- Blackboard (owned by blackboard role, edited here with this note — please review): `BlackboardService.update_report(report_id, *, trust_score, confirms, denies)` and `reports_near(lon, lat, radius_m, *, since)` thin wrappers over existing repo functions; no schema/migration change.
- Dashboard: consensus reasoning arrives as `agent.event` with `agent="consensus"`, kinds `report.pending|rejected|ignored|duplicate`, `community.hazard.created|updated`, `hazard.corroborated|disputed|resolved_by_community`, `report.corroborated_by_vision`; `data` carries `independent`, `weight`, `reason`.
What I need from you: Reviewer — move 6.1 to DONE if the smoke false-negative test and live log satisfy the gate. Mobile — implement the canonical signing above and verify against `arco_agents.reports.verify_report` (add a fixture report to `data/samples/` if useful).
Known limitations: trust is in-memory per agent instance (resets to prior on restart); "independent" = distinct device keys (Sybil resistance only via rate limit + trust floor); vision-negative is evaluated against *active* vision hazards within 80 m, a recently resolved one is marked disputed rather than trusted silently.

### [simulator -> dashboard, reviewer, blackboard, agents] 2026-10-24 22:40 7.1, 7.2
What I built: `services/simulator` (`arco_sim`, `:8004`) — scenario player (`Player`: play/pause/speed/seek/advance with the scenario clock as the single source of truth, chronological firing of timeline + community events, HTTP-only reconciliation on seek, chaos hazard + cellular, `check_expected`), FastAPI (`/health /scenarios /scenario /state /check /load /play /pause /speed /seek /chaos/hazard /chaos/cellular /feeds/{cam}/frame.jpg /feeds/{cam}/clip.mp4`), `arco-sim` CLI (`validate | play --check | render-feed | serve`), synthetic street-feed renderer (cv2, watermark "SIMULATED street feed", overlays from `micro_feeds[].synthetic_overlays`), three scenarios on real OSM geometry (`data/scenarios/{lahaina_2023,joplin_2011,camp_fire_2018}.json`) plus committed ODbL graphs `data/samples/osm/{joplin,paradise}_drive.graphml`. Compose service `simulator` (depends on healthy blackboard + agents), Dockerfile, `scripts/healthcheck.sh` section, Make targets `validate-scenarios test-sim serve-simulator play-scenario render-feed`.
How to run/test: `make validate-scenarios` · `make test-sim` (needs `make db-up`) · `docker compose up -d --wait simulator` then `curl -X POST :8004/speed -d '{"speed":60}' && curl -X POST :8004/play` · `make play-scenario SCENARIO=joplin_2011 SPEED=120` (headless, exits 2 if `expected_blocked_edges` unmet).
Interfaces I expose / changed:
- Simulator REST above; `PlayerState` (`t, playing, speed, timeline_cursor/total, community_cursor/total, active_hazards, cellular_up, graph_version, events_fired, reports_sent, last_error, log[]`) is what the scenario control bar should bind to; every transport change also publishes `clock.tick` on the blackboard bus (topic `clock`) so the dashboard can follow `scenario_t` without polling :8004.
- Blackboard (owned by blackboard role; edited here, please review): `POST /graph/ingest {source, ref, network_type}` (201; 404 on missing graphml), `POST /clock`, `POST /network?scenario_t=`; `ingest.load_graph` resolves relative `graphml_file` refs against the repo root and raises `FileNotFoundError` explicitly; `repo.replace_routes` retires *all* current routes before inserting the new publication (a route through a severed edge no longer survives from an earlier graph version); blackboard image now ships `data/samples/osm`.
- Agents (owned by agents role; edited here): `Consensus._target` is binding on `target_hazard_id` (stale targeted denials never retarget another hazard) — regression test added.
- Every Dockerfile gained the `services/simulator/pyproject.toml` + `arco_sim/__init__.py` stub (uv `--frozen` sync needs every workspace member).
What I need from you: Dashboard — scenario control bar drives `:8004` (`/scenarios`, `/load`, `/play`, `/pause`, `/speed`, `/seek`), chaos button → `/chaos/hazard` + `/chaos/cellular`, street-feed panel → `/feeds/{camera_id}/frame.jpg?t=` (poll) or `/clip.mp4`; show `label` ("Simulated on real event geometry") wherever scenario data is rendered. Reviewer — gate 7.1/7.2 on the pasted logs in TASKS.md. Architect — none (no schema changes needed).
Known limitations: footage is synthetic and flat-shaded (plumbing, not accuracy); polygons are hand-digitised approximations with compressed timelines (stated in each scenario's `notes`); `expected_blocked_edges` is a 40-edge sample of the measured severed set on present-day OSM, re-measured whenever polygons change; macro tiles are optional (synthetic labelled tile when the PNG is missing); backward seek resolves hazards but leaves earlier agent events as history; the simulator trusts a single blackboard — no retry queue if the blackboard is down mid-run (`last_error` is set and playback pauses).

### [dashboard -> mobile, reviewer, integrator, architect] 2026-10-24 23:30  8.1, 8.2, 8.3
What I built: `apps/dashboard` (`@arco/dashboard`) — Next.js 15 / React 19 ops dashboard. Zustand store with a pure `applyMessage` reducer over `@arco/schemas` `AnyMessage`; `ReconnectingSocket` (exp backoff + jitter, state + attempt counter in the status bar) on `/ws/blackboard`, benchmark `/ws/telemetry`, base-station `/ws/detections`; MapLibre basemap (OpenFreeMap dark + ESRI imagery toggle) with deck.gl overlays (hazards, edges with severed flash, routes with the snap animation, flow trips, population + report pins, labels) and a three.js custom layer (hazard volumes, instanced crowd particles, beacons, FPS measured in `render`); simulator control bar (scenario / play / pause / scrub / speed / chaos hazard-by-click / chaos cellular); synthetic street-feed panel bound to the backend clock; uPlot telemetry strip with LIVE/RECORDED from `telemetry.sample.mode`; agent reasoning feed; presentation mode (`Ctrl+P`); recorded-replay fallback when the blackboard is unreachable (`public/recorded/lahaina_2023.replay.json`, format `arco.replay/1`, re-recorded with `make record-replay`).
How to run/test: `make dev-dashboard` (`DASHBOARD_PORT=3100 make dev-dashboard` if 3000 is busy) with the compose stack up; `make test-dashboard` (vitest, 20); `make e2e-dashboard` (Playwright: live scenario + snap, chaos cellular, backend-killed replay — starts its own dev servers on :3200/:3201; the live spec skips if :8004 is down). `PW_TRACE=1` enables Playwright tracing (off by default: trace snapshots read the WebGL canvases back and stall the page under SwiftShader).
Interfaces I expose / changed: nothing server-side. REST is proxied through Next rewrites (`/api/bb`, `/api/sim`, `/api/agents`, `/api/base`, `/api/bench` → `*_URL`); WS origins via `NEXT_PUBLIC_*_WS` (see `.env.example`). Root `eslint.config.mjs` gained a browser-app override for `apps/dashboard/**` (globals, `restrict-template-expressions` allows number/boolean); `.gitignore`/`.prettierignore` ignore `.next-*`, `playwright-report`, `test-results`. Makefile: `dev-dashboard`, `test-dashboard`, `e2e-dashboard`, `record-replay`.
What I need from you: Mobile (9.1) — the dashboard will host the mesh panel (R3F + drei) fed by whatever mesh/CRDT state message you publish; please route it through the blackboard bus so the dashboard keeps a single socket, and request the message type from the Architect (I will only import it from `@arco/schemas`). Reviewer — gate 8.1–8.3 on the pasted logs in TASKS.md; note the FPS caveat (reported, not asserted; ~120 fps desktop vs ~1 fps headless SwiftShader). Integrator (10.2) — `make demo` should start the stack, `make dev-dashboard`, load Lahaina and open `http://localhost:${DASHBOARD_PORT}`; the control bar needs no manual steps beyond pressing play (or `POST :8004/play`). Architect — none required; a `telemetry.pipeline = synthetic` kind is still outstanding from 4.2.
Known limitations: basemap tiles need internet (replay data does not); three.js is a raw MapLibre custom layer (R3F/drei planned for the Phase 9 mesh panel); the replay file normalises edges to open at t=0 and is a single scenario (Joplin / Camp Fire fall back to the Lahaina recording only if `NEXT_PUBLIC_DEFAULT_SCENARIO` is changed and re-recorded); snap latency shown is backend `graph.updated.ts` → `routes.updated.ts` and climbs under 200× load (33 ms observed at 1×, 863 ms at 200× with 9 active hazards); the street feed is the simulator's synthetic frame, labelled as such in the panel.

### [mobile -> reviewer, integrator, architect, dashboard] 2026-10-25 00:20  9.1
What I built: `apps/mobile` (`@arco/mobile`) — Expo SDK 58 community app plus an RN-free core (`@arco/mobile/core`): `Transport` seam; `SimulatedMeshNetwork`/`SimulatedMeshTransport` (labelled simulated; range, loss, latency, per-device cellular, partition/heal); `WebSocketRelayTransport` + `scripts/relay-server.mjs`; Ed25519 `DeviceKey`/`makeReport`/`verifyReport` byte-compatible with `arco_agents.reports`; yjs `MeshDoc` (pins verified on every merge, routes LWW by graph_version/version, `uploaded` set) + `MeshSync` gossip; `Uplink` (gateway → `POST /reports`, once per pin, 409 = ok); on-device A\* fallback router over `assets/tiles/lahaina_2023.json` (exported by `scripts/export_graph_tile.py`); guidance (arrow/turn/arrive/off-route); `MeshNode` tying it together with `connectivity()` = `cell | mesh | isolated`. Phone UI: arrow, instruction, provenance NAVIGATOR vs ON-DEVICE FALLBACK, pin picker, cellular toggle, radio availability, simulation labels. Dashboard "Community mesh" panel (`apps/dashboard/src/components/mesh`, R3F + drei) runs the same core with a gateway that uplinks to the real blackboard and can bridge to the relay.
How to run/test: `make test-mobile` (30), `make test-dashboard` (22), `make e2e-dashboard` (4, incl. mesh pin → gateway → upload → `report.*` event), `make bundle-mobile` (Metro compile proof), `make mesh-relay` + `EXPO_PUBLIC_MESH_RELAY_URL=ws://<lan-ip>:8787 EXPO_PUBLIC_BLACKBOARD_URL=http://<lan-ip>:8001 make dev-mobile` for a real phone (see `apps/mobile/README.md`).
Interfaces I expose / changed: no server-side changes and no new schema type — mesh state never crosses the bus; pins reach the blackboard only as ordinary signed `CommunityReport`s through the gateway's uplink (so the Consensus agent, trust and clustering apply unchanged), and routes reach phones from `GET /routes` / the dashboard store through the gateway. Dashboard: `@arco/mobile` added to `transpilePackages`, `NEXT_PUBLIC_MESH_RELAY_WS` (optional), `src/types/r3f.d.ts`, right column now `15rem / 1fr / 21rem`. Root: `@types/react ~19.3.0` declared at the workspace root (needed so `next/dynamic` types resolve under ESLint after the install reshuffle), ESLint browser override extended to `apps/mobile/**`, `.gitignore` for `.expo*`. Makefile: `test-mobile`, `mesh-relay`, `dev-mobile`, `bundle-mobile`, `export-tile`. `.env.example`: mobile section.
What I need from you: Reviewer — gate 9.1 on the TASKS.md log; please check that the "simulated" labelling (panel badge, `position SIMULATED`, radio reasons) is acceptable. Integrator (10.2) — nothing manual: the mesh panel self-starts with the scenario; if `make demo` starts the relay (`make mesh-relay &`) set `NEXT_PUBLIC_MESH_RELAY_WS` so the badge shows `relay open` instead of `reconnecting`. Architect — none required. Dashboard — the earlier request for a mesh message type is withdrawn (see above).
Known limitations: BLE / Wi-Fi Direct are stubs with stated reasons (Expo Go has no BLE, ble-plx is central-only, iOS background advertising, Wi-Fi Direct Android-only); phone position is simulated; device key is in-memory per session; Expo wants Node ≥ 22.13 (Node 20 warns, bundles); drei `Html` under React 19 strict mode logs a dev-only "synchronously unmount a root" warning once per label; no physical two-phone run happened in this session (relay path covered by tests).

### [agents -> reviewer, integrator] 2026-10-25 23:20  10.1
What I built: SLM explanations stay optional and never pick a route. `SlmBackend` seam with `OllamaBackend` (`/api/generate`) and `LlamaCppBackend` (`/completion`). Fact guard rejects rewrites that drop a road name, distance or destination (or exceed 60 words) and falls back to the deterministic template. Speculative decoding is reported from the runtime: llama.cpp `draft_n`/`draft_n_accepted` → `speculative="active"` + real acceptance; no those fields → `"not_reported"`; Ollama → `"unsupported"`. Stats on `GET :8003/health` (`explainer`, `slm_*`) and as flat `slm_*` keys on every `routes.recomputed` event. `scripts/slm_bench.py` / `make slm-bench` measure a real server and exit 2 with no numbers if none is up. Dashboard route tooltip shows rationale + `[deterministic text | SLM rewrite]`.
How to run/test: `make test-slm` (19). `ARCO_SLM_ENABLED=true ARCO_SLM_BACKEND=ollama|llamacpp` plus a running runtime to see SLM text; default remains off.
Interfaces I expose / changed: settings `ARCO_SLM_BACKEND`, `LLAMACPP_URL`, `ARCO_SLM_MAX_TOKENS`, `ARCO_SLM_SPECULATIVE_N_MAX` (compose + `.env.example`). No schema change.
What I need from you: Reviewer — gate 10.1 on the TASKS.md log; please confirm we did not invent latency or acceptance numbers (we did not: no runtime on this machine, bench exit 2). Integrator — demo can leave the SLM off.
Known limitations: no Ollama / llama.cpp installed here, so no live measurement; speculative decoding is a llama.cpp *server* feature (draft model at start-up), not something we implement.

### [integrator -> reviewer, orchestrator] 2026-10-25 23:20  10.2
What I built: `make demo` (`scripts/demo.sh`) runs the full Lahaina scenario with no manual steps: compose up, healthcheck, mesh relay + Next dashboard (new session, auto-picks a free port if `:3000` is a non-ARCO server), load/seek/speed/play, open the browser (`?present=1` when `DEMO_PRESENT=1`), follow the clock, print this-run agent events and `GET :8004/check`. `make demo-stop` kills the process groups. `make demo-smoke` (`tests/e2e/test_demo_smoke.py`) plays at 200× over HTTP and asserts the simulation label, scripted hazards applied with reasoning, expected blocked edges, routes with text, signed reports through consensus, `network.cellular(down)` + `clock.tick` on the bus, no raw-video/upload path, benchmark hardware from the machine, and the dashboard HTML when `DASHBOARD_URL` is set.
How to run/test: `make demo` (or `DEMO_OPEN=0 DEMO_SPEED=120 make demo`); `make demo-smoke`; `DASHBOARD_URL=http://localhost:3001 make demo-smoke` after a demo to include the dashboard check.
Interfaces I expose / changed: Makefile `demo` / `demo-stop` / `demo-smoke`; `?present=1` on the dashboard. Nothing server-side.
What I need from you: Reviewer — gate 10.2 on the pasted logs. Orchestrator — all phases 1–10 now have IN_REVIEW or DONE evidence; this is the last feature work.
Known limitations: `DEMO_OPEN=0` in this session so no GUI browser pop; SLM off (no runtime); Colima had to be restarted before the verifying `make demo` (prior VM was down).
