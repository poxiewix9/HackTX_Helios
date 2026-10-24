# ARCO TASK BOARD
Status: TODO | IN_PROGRESS | IN_REVIEW | DONE | BLOCKED
Each task needs: id, owner, depends_on, exit command, and evidence once done.
Tasks whose `depends_on` are not all DONE are gated; do not start them.

| id   | owner      | depends_on | task                                          | exit command                                      | status      | evidence |
|------|------------|------------|-----------------------------------------------|---------------------------------------------------|-------------|----------|
| 1.1  | architect  | -          | Monorepo scaffold, tooling, pre-commit        | `make bootstrap && make lint`                     | DONE        | see log 1.1 |
| 1.2  | architect  | 1.1        | Shared schemas + TS codegen                   | `make schemas && make schemas-check && make test` | DONE        | see log 1.2 |
| 1.3  | architect  | 1.1        | Docker Compose (PostGIS, Redis) + CI          | `docker compose up -d && make healthcheck`        | DONE        | see log 1.3 |
| 2.1  | blackboard | 1.2,1.3    | Tables, migrations, indexes                   | `make test-blackboard`                            | IN_REVIEW   | see log 2.1 |
| 2.2  | blackboard | 2.1        | Event bus + spatial API + WS stream           | `make test-blackboard-events`                     | IN_REVIEW   | see log 2.2 |
| 2.3  | blackboard | 2.1        | OSMnx ingestion                               | `make ingest-sample && make test`                 | IN_REVIEW   | see log 2.3 |
| 3.1  | agents     | 2.2,2.3    | Cartographer                                  | `make test-cartographer`                          | TODO        |          |
| 3.2  | agents     | 3.1        | Navigator + crowd dispersal                   | `make test-navigator`                             | TODO        |          |
| 4.1  | vision     | 1.2        | Export, split, quantize                       | `make test-split`                                 | TODO        |          |
| 4.2  | vision     | 4.1,2.2    | Edge capture + base station + georef          | `make test-vision-e2e`                            | TODO        |          |
| 5.1  | benchmark  | 4.2        | FP32 vs split benchmark + recorded replay     | `make bench`                                      | TODO        |          |
| 6.1  | agents     | 3.2        | Consensus + trust + clustering                | `make test-consensus`                             | TODO        |          |
| 7.1  | simulator  | 2.2,3.2    | Scenario player + schema validation           | `make test-sim`                                   | TODO        |          |
| 7.2  | simulator  | 7.1        | Lahaina, Joplin, Camp Fire scenarios          | `make validate-scenarios`                         | TODO        |          |
| 8.1  | dashboard  | 1.2        | Dashboard shell against mock feed             | `pnpm --filter dashboard test`                    | TODO        |          |
| 8.2  | dashboard  | 8.1,7.1    | Overlays, snap animation, Three.js layers     | `pnpm --filter dashboard e2e`                     | TODO        |          |
| 8.3  | dashboard  | 5.1,8.2    | Telemetry strip + offline fallback            | `pnpm --filter dashboard e2e`                     | TODO        |          |
| 9.1  | mobile     | 1.2,6.1    | Arrow UI, CRDT, transports, simulated mesh    | `pnpm --filter mobile test`                       | TODO        |          |
| 10.1 | agents     | 3.2        | SLM explanations + speculative decoding       | `make test-slm`                                   | TODO        |          |
| 10.2 | integrator | all        | make demo end to end + final E2E              | `make demo-smoke`                                 | TODO        |          |

## Phase gate log
(Orchestrator appends: phase, date, gate checks run, result.)

- Phase 1 — 2026-10-24 — gate: `make lint`, `make schemas-check`, `make test`, `docker compose up -d && make healthcheck` all green; 1.1-1.3 DONE (user said "continue"). Commit `de817c8`.

## Task logs
(Agents append: iteration count, commands run, last output lines, root causes found.)

### 1.1 architect — 2026-10-24 — 2 iterations
- it1: `make bootstrap && make lint` -> ruff E501 x4, RUF100; ESLint no-dynamic-delete, no-unused-vars (`_mode`), no-floating-promises; Prettier.
  Root cause: long SQL/signature lines; rest-sibling destructure not ignored by lint config; Ajv validate() returns boolean|Promise.
- it2: wrapped lines, `Reflect.deleteProperty`, `varsIgnorePattern`/`ignoreRestSiblings`, `expect(v(...)).toBe(false)`.
```
$ make bootstrap && make lint
bootstrap: done
All checks passed!            (ruff check)
21 files already formatted    (ruff format --check)
Success: no issues found in 14 source files   (mypy --strict)
All matched files use Prettier code style!
tsc --noEmit -p tsconfig.json  (clean)
```

### 1.2 architect — 2026-10-24 — 3 iterations
- it1: TS codegen produced `Confidence1`, `HazardClass1` aliases. Root cause: Pydantic emits `title` on every property and `$ref` with sibling `description`.
- it2: codegen normaliser strips nested titles, keeps only `$ref`, marks literal discriminators required. mypy: `label: Literal[...] = SIMULATION_LABEL` (str). Fixed with `Final[SimulationLabel]`.
- it3: `isMessageOfType` narrowed to `never` for multi-literal `type` (HazardEvent). Root cause: `Extract<AnyMessage,{type:T}>` not distributive over literal unions; replaced with distributive `MessageOfType<T>`.
```
$ make schemas && make schemas-check && make test
wrote 37 files to packages/shared-schemas/schema
wrote .../ts/src/generated/arco.ts
schemas-check: up to date
============================== 30 passed in 1.09s ==============================   (pytest, incl. 2 PostGIS integration tests)
 Test Files  2 passed (2)
      Tests  14 passed (14)                                                         (vitest, Ajv against the same bundle)
```

