# ARCO TASK BOARD
Status: TODO | IN_PROGRESS | IN_REVIEW | DONE | BLOCKED
Each task needs: id, owner, depends_on, exit command, and evidence once done.
Tasks whose `depends_on` are not all DONE are gated; do not start them.

| id   | owner      | depends_on | task                                          | exit command                                      | status      | evidence |
|------|------------|------------|-----------------------------------------------|---------------------------------------------------|-------------|----------|
| 1.1  | architect  | -          | Monorepo scaffold, tooling, pre-commit        | `make bootstrap && make lint`                     | DONE        | see log 1.1 |
| 1.2  | architect  | 1.1        | Shared schemas + TS codegen                   | `make schemas && make schemas-check && make test` | DONE        | see log 1.2 |
| 1.3  | architect  | 1.1        | Docker Compose (PostGIS, Redis) + CI          | `docker compose up -d && make healthcheck`        | DONE        | see log 1.3 |
| 2.1  | blackboard | 1.2,1.3    | Tables, migrations, indexes                   | `make test-blackboard`                            | DONE        | see log 2.1 |
| 2.2  | blackboard | 2.1        | Event bus + spatial API + WS stream           | `make test-blackboard-events`                     | DONE        | see log 2.2 |
| 2.3  | blackboard | 2.1        | OSMnx ingestion                               | `make ingest-sample && make test`                 | DONE        | see log 2.3 |
| 3.1  | agents     | 2.2,2.3    | Cartographer                                  | `make test-cartographer`                          | DONE        | see log 3.1 |
| 3.2  | agents     | 3.1        | Navigator + crowd dispersal                   | `make test-navigator`                             | DONE        | see log 3.2 |
| 4.1  | vision     | 1.2        | Export, split, quantize                       | `make test-split`                                 | IN_REVIEW   | see log 4.1 |
| 4.2  | vision     | 4.1,2.2    | Edge capture + base station + georef          | `make test-vision-e2e`                            | IN_REVIEW   | see log 4.2 |
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
- Phase 2 — 2026-10-24 — gate: `make lint`, `make test` (62 py + 14 ts), `make test-blackboard-all` (35), `docker compose up -d --wait && make healthcheck`, `make ingest-sample` all green; 2.1-2.3 DONE (user said "continue"). Commit `386e819`.
- Phase 3 — 2026-10-24 — gate: `make lint`, `make test` (92 py + 14 ts), `make test-cartographer` (15), `make test-navigator` (15), `docker compose up -d --wait && make healthcheck`, live hazard → sever → reroute → `/ws/routes` verified; 3.1-3.2 DONE (user said "continue"). Commits `cea1819`, `ed2358c`.

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

### 3.1 agents — 2026-10-24 — 4 iterations
- it1: integration tests failed with FK violation on `edge_hazard_links.hazard_id` — links require the hazard row; tests now upsert the hazard through the service first (FK kept on purpose).
- it2: `shapely.ops.transform` DeprecationWarning + mypy `no-any-return` in `geo.buffer_m` → `shapely.transform` with numpy arrays, explicit `float()`.
- it3: Ruff PLR0912 on `_recompute` → extracted pure `plan_edge_update(edge, state)` (also unit-tested without a DB).
- it4: after bringing the compose `agents` container up, `test_fire_severs_edges_and_resolve_restores_them` failed (`graph_event is None`, 0 severed): the live container consumed the test's hazard event from the shared DB/Redis and severed the edges first, so the in-test Cartographer saw nothing to change. Root cause = tests shared the dev DB and the (server-wide) Redis Pub/Sub channels with the running stack. Fix: `arco_blackboard.testing.use_test_database()` — tests run in `<db>_test` (auto-created), Redis db 9, and channel prefix `blackboard-test.` (new `ARCO_BUS_PREFIX` setting threaded through `Publisher`/`RedisSubscriber`). Verified 3 consecutive green runs with the container live; container logs show no test hazards afterwards.
```
$ make test-cartographer
ARCO_REQUIRE_DB=1 uv run pytest services/agents/tests/test_cartographer.py -q
...............                                                          [100%]
15 passed in 3.95s
```
Covers: penalties.yaml loads all 12 HazardClass values; decide() ignore/sever/penalize/capacity math incl. the `(0.5+0.5·s)` severity term and `capacity_factor**w`; combine() any-sever / MAX multiplier / MIN capacity; buffer growth + cap; `plan_edge_update` idempotence; integration: fire severs real Lahaina edges and resolve restores them (graph_version +1 each way, AgentEvents written), smoke penalizes without severing, crowd_bottleneck lowers capacity_factor, overlapping fire+smoke → edge severed and release of the smoke keeps the sever, TTL expiry via `expire()`, moving front `regrow()` enlarges the affected set, low-confidence hazard → `hazard.applied.noop`.

