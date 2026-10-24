# ARCO TASK BOARD
Status: TODO | IN_PROGRESS | IN_REVIEW | DONE | BLOCKED
Each task needs: id, owner, depends_on, exit command, and evidence once done.
Tasks whose `depends_on` are not all DONE are gated; do not start them.

| id   | owner      | depends_on | task                                          | exit command                                      | status      | evidence |
|------|------------|------------|-----------------------------------------------|---------------------------------------------------|-------------|----------|
| 1.1  | architect  | -          | Monorepo scaffold, tooling, pre-commit        | `make bootstrap && make lint`                     | IN_REVIEW   | see log 1.1 |
| 1.2  | architect  | 1.1        | Shared schemas + TS codegen                   | `make schemas && make schemas-check && make test` | IN_REVIEW   | see log 1.2 |
| 1.3  | architect  | 1.1        | Docker Compose (PostGIS, Redis) + CI          | `docker compose up -d && make healthcheck`        | IN_REVIEW   | see log 1.3 |
| 2.1  | blackboard | 1.2,1.3    | Tables, migrations, indexes                   | `make test-blackboard`                            | TODO        |          |
| 2.2  | blackboard | 2.1        | Event bus + spatial API + WS stream           | `make test-blackboard-events`                     | TODO        |          |
| 2.3  | blackboard | 2.1        | OSMnx ingestion                               | `make ingest-sample && make test`                 | TODO        |          |
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
