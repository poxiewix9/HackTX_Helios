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
| 4.1  | vision     | 1.2        | Export, split, quantize                       | `make test-split`                                 | DONE        | see log 4.1 |
| 4.2  | vision     | 4.1,2.2    | Edge capture + base station + georef          | `make test-vision-e2e`                            | DONE        | see log 4.2 |
| 5.1  | benchmark  | 4.2        | FP32 vs split benchmark + recorded replay     | `make bench`                                      | DONE        | see log 5.1 |
| 6.1  | agents     | 3.2        | Consensus + trust + clustering                | `make test-consensus`                             | DONE        | see log 6.1 |
| 7.1  | simulator  | 2.2,3.2    | Scenario player + schema validation           | `make test-sim`                                   | DONE        | see log 7.1 |
| 7.2  | simulator  | 7.1        | Lahaina, Joplin, Camp Fire scenarios          | `make validate-scenarios`                         | DONE        | see log 7.2 |
| 8.1  | dashboard  | 1.2        | Dashboard shell against mock feed             | `make test-dashboard`                             | DONE        | see log 8.x |
| 8.2  | dashboard  | 8.1,7.1    | Overlays, snap animation, Three.js layers     | `make e2e-dashboard`                              | DONE        | see log 8.x |
| 8.3  | dashboard  | 5.1,8.2    | Telemetry strip + offline fallback            | `make e2e-dashboard`                              | DONE        | see log 8.x |
| 9.1  | mobile     | 1.2,6.1    | Arrow UI, CRDT, transports, simulated mesh    | `make test-mobile`                                | IN_REVIEW   | see log 9.1 |
| 10.1 | agents     | 3.2        | SLM explanations + speculative decoding       | `make test-slm`                                   | TODO        |          |
| 10.2 | integrator | all        | make demo end to end + final E2E              | `make demo-smoke`                                 | TODO        |          |

## Phase gate log
(Orchestrator appends: phase, date, gate checks run, result.)

- Phase 1 — 2026-10-24 — gate: `make lint`, `make schemas-check`, `make test`, `docker compose up -d && make healthcheck` all green; 1.1-1.3 DONE (user said "continue"). Commit `de817c8`.
- Phase 2 — 2026-10-24 — gate: `make lint`, `make test` (62 py + 14 ts), `make test-blackboard-all` (35), `docker compose up -d --wait && make healthcheck`, `make ingest-sample` all green; 2.1-2.3 DONE (user said "continue"). Commit `386e819`.
- Phase 3 — 2026-10-24 — gate: `make lint`, `make test` (92 py + 14 ts), `make test-cartographer` (15), `make test-navigator` (15), `docker compose up -d --wait && make healthcheck`, live hazard → sever → reroute → `/ws/routes` verified; 3.1-3.2 DONE (user said "continue"). Commits `cea1819`, `ed2358c`.
- Phase 4 — 2026-10-24 — gate: `make lint`, `make test` (127 py + 14 ts), `make test-split` (24), `make test-vision-e2e` (11), `make schemas-check`, `docker compose up -d --wait && make healthcheck` (base-station added), live edge → base → PostGIS → agents reroute verified; 4.1-4.2 DONE (user said "continue", and asked to run phases 5-10 in succession without further gates — Orchestrator applies the gate checks itself at each phase end). Commit `a152fd4`.

- Phase 7 — 2026-10-24 — gate: `make lint`, `ARCO_REQUIRE_DB=1 make test` (159 py + 14 ts), `make validate-scenarios` (3/3), `make test-sim` (14), `docker compose up -d --build --wait blackboard agents simulator` healthy, Lahaina at 200× through the real agents: 210 severed, 40/40 expected edges, 3 routes, consensus 5 reports / 4 reporters; 7.1-7.2 DONE (Orchestrator gate, user asked for phases to run in succession). Commit `981762c`.

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