### 3.2 agents — 2026-10-24 — 3 iterations
- it1: LangGraph typing under mypy strict (`StateGraph` generics, conditional edges) → `g: Any`, explicit edge mapping, route functions return `str`.
- it2: Navigator test for fault isolation replaced a bound method by assignment (mypy `method-assign`) → `monkeypatch.setattr`.
- it3: `make lint` mypy "Duplicate module named conftest" once both services had `tests/conftest.py` → Makefile runs mypy per service.
```
$ make test-navigator
ARCO_REQUIRE_DB=1 uv run pytest services/agents/tests/test_navigator.py services/agents/tests/test_pipeline.py -q -s
........2026-10-24 15:22:39 [warning  ] slm unavailable; deterministic fallback error='[Errno 61] Connection refused'
REROUTE compute_ms=15.5 wall_ms=108.1
2026-10-24 15:22:42 [error    ] agent node failed              error="RuntimeError('navigator exploded')" node=navigator
15 passed in 3.66s
```
Covers: RoutingGraph mirrors edges (severed removed, cheapest parallel kept), A* == Dijkstra cost, heuristic admissible, k-diverse paths differ, incremental `apply_edges`; dispersal: all cells assigned, zone capacities respected, loads ≤ max_load_ratio or flagged, `split_groups` sums to population; explainer: deterministic text contains road names, SLM unreachable → fallback (the warning above is the real fallback path), cache hits on unchanged routes; integration: initial routes for the demo population, hazard on a route → different path + `graph_version` bump + `routes.updated` on the bus with measured `compute_ms` (`REROUTE` line above), navigator exception isolated by the pipeline (`agent node failed` above is the asserted log), graph_version dedup.
Live E2E against the compose stack (`arco-agents` container): `POST :8001/hazards` fire_front over cell-front-st-south's route → container log `cartographer severed=8 compute_ms=229.6 graph_version=4` → `navigator routes=6 compute_ms=18.0 max_edge_load_ratio=0.213` → `/ws/routes` frame received 60–271 ms after the POST (two runs); `status=resolved` → `restored=8`, reroute `compute_ms=14.7`, `explainer_cache_hits=6`. `make healthcheck` now also checks `:8003/health`.
```
$ make lint      -> ruff clean, 54 files formatted, mypy: 30 + 19 source files no issues, eslint/prettier/tsc clean (exit 0)
$ make test      -> 92 passed (pytest, incl. 15 + 15 agents) / Test Files 2 passed, Tests 14 passed (vitest)
```

