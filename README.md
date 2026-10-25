# ARCO — Autonomous Relief Corridor Orchestrator

Decentralised, edge-optimised evacuation routing: satellite + street-camera hazards ->
PostGIS blackboard -> multi-agent edge severing and crowd-dispersing routes -> dashboard
and an offline community mesh. See `PROJECT_SPEC.md` for the full specification and
`docs/ARCHITECTURE.md` for the data-flow diagram and limitations.

> Demo content is **simulated on real event geometry** (Lahaina 2023, Joplin 2011,
> Camp Fire 2018). It is not a replay of real inference output.

## Prerequisites

- Python 3.11 + [`uv`](https://docs.astral.sh/uv/) (`brew install uv`)
- Node 20 + pnpm 10 (`corepack enable`)
- Docker Engine + Compose v2. Docker Desktop or Colima both work:
  `brew install colima docker docker-compose && colima start`
- GNU make

## Quick start (Phases 1–7)

```bash
make bootstrap          # uv sync, pnpm install, .env, pre-commit hooks
docker compose up -d --wait   # postgres+redis, migrate one-shot, blackboard :8001, base-station :8002, agents :8003, simulator :8004, benchmark :8005
make healthcheck        # containers healthy, extensions present, Redis PONG, every /health ok
make ingest-sample      # Lahaina OSM road graph (offline sample) -> road_edges
curl "localhost:8001/edges?bbox=-156.7,20.86,-156.66,20.9" | head -c 300
make lint               # ruff + mypy --strict + eslint + prettier + tsc
make test               # pytest (unit + integration) + vitest
make test-blackboard-all      # blackboard tables, bus, REST/WS, ingestion (needs DB)
make test-cartographer && make test-navigator   # Phase 3 agents (needs DB; isolated <db>_test)
make test-split         # Phase 4 ONNX split / INT8 / packet codec / no-raw-frame guard
make test-vision-e2e    # Phase 4 frame -> base station -> PostGIS hazard row + telemetry (needs DB)
make run-edge FRAMES=25 # synthetic edge node against the compose base station (ARCO_DETECTOR=synthetic)
make export-split WEIGHTS=models/yolov8n-seg.pt   # real model: head_int8 + tail + manifest (needs --extra export)
make bench              # Phase 5 FP32 full vs INT8 split on identical frames -> JSON/CSV (needs the model)
make bench-show         # table of the committed recorded run (M1 Pro CPU: both miss 80 ms/30 fps, tail-bound)
make test-consensus     # Phase 6 signed reports, trust, ST-DBSCAN, smoke false-negative override (needs DB)
make validate-scenarios # Phase 7 data/scenarios/*.json schema + referenced files
make test-sim           # Phase 7 player tests + Lahaina run through the real agents in-process (needs DB)
curl -s -X POST localhost:8004/speed -H 'content-type: application/json' -d '{"speed":60}'; curl -s -X POST localhost:8004/play
make play-scenario SCENARIO=joplin_2011 SPEED=120   # headless CLI player + expected_blocked_edges check
make dev-dashboard      # Phase 8 dashboard on :${DASHBOARD_PORT:-3000} (map, snap animation, 3D, telemetry, agent feed)
make test-dashboard     # Phase 8 vitest (reducer, reconnect, replay, layers, components)
make e2e-dashboard      # Phase 8 Playwright: live scenario + snap (needs the stack), mesh pin → upload, backend-killed recorded replay
make test-mobile        # Phase 9 mobile core: signing (Python cross-check), CRDT under loss, partition-and-heal, relay, on-device reroute
make mesh-relay         # Phase 9 WebSocket relay (:8787) that phones + the dashboard's simulated gateway join
make dev-mobile         # Phase 9 Expo dev server (Expo Go); see apps/mobile/README.md for EXPO_PUBLIC_* and the BLE/Wi-Fi Direct caveats
make test-slm           # Phase 10 SLM explanations: fact guard, fallbacks, speculative-decoding labels (no model required)
make slm-bench          # Phase 10 measure a real local SLM (exit 2 if none is running)
make demo               # Phase 10 full Lahaina end to end, no manual steps (compose + dashboard + play + follow)
make demo-smoke         # Phase 10 host-side E2E against the running stack (200×)
make demo-stop          # stop the dashboard + mesh relay started by `make demo`
make schemas            # regenerate JSON Schema + TS types from Pydantic
make schemas-check      # fail if generated files are stale
```

`docker compose up -d --wait` runs the `blackboard-migrate` one-shot before starting the
`blackboard` API, so a fresh machine needs no host-side Python to get a migrated database.
Stream events: `websocat "ws://localhost:8001/ws/blackboard?since_seq=0"`; routes and
agent decisions: `websocat ws://localhost:8003/ws/routes`. Vision: `websocat ws://localhost:8002/ws/telemetry` /
`/ws/detections`; `curl localhost:8002/metrics`. With no model files the base station runs the
labelled synthetic detector; `docker compose --profile vision up -d edge-capture` adds a
synthetic edge node. Tests use an isolated
`arco_test` database + `blackboard-test.` Redis prefix, so they never wipe the dev stack.

## Repository layout

```
packages/shared-schemas/   Pydantic models -> JSON Schema -> TS types (the contract)
services/blackboard/       PostGIS schema, Alembic migrations, event bus, spatial API
services/agents/           LangGraph Cartographer + Navigator, penalties.yaml, explainer (SLM optional)
services/edge-capture/     ONNX split + INT8 head, feature-packet codec, frame sources, edge node
services/base-station/     tail inference, seg postprocess, class map, georef, hazard writer, telemetry API
services/benchmark/        A-vs-B harness, pacing/drop model, HardwareInfo, recorded replay on :8005
services/simulator/        scenario player (clock/seek/chaos), synthetic street feeds, arco-sim CLI on :8004
apps/dashboard/            Next.js 15 ops dashboard: MapLibre + deck.gl + three.js, snap animation, telemetry, offline replay
apps/mobile/               Expo community app: arrow UI, signed pins, yjs CRDT mesh (simulated + WebSocket relay), on-device fallback router
data/{scenarios,samples}/  Lahaina/Joplin/Camp Fire scenarios + OSM samples (ODbL) + vision georefs
models/                    exported ONNX heads/tails + manifest.json (gitignored; make export-split)
scripts/                   healthcheck, demo.sh, slm_bench.py, export_graph_tile.py, export_split.py, finetune.py
docs/                      ARCHITECTURE.md
```

## Multi-agent build workflow

This repo is built by role agents coordinated through shared files:

- `PROJECT_SPEC.md` — the spec; `TASKS.md` — task board with exit commands and evidence;
  `HANDOFFS.md` — the only inter-agent channel.
- `.cursor/rules/00-global.mdc` — rules for every agent (loop protocol, honesty rules).
- `.cursor/rules/roles/*.mdc` — one file per role with OWNED paths and exit criteria.
- `.cursor/commands/{tick,work,review}.md` — `/tick` (orchestrator), `/work <role>`,
  `/review` (reviewer).
- `.cursor/hooks.json` + `scripts/loop_check.sh` — optional stop-hook that keeps a role
  agent looping on its IN_PROGRESS task until the exit command passes (6 iterations per
  task, 25 per session). Opt in per checkout with `touch .cursor/autoloop.enabled`.

Run Phase 1 and 2 serially (Architect, then Blackboard). After the Phase 2 gate, run
Vision, Agents, Simulator and Dashboard in parallel worktrees (`git worktree add`), each
touching only its OWNED paths.

## Make targets

Run `make help` for the list. Each `TASKS.md` row's exit command is a make target or a
`pnpm --filter` command.