### 5.1 benchmark — 2026-10-24 — 3 iterations
- it1: first design had `BenchmarkResult` per pipeline only, but the harness measures per stream and the contract had no `stream` field → Architect applied schema 0.2.2: `BenchmarkResult.stream: StreamKind | None` and `PipelineKind += "synthetic"` (the base station was labelling the synthetic detector `int8_split`; now `synthetic`). `make schemas` regenerated; base-station e2e test updated to the honest label.
- it2: pacing/drop model was off by one slot (counted the frame being processed as dropped) → rewritten as "arrivals that passed while busy = `ceil((now − next_arrival)/period)`", exact under a fake clock (70 ms work @ 30 fps → precisely 2 drops/frame, 10 fps).
- it3: mypy: `max(dict, key=...)` typed the bottleneck as `Stage | None`; `opencv` `imdecode` may return None; pydantic `default_factory` for `list[StreamKind]` → typed helpers, explicit None check. Docker build hit a full host disk again (Colima image I/O errors) → purged pip/npm/brew download caches (~6 GB), restarted Colima, pruned images; container builds fine afterwards.
```
$ make bench RECORD=1
run 20261024T212059Z-b8ae37  model yolov8n-seg@full_fp32 vs yolov8n-seg@head_int8 -> yolov8n-seg@tail_fp32  frames synthetic:0  paced 30.0 fps
hardware: Apple M1 Pro x8, 16.0 GB, gpu=none used, ort=CPUExecutionProvider, Darwin 25.5.0 (arm64), py3.11.14
pipeline   stream    n drop    mean     p50     p95    fps   kB/f    rss  target  bottleneck
fp32_full  micro    60  120    69.2    66.8    78.1    9.9  108.0    266  MISSED  tail
int8_split micro    60  119    68.3    65.9    83.1   10.0  127.6    360  MISSED  tail
fp32_full  macro    60  116    69.6    68.2    80.4   10.1  123.1    397  MISSED  tail
int8_split macro    60  118    68.7    67.2    75.8   10.0  209.7    396  MISSED  tail
micro: split FASTER (mean 69.22 -> 68.303 ms, x1.013), bandwidth 110615 -> 130622 B/frame (x0.85), target MISSED, bottleneck=tail
wrote services/benchmark/results/20261024T212059Z-b8ae37/{run.json,summary.csv,samples.csv}; recorded -> services/benchmark/recorded/20261024T212059Z-b8ae37.json
$ make test-benchmark   -> 8 passed (incl. real short run: fp32 73.7 ms vs split 71.8 ms, MISSED, bottleneck tail)
$ docker compose up -d --wait benchmark && curl :8005/health -> replay_run_id=20261024T212059Z-b8ae37, replay_hardware=Apple M1 Pro; /ws/telemetry frames: telemetry.sample seq 96..98 mode=recorded
$ make lint -> clean (mypy 30+19+15+13+12 files); make test -> 135 passed / 14 vitest
```
Honest result: on this CPU-only machine **both pipelines miss** 80 ms / 30 fps; bottleneck is the FP32 tail (~57–61 ms) in both; the split is ~1 % faster; its packet is *larger* than JPEG q90 at this cut (128 kB vs 108 kB). Written as such in `services/benchmark/README.md` and ARCHITECTURE. The harness reports the bottleneck instead of hiding it, as the spec requires.

### Phase 5 gate — 2026-10-24 (Orchestrator, after Reviewer pass on 5.1)
Reviewed: harness runs A (FP32 full) and B (INT8 split) on identical frames with one pacing model, reports JSON + CSV, reads hardware from the machine, replays recorded runs with `mode=recorded`, and reports the missed target + bottleneck instead of hiding it. 5.1 → DONE.