### 4.1 vision — 2026-10-24 — 5 iterations
- it1: split correctness test on the tiny conv net passed only for the cut *before* the concat — cuts inside a skip connection are two tensors, not one. Root cause: the cut enumeration looked at node boundaries, not at the set of live tensors. Rewrote `single_cut_points()` as a live-tensor sweep: a cut is valid only where exactly ONE tensor crosses the boundary **and** every consumer of the graph input is already behind it (privacy guard). The tiny net's `cat` cut is now found and tested.
- it2: `quantize_static` failed on the YOLO head with `ShapeInferenceError` → run `quant_pre_process` (symbolic shape inference) first, fall back to the raw model if the pre-processor itself fails; both paths covered.
- it3: INT8 head test passed even with a broken quantizer because cosine(fp32, fp32) = 1 → added the assertion that the INT8 output must differ from FP32 (`max_abs > 0`) so the test cannot pass by not quantizing.
- it4: Exporting yolov8n-seg: ultralytics tried to pip-install `onnxslim` (no pip in the uv venv) → export without the simplifier; split found 10 single-tensor cuts after the image's last use, chose index 10 (`/model.3/act/Mul_output_0`, `[1,64,80,80]`). Disk was 100 % full mid-export (torch) → cleaned caches, finished, then removed torch/ultralytics again (`uv sync --all-packages`).
- it5: `opencv-python-headless` files were missing from the venv after the disk-full event (dist-info present, no `cv2`) → `uv sync --reinstall-package opencv-python-headless`. Lint: E501/PLC0415/mypy in new tests → wrapped lines, hoisted imports, typed helpers (no rule weakened); Makefile runs mypy per service (duplicate `conftest`).
```
$ make test-split
uv run pytest services/edge-capture/tests -q -p no:cacheprovider -s
.......INT8 head: min cosine=0.99995 max_abs=0.1044 (fp32 split max_abs=0.00e+00)
..yolov8n-seg: INT8 head min cosine=0.99983 manifest={"fp32_split_max_abs_measured": 0.0, "fp32_split_bound": 0.0001, "int8_head_min_cosine_measured": 0.9998390518168697, "int8_head_cosine_bound": 0.99, "int8_head_mean_abs_measured": 0.09483722586635389, "eval_inputs": 4, "calibration": "synthetic_noise", "calibration_n": 16, "quantize_seconds": 2.2}
.
24 passed in 1.35s
```
Covers: cut enumeration on a conv net with a skip/concat (cuts `r0`, `r1`, `cat`; none inside the skip); `choose_cut` by index / tensor name; a graph whose last node reads the image has **no** valid cut (`SplitError`); FP32 split == unsplit to ≤ 1e-4 at every cut; INT8 head cosine ≥ 0.99 *and* ≠ FP32; `compare` rejects shape mismatch; manifest round-trip; the exported yolov8n-seg head/tail re-verified against `full_fp32.onnx` when `models/yolov8n-seg/manifest.json` exists (it does locally; `models/` is gitignored). Packet codec: uint8 error ≤ scale/2, constant tensor, zstd round-trip; deterministic letterbox; seeded synthetic source; private-host classification table; remote base refused without `ARCO_ALLOW_REMOTE_BASE`; **`test_no_raw_frame_leaves_the_edge`**: full `EdgeNode` run with a recording link, no outbound frame contains any captured image row and each decodes to the cut-shape tensor.
Real-model numbers (local, M1 Pro CPU EP, not a benchmark claim): `scripts/export_split.py --weights models/yolov8n-seg.pt --split-index 10` → head_int8 60 KB, tolerance block above; `quantize_seconds=2.2`.

