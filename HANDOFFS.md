# ARCO HANDOFFS

Agents communicate only through this file, `TASKS.md`, and `packages/shared-schemas`.
Append; never rewrite history.

## BLOCKERS
(none)

## CONTRACT CHANGE REQUESTS (to Architect)
(none)

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