### 6.1 agents — 2026-10-24 — 4 iterations
- it1: `_hazards_near` built `BBox(west=…)` but `BBox` is an `Annotated` 4-list → pass `[w, s, e, n]`. `cryptography` added to `services/agents` deps (`uv sync`).
- it2: wired Consensus into the LangGraph: `report.*` → consensus node → (hazard written) → cartographer → navigator in the **same** run; `handled_hazard_seqs` dedupes the bus redelivery of the consensus-written `hazard.created`; `observe_hazard` on `hazard.*` is guarded so a Consensus crash never blocks the Cartographer. `AgentsRuntime` TOPICS += `report`; `/health` exposes consensus counters.
- it3: ruff E501 ×18 / PLR0917 ×2 in the reasoning strings and helpers → keyword-only helpers, wrapped strings (no rule changes). mypy on the tests: `HazardClass` is a `StrEnum`, not a Literal → typed constants + `_kind()/_cons()` helpers instead of indexing `dict | None`.
- it4: all green.
```
$ make test-consensus
ARCO_REQUIRE_DB=1 uv run pytest services/agents/tests/test_consensus.py -q -p no:cacheprovider
.........                                                                [100%]
9 passed in 5.14s
$ make lint -> ruff clean, mypy 23 (agents) + others no issues, eslint/prettier/tsc clean
$ make test -> 144 passed (pytest) / Test Files 2 passed, Tests 14 passed (vitest)
```
Covers: Ed25519 sign/verify + tamper (moved pin, swapped pubkey, swapped pubkey+hash all rejected), canonical bytes stable across JSON round-trip; ST-DBSCAN (space + time separation, chain growth, empty); trust floor/cap and sliding-window rate limit; **smoke false-negative integration**: vision-negative spot on the longest route, phone-1 pin → `report.pending` (no hazard), phone-1 again → still nothing (not independent), phone-2 → `community.hazard.created` (source=community, conf 0.6) → Cartographer penalized/severed the route edge → Navigator rerouted, all in one pipeline run; AgentEvent reasoning contains "vision-negative" and "2 independent"; bus redelivery of that hazard event does not re-run the Cartographer. Also: pins near an active vision hazard corroborate (0.6 → 0.7) instead of duplicating; two independent denials → vision hazard `disputed` with geometry intact (never deleted) and penalties released; community hazard resolved by denial quorum with reporter trust docked; forged signature and 6th/7th report in the window rejected with reasons logged; vision arriving later credits pending pins; Consensus crash isolated (`errors` recorded, Cartographer still severs).
Live against the compose stack (agents image rebuilt): two signed `POST :8001/reports` at the midpoint of Lahainaluna Road (on the longest current route) → agents log `consensus kind=report.pending` then `community.hazard.created reports=2 hazards=1` → `cartographer penalized=14 graph_version=37` → `navigator routes=6 compute_ms=16.4 trigger=graph.updated`; `/edges` shows the edge `penalized 12.5 → 25.2`; `/health` `consensus_reports_seen=2 known_reporters=2 recent_errors=[]`.

### Phase 6 gate — 2026-10-24 (Orchestrator, after Reviewer pass on 6.1)
Reviewed: signed reports (Ed25519, canonical JSON), trust ledger with floor/cap/rate limit, ST-DBSCAN clustering, quorum of 2 independent trusted reporters overrides a vision negative and triggers a reroute in one pipeline run, denials dispute (never delete) hazards. Found by the Phase 7 simulator and fixed before the gate: a stale `all_clear` targeted at a *resolved* hazard fell through to the nearest active hazard of the same class and disputed it (re-opened ~400 edges through the live fire front). `Consensus._target` is now binding on `target_hazard_id`; regression `test_stale_targeted_denial_never_retargets_another_hazard`. `make test-consensus` → `10 passed in 6.09s`. 6.1 → DONE.