### 4.2 vision — 2026-10-24 — 6 iterations
- it1: `test_homography_recovers_pinhole_ground_plane` was off by metres for far points: DLT solution had negative projective depth for valid pixels, so the horizon test rejected everything. Root cause: SVD null vector sign is arbitrary → normalise `H` so calibration points map with `w > 0`; horizon test moved from row 150 (still ground for a 14° pitch) to row 100 (sky).
- it2: blackboard returned 422 to the `HazardWriter` — it posted `content=` (raw JSON string) with no content-type → `json=h.model_dump(mode="json")`.
- it3: `WsCollector._stop` in the E2E test shadowed `threading.Thread._stop` and the thread never exited → renamed `_halt`.
- it4: test relative import `from .conftest import GEOREF_DIR` broke under the workspace rootdir → module-level path constants in each test.
- it5: polygon bounds assertion in the seg decode test was tighter than the 160×160 prototype grid can resolve → tolerance = one prototype cell (documented in the test).
- it6: after `make test-vision-e2e` passed, uvicorn printed `Cancel N running task(s)` tracebacks on teardown: the `/ws/detections` and `/ws/telemetry` handlers were send-only loops and never learned the client had closed. Fixed in `Broadcaster.serve` (concurrent receive task → `WebSocketDisconnect` promptly). Re-ran: 0 tracebacks.
```
$ make test-vision-e2e
ARCO_REQUIRE_DB=1 uv run pytest services/base-station/tests -q -p no:cacheprovider -s
REAL MODEL yolov8n-seg: head_int8=5.6ms serialize=2.4ms packet=130627B (21x smaller than raw BGR) tail=61.3ms post=1.54ms providers=['CPUExecutionProvider'] detections=0
REAL MODEL yolov8n-seg: full_fp32=67.9ms (same frame, same process)
REAL MODEL E2E 6 frames: mean stage ms={'capture': 0.0, 'head': 12.68, 'serialize': 3.38, 'network': 158.71, 'deserialize': 0.93, 'tail': 64.48, 'postprocess': 1.21, 'georef': 0.34, 'db_write': 0.0} mean total=241.7ms mean packet=130731B
VISION E2E 12 frames: wall=215ms last total=189.4ms stages={'capture': 0.0, 'head': 0.0, 'serialize': 0.01, 'network': 174.23, 'deserialize': 0.08, 'tail': 0.0, 'postprocess': 0.01, 'georef': 0.36, 'db_write': 14.71}
11 passed in 7.42s
```
Covers: homography recovery from a pinhole camera (< 5 % range error on held-out ground points), horizon + degenerate input rejected, sample `camera_front_st.json` self-consistent (RMS < 1 px), tile affine corners; YOLOv8-seg decode + class-aware NMS + mask→polygon on a fabricated output; COCO proxy aggregation (1 person → nothing, 4 → one `crowd_bottleneck`, identity map for fine-tuned names). Integration: synthetic edge (12 frames, 1280×720, real `CameraGeoref`) → `ws://…/ws/features` → base station (synthetic detector) → blackboard REST → **2 hazard rows in PostGIS** (`debris`, `smoke`, `source=simulated`, polygons inside the Lahaina bbox), `DetectionsMessage.detector == "synthetic"`, `TelemetrySample(mode="live")` with all 9 stages, Prometheus `/metrics`, `hazard.created/updated` on the blackboard WS; HTTP `POST /features` macro tile path → `fire_front` row. Real model (skipped without manifest): INT8 head → packet → real tail + FP32 baseline, and the E2E through the base station with `ARCO_DETECTOR=onnx_split`.
Note on the numbers: `network` ≈ 150–190 ms in-test is queueing from an un-paced 12-frame burst into a single-worker base station on the same host (frames wait behind each other), not link latency; the paced live run below shows ~75 ms through Colima port forwarding. On this CPU tail (61 ms) ≈ unsplit (68 ms): the split buys bandwidth (131 kB vs 2.7 MB) and edge offload, not base compute — stated in the docs, to be measured properly in Phase 5.
Live E2E against the compose stack: `docker compose up -d --wait` (new `base-station` container healthy) → host `arco_edge.main --frames 25` at 5 FPS → `/health` `frames=25 dropped=0 last_total_ms=74.2` → blackboard rows `smoke`/`debris` `cam-front-st active` → `arco-agents` log `cartographer penalized=2 compute_ms=47.0 graph_version=10` → `navigator routes=6 compute_ms=15.5`. `/metrics`: `arco_frame_total_ms_count{pipeline="int8_split",stream="micro"} 25`.
```
$ make lint   -> ruff clean, all files formatted, mypy: 30 + 19 + 15 + 13 source files no issues, eslint/prettier/tsc clean (exit 0)
$ make test   -> 127 passed (pytest) / Test Files 2 passed, Tests 14 passed (vitest)
$ make healthcheck -> postgres/redis/blackboard/base-station/agents all ok: healthcheck: OK
```
