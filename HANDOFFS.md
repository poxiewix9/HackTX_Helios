# ARCO HANDOFFS

Agents communicate only through this file, `TASKS.md`, and `packages/shared-schemas`.
Append; never rewrite history.

## BLOCKERS
(none)

## CONTRACT CHANGE REQUESTS (to Architect)
- 2026-10-24 agents → architect — **APPLIED** (schema 0.2.1): `RoadEdge.capacity_factor: float = 1.0` (0 < x ≤ 1), the Cartographer's
  capacity reduction for `crowd_bottleneck` so the Navigator's congestion term sees it without a side channel. Blackboard column +
  migration `0003_edge_capacity_factor`, `convert` round-trip, `make schemas` regenerated (JSON + TS). Non-breaking (default 1.0).

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