### 7.1 + 7.2 simulator — 2026-10-24 — 6 iterations
- it1: `uv run` broke after adding the member (`arco-agents` missing from `tool.uv.sources`) → added `arco-agents` + `arco-simulator`. Overpass fetch for Joplin/Paradise: background shells died silently → foreground fetch; one rate-limit refusal, retried. Graphs committed under `data/samples/osm/{joplin,paradise}_drive.graphml` with ODbL sidecars.
- it2: the player stamped every event fired by a coarse `advance()` with the final `t` → merged chronological firing of timeline + community events, each at its own `t`. Backward `seek` reconciles through HTTP (resolve / re-fire / restore network), never through SQL.
- it3: routes through severed edges survived a graph version bump → `replace_routes` now retires every current route before inserting (blackboard, noted in HANDOFFS). Stale `all_clear` disputed the fire front → Consensus `_target` fix (see Phase 6 gate).
- it4: first polygons closed every exit (all cells unreachable) → reshaped the late-stage polygons against the actual OSM street coordinates and moved two population cells out of the burn footprint so each scenario keeps real corridors: Lahaina 3/6 cells routed at t=1800, Joplin 4/6, Camp Fire 2/5 (unreachable cells sit inside the fire / swath).
- it5: `expected_blocked_edges` measured by playing each scenario through the real agents (`/tmp` probe), 40-edge sample written per scenario; two tests that encoded the old polygon / empty expectations updated to the real data (no assertion weakened: the chaos test now asserts every expectation is *missing* on the fake blackboard). `InProcessAgents.drain()` loops until the outbox is quiescent. mypy strict on tests: narrowed-bool unreachable warnings → assert on fresh `state()` reads.
- it6: live compose: `POST /graph/ingest` returned 404 in-container because the blackboard image had no `data/samples/osm` and the simulator sent a host-absolute path → blackboard resolves repo-relative `graphml_file` refs against its own root (and raises `FileNotFoundError` explicitly), simulator sends the ref as written, blackboard image copies `data/samples/osm`. `Player.load(data_dir=)` parameter dropped (unused).
```
$ make validate-scenarios
OK  camp_fire_2018  wildfire  2018-11-08    1800s   13 timeline    5 community  3 cams  sources=3
OK  joplin_2011     tornado   2011-05-22    1500s   14 timeline    5 community  2 cams  sources=3
OK  lahaina_2023    wildfire  2023-08-08    1800s   13 timeline    5 community  1 cams  sources=3
3/3 scenarios valid
$ make test-sim
ARCO_REQUIRE_DB=1 uv run pytest services/simulator/tests -q -p no:cacheprovider
14 passed in 8.41s
$ uv run mypy services/simulator -> Success: no issues found in 14 source files
$ make lint -> ruff clean, mypy clean for every service, eslint/prettier/tsc clean
$ ARCO_REQUIRE_DB=1 make test -> 159 passed (pytest) / 14 passed (vitest)
$ docker compose up -d --build --wait blackboard agents simulator -> all Healthy
$ curl :8004/health -> {"status":"ok","label":"Simulated on real event geometry","blackboard":true,"agents":true,"scenario_id":"lahaina_2023",...,"last_error":null}
$ POST :8004/speed {speed:200}; POST :8004/play; 12 s later GET :8004/state
  t=1800 playing=false active_hazards=[fire-east, fire-south, smoke-lahainaluna, vehicles-front-north] reports_sent=5 events_fired=18 last_error=null
$ GET :8004/check -> expected 40 severed 210 missing 0 ok True
$ GET :8003/health -> routes=3 graph_version=168 consensus_reports_seen=5 consensus_known_reporters=4 scenario_t=1800.0 recent_errors=[]
$ GET :8001/agent-events -> hazard.applied 137, routes.recomputed 149, community.hazard.created 3, hazard.corroborated 2, hazard.disputed 1, hazard.resolved 3, hazard.expired 2
$ make healthcheck -> healthcheck: OK (postgres, redis, blackboard, base-station, benchmark, agents, simulator)
```
Covers: scenario schema + data-source/licence/approximation notes for all three; chronological firing; wall-clock speed and pause; seek reconciliation (forward fires skipped pins, backward resolves future hazards and restores cellular); deterministic hazard ids and per-phone signed keys (`verify_report` ok, one key per reporter); chaos hazard + cellular; synthetic feed watermark and overlays appearing at the scripted `t`; full Lahaina timeline through the blackboard REST API and the real LangGraph pipeline in-process asserting `hazard.applied`, `routes.recomputed`, `community.hazard.created`, `network.cellular(down)` and the final `clock.tick`.