### 1.3 architect — 2026-10-24 — 2 iterations
- it1: `pgrouting/pgrouting:16-3.4-3.6` tag does not exist on Docker Hub. Root cause: wrong tag; switched to `16-3.5-3.8` (PG16 / PostGIS 3.5.2 / pgRouting 3.8.0).
- it2: healthcheck ran before the migrate one-shot finished; added wait for `blackboard-migrate` exit 0.
```
$ docker compose down -v && docker compose up -d && make healthcheck
== container health ==
  postgres   healthy
  redis      healthy
== blackboard-migrate (one-shot) ==
  exited 0
== postgres extensions ==
  pgrouting 3.8.0
  postgis 3.5.2
== redis ==
  PONG
healthcheck: OK
```
Environment note: Docker Desktop was absent on the dev machine; installed `colima` + `docker-compose` via Homebrew, started `colima` (4 CPU / 6 GB). Compose behaviour is identical.

### 2.1 blackboard — 2026-10-24 — 3 iterations
- it1: autogenerated `0002_tables.py` lacked `import geoalchemy2` (NameError on upgrade) and `graph_state` had no seed row. Fixed both; verified upgrade→downgrade→upgrade.
- it2: mypy: `gf.Geography` does not exist (`from geoalchemy2 import Geography`); `Result.rowcount` untyped → `isinstance(res, CursorResult)`; shapely untyped → `types-shapely`.
- it3: tests imported helpers via `from tests.conftest import …` (not importable without a `tests` package) → moved builders to `tests/bb_helpers.py`; `test_migrations.py` still asserted head `0001` → `0002`.
```
$ make test-blackboard
ARCO_REQUIRE_DB=1 uv run pytest services/blackboard/tests/test_migrations.py services/blackboard/tests/test_tables.py -q
13 passed in 1.90s
```
Covers: head==0002 and reversible; 7 tables + graph_state + bus_outbox; GiST on hazards/road_edges/community_reports/routes.path/safe_zones;
hazard round trip + upsert; bbox filtering; `edges_intersecting` overlap_fraction≈0.5 computed in metres; `update_edge_states` bumps graph_version;
TTL expiry; route replace/is_current; agent events; `reports_near` geography distance.

### 2.2 blackboard — 2026-10-24 — 3 iterations
- it1: `POST /hazards` 422 "Input should be a valid dictionary" — tests sent `content=<json str>` without content-type; switched tests to `json=`.
- it2: `TypeError: unhashable type: '_Client'` — `@dataclass` generated `__eq__` and removed `__hash__`; `@dataclass(eq=False)` so clients are identity-hashed in the set.
- it3: WS test hung: `since_seq=0` was treated as "no catch-up" (`if since_seq:`), so the replayed frame never arrived. Changed semantics: omit → live-only; any integer (incl. 0) → replay outbox rows with seq > since_seq, then live, de-duplicated by seq. Outbox read moved to `asyncio.to_thread` so it does not block the loop.
```
$ make test-blackboard-events
ARCO_REQUIRE_DB=1 uv run pytest services/blackboard/tests/test_bus.py services/blackboard/tests/test_api.py -q
17 passed, 1 warning in 4.77s
```
Covers: hazard insert observed on NOTIFY subscriber AND on Redis subscriber (both carry the same seq as the HTTP response); seq monotonic == outbox id;
`read_outbox(since_seq, topics)`; publisher falls back to notify-only when REDIS_URL is unreachable; created/updated/resolved/disputed event types;
/health, /metrics, POST→GET bbox (inside hit, elsewhere empty), bad bbox 422, extra field 422, reports/safe-zones/graph version/events catch-up,
/edges/intersecting, WS catch-up + live + topic filter.
Compose: `blackboard` service added (depends on migrate `service_completed_successfully`, /health healthcheck). `make healthcheck` now checks the API too.
E2E outside tests: WS client connected to the container received `graph.updated {seq:1, graph_version:1, full_rebuild:true}` published by a host-side `make ingest-sample` (through Redis).

### 2.3 blackboard — 2026-10-24 — 2 iterations
- it1: `make ingest-sample` passed positional args but the CLI uses `--graphml/--bbox/--place` → fixed Makefile. OSMnx wrote `./cache` in the repo root → `ox.settings.cache_folder = data/cache/osmnx` (git-ignored).
- it2: sample metadata `source` is an object (name/url/license), test assumed a string → fixed test.
```
$ make ingest-sample && make test
ingested 637 edges / 266 nodes; graph_version=1
======================== 62 passed, 1 warning in 6.91s =========================   (pytest)
 Test Files  2 passed (2)
      Tests  14 passed (14)                                                         (vitest)
$ make lint
All checks passed! / 34 files already formatted / Success: no issues found in 21 source files / Prettier + tsc clean
```
Covers: config defaults + fallback; `parse_bbox_wsen`; `graph_to_edges` on a synthetic MultiDiGraph (list-valued tags, missing geometry → straight line,
zero-length skipped, dense ids, cost = length/speed); sample metadata; integration ingest of the Lahaina graphml (637 edges, all lengths>0, bbox query
returns all, sub-bbox strict subset, `graph.updated(full_rebuild)` in outbox, re-ingest replaces and bumps version to 2).
Sample provenance: OpenStreetMap contributors via OSMnx, ODbL 1.0, present-day geometry (`data/samples/osm/lahaina_drive.json`).