### 8.1 + 8.2 + 8.3 dashboard — 2026-10-24 — 6 iterations
- it1: Next 15 / React 19 / Tailwind app, Zustand store with a pure `applyMessage` reducer over `@arco/schemas` `AnyMessage` (no hand-written message types), `ReconnectingSocket` (exp backoff + jitter, state surfaced), REST through Next rewrites (services have no CORS). Port 3000 was taken by an unrelated local server → `DASHBOARD_PORT` env; e2e uses 3200/3201.
- it2: MapLibre + deck.gl `MapboxOverlay` layers (hazard polygons, edges with severed flash, routes with snap width/colour animation, flow trips, population + report pins, labels) and a three.js `CustomLayerInterface` sharing the map GL context (hazard volumes, instanced crowd particles, beacons; FPS measured in `render`). `CustomLayerInterface.render` matrix typing → `ArrayLike<number>`.
- it3: ESLint strict-type-checked on a browser app: `restrict-template-expressions` override for `apps/dashboard`, floating promises on map calls (`void`), `no-unnecessary-condition` from narrowing across awaits (`isDisposed()` helper), unsafe `Mesh` access cast. `pnpm lint` + `typecheck` clean. Verified visually in a desktop Chromium against the compose stack: satellite map, flashing severed edges, routes snapping (`SNAP #13 33 ms · compute 3.7 ms`), three.js ~120 fps with 5 volumes / 700 particles / 2 beacons, synthetic street feed, agent decisions, telemetry `RECORDED` badge from the replayed benchmark run.
- it4: recorded replay `scripts/record-replay.mjs` → `public/recorded/lahaina_2023.replay.json` (1071 messages, 55 telemetry samples; edges normalised to open at t=0, stated in `notes`). Offline e2e showed `LIVE STACK`: `config.ts` read `process.env[name]` dynamically, which Next does not inline into the client bundle, so the dead-port overrides were ignored → literal `process.env.NEXT_PUBLIC_*` reads.
- it5: offline e2e still stuck at `CONNECTING` although the replay fetch returned 200. Root cause from a scratch probe (passes) vs the test (fails): Playwright **tracing** reads the three WebGL canvases back on every expect poll; under headless SwiftShader the page stalls for seconds ("GPU stall due to ReadPixels") and never runs the fallback. Trace is now off by default (`PW_TRACE=1` opts in). Live e2e: at 200× every re-route re-arms the 450 ms flash, so `fired` was never reached while playing → the test pauses the backend clock first, then asserts firing → fired; the recorded clock ticks say `playing`, so the offline test disables whichever transport button is shown and the scrub bar.
- it6: packaging — Makefile `dev-dashboard`/`test-dashboard`/`e2e-dashboard`/`record-replay`, `.env.example` NEXT_PUBLIC section, `apps/dashboard/README.md`, ARCHITECTURE "Dashboard internals" + limitations (GPU-dependent FPS, raw three.js custom layer not R3F, basemap tiles need internet, replay edges start open).
```
$ make test-dashboard
 Test Files  5 passed (5)
      Tests  20 passed (20)
$ make e2e-dashboard
  ✓  1 [live] › e2e/live.spec.ts:22:1 › plays the default scenario and the snap animation fires after a hazard event (33.8s)
  ✓  2 [live] › e2e/live.spec.ts:97:1 › chaos button: cellular kill is reflected from the network event (12.9s)
  ✓  3 [offline] › e2e/offline.spec.ts:8:1 › falls back to the recorded replay when the backend is unreachable (13.8s)
  3 passed (1.2m)
$ pnpm exec playwright test --project=live --reporter=json  (annotations)
  snap = SNAP #11 863 ms · compute 4.0 ms        # hazard→routes latency from backend timestamps, 200× with 9 active hazards
  telemetry = RECORDED                           # benchmark service replaying the committed run
  three-fps = 1.0 fps (9 hazard volumes · 700 crowd particles · 2 beacons)   # headless SwiftShader software GL; ~120 fps measured in desktop Chromium on the same laptop
$ make lint -> ruff clean, mypy clean, eslint clean, prettier clean, tsc clean (shared-schemas + dashboard)
$ ARCO_REQUIRE_DB=1 make test -> 159 passed (pytest) / 14 + 20 passed (vitest)
```
Covers: reducer semantics (hazard upsert/resolve, edge state + flash, graph version + snap latency, clock, network, telemetry ring, agent event cap); backoff curve and reconnect state machine with fake timers; replay ordering, scheduling and looping; layer colour/width math; StatusBar/ControlBar/TelemetryStrip rendering; Playwright live scenario (snap fires after a hazard event, backend clock drives the UI, LIVE/RECORDED badge, measured FPS, street feed, pause) and backend-killed replay (RECORDED REPLAY badge + banner, agent events and snaps from the recording, controls disabled, reconnect counter climbs, no page errors). Not covered: the FPS *target* — the number is reported, not asserted, because the headless renderer is software GL.

### Phase 8 gate — 2026-10-24 (Orchestrator, after Reviewer pass on 8.1–8.3)
Reviewed: message types only from `@arco/schemas`; backend clock drives the UI (clock-t asserted from `clock.tick`); snap latency computed from backend timestamps; `LIVE`/`RECORDED` read from the telemetry sample; recorded replay goes through the same reducer and is labelled `RECORDED REPLAY`; no raw video path (street feed is the simulator's synthetic MJPEG over the LAN). Evidence re-run this session: `make test-dashboard` → 20 passed; `make e2e-dashboard` → 3 passed (1.2m); `make lint` clean; `ARCO_REQUIRE_DB=1 make test` → 159 / 14 + 20. Commit `991c492`. 8.1, 8.2, 8.3 → DONE.

### 9.1 mobile — 2026-10-24 — 6 iterations
- it1: `apps/mobile` Expo SDK 58 workspace package with `src/core` kept RN-free (`@arco/mobile/core` export) so the same code runs in Node tests and the dashboard. `Transport` seam, `SimulatedMeshNetwork` (range / loss / latency / per-device cellular / partition / heal, labelled simulated), yjs `MeshDoc` (pins, routes, uploaded) with signature verification on every merge, `MeshSync` gossip (tag byte + state vector / update, flood-forward only on state change). Ed25519 signer matching `arco_agents.reports` byte-for-byte: Python fixtures generated with `arco_agents.reports` (`tests/fixtures/signed_reports.json`); first mismatch was Pydantic's microsecond `created_at` vs ISO-ms → canonicaliser normalises to ms.
- it2: `WebSocketRelayTransport` + `scripts/relay-server.mjs` (rooms, addressed/broadcast frames, peer lists). Relay test timed out: `wss.address()` read before `listening` with port 0 → `startRelay` awaits `listening`. Partition test saw `unreachable > 0` from frames still in flight when the partition was applied → the harness settles the network before partitioning and drains one latency hop after healing (assertion unchanged).
- it3: on-device fallback: `scripts/export_graph_tile.py` exports `assets/tiles/lahaina_2023.json` (`arco.tile/1`, 266 nodes / 637 edges / 2 safe zones / population cells, OSM ODbL noted in the tile) from the scenario's road graph; A\* with pin severing (40 m) and penalties (120 m, ×4). Reroute test returned null when the pin sat on the single junction every path shares → pin placed at the quarter point (verified: detour 1085 m, min distance to pin 63 m). Guidance (snap, bearing, turn, arrive/off-route) + simulated walker; coarse-sampled re-anchor got stuck → exact segment projection.
- it4: Expo app (`src/phone`: `useDevice` hook over `MeshNode`, `ArrowScreen` with connectivity badge `cell | mesh | isolated`, arrow, provenance label NAVIGATOR vs ON-DEVICE FALLBACK, pin picker, cellular toggle, radio availability reasons, `SIMULATION_LABEL · position SIMULATED`). Metro needed `isomorphic-webcrypto` for lib0; `src/app` was auto-detected as an Expo Router root → renamed `src/phone`. `expo export --platform ios` → 701 modules bundled (Node 20 warning: Expo wants ≥ 22.13). BLE / Wi-Fi Direct are stubs whose `availability()` states why (documented in `apps/mobile/README.md`).
- it5: dashboard "Community mesh" panel (`MeshDemo` + R3F/drei): gateway with real `Uplink` to the blackboard and optional relay bridge, 3 in-range phones, a bridge and 2 phones only reachable through it; cellular / partition / loss / drop-pin controls. React 19 + R3F 9 needed a `ThreeElements` JSX augmentation. `MeshDemo` unit test: pin from an isolated phone reaches the gateway with cellular off, uploads exactly once when cellular returns (signature verified in the fake fetch).
- it6: `make lint` failed only under ESLint (`no-unsafe-assignment` on `dynamic(() => import(MapView))`) although `tsc` passed: after the mobile install pnpm no longer hoisted `@types/react` into `.pnpm/node_modules`, so `next/dynamic`'s `import React from 'react'` resolved to an error type that `skipLibCheck` hid from tsc. Fixed by declaring `@types/react ~19.3.0` at the workspace root (single version everywhere). Next had rewritten `apps/dashboard/tsconfig.json` again (dev server) → restored. Live e2e extended with the mesh path; `mesh-stats` exposes `data-pins`/`data-uploaded`. Panel row enlarged (labels were overlapping at 15rem); verified visually in a 1800×1000 Playwright Chromium.
```
$ make test-mobile
 Test Files  6 passed (6)
      Tests  30 passed (30)
$ make test-dashboard
 Test Files  6 passed (6)
      Tests  22 passed (22)
$ make e2e-dashboard
  ✓  1 [live] › e2e/live.spec.ts:22:1 › plays the default scenario and the snap animation fires after a hazard event (40.9s)
  ✓  2 [live] › e2e/live.spec.ts:97:1 › mesh panel: a pin dropped on an isolated phone reaches the gateway over the mesh and uploads when cellular returns (32.8s)
  ✓  3 [live] › e2e/live.spec.ts:143:1 › chaos button: cellular kill is reflected from the network event (21.9s)
  ✓  4 [offline] › e2e/offline.spec.ts:8:1 › falls back to the recorded replay when the backend is unreachable (33.3s)
  4 passed (2.9m)
$ pnpm --filter @arco/mobile exec expo export --platform ios --output-dir .expo-export
iOS Bundled 7903ms index.ts (701 modules)
$ make lint -> ruff clean, mypy clean, eslint clean, prettier clean, tsc clean (shared-schemas + mobile + dashboard)
$ ARCO_REQUIRE_DB=1 make test -> 159 passed (pytest) / 14 + 30 + 22 passed (vitest)
```
Covers (role EXIT): arrow UI renders from a route (ArrowScreen bound to `activeRoute()`, bundled by Metro); CRDT sync converges with packet loss (35 % loss, 3 hops); `SimulatedMeshTransport` partition-and-heal; a pin dropped on one device appears on another with cellular disabled (mesh test + live e2e); on-device fallback reroutes around a pinned hazard on the real Lahaina tile; `Transport` is the only seam; both required transports fully tested; pins are signed `CommunityReport`s verified by the Python side. Not covered: BLE / Wi-Fi Direct radios (stubs with stated reasons), real GPS (position simulated), a physical two-phone run (relay path exercised by tests, not by hardware in this session).
