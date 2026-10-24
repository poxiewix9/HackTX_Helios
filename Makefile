# ARCO monorepo Makefile. Every task on TASKS.md has an exit command defined here.
SHELL := /bin/bash
.DEFAULT_GOAL := help

UV      ?= uv
PNPM    ?= pnpm
COMPOSE ?= docker compose
PY_DIRS := packages/shared-schemas/python packages/shared-schemas/tests services/blackboard services/agents services/edge-capture services/base-station services/benchmark services/simulator scripts/export_split.py scripts/finetune.py

# Load .env if present so DATABASE_URL etc. are available to host-side commands.
ifneq (,$(wildcard .env))
  include .env
  export
endif

.PHONY: help
help: ## List targets
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
.PHONY: bootstrap
bootstrap: ## Install Python (uv) + JS (pnpm) deps, pre-commit hooks, .env
	$(UV) sync --all-packages
	$(PNPM) install --frozen-lockfile || $(PNPM) install
	@[ -f .env ] || cp .env.example .env
	-$(UV) run pre-commit install --install-hooks >/dev/null 2>&1 || echo "pre-commit install skipped (no git hooks dir?)"
	@echo "bootstrap: done"

# ---------------------------------------------------------------------------
# Shared schemas (single source of truth: Pydantic -> JSON Schema -> TS)
# ---------------------------------------------------------------------------
.PHONY: schemas
schemas: ## Regenerate JSON Schema + TypeScript types from Pydantic models
	$(UV) run python -m arco_schemas.export --out packages/shared-schemas/schema
	$(PNPM) --filter @arco/schemas generate
	$(PNPM) exec prettier --write "packages/shared-schemas/ts/src/generated/**/*.ts" >/dev/null

.PHONY: schemas-check
schemas-check: ## Fail if generated schema/TS files are stale relative to Pydantic models
	$(UV) run python -m arco_schemas.export --out packages/shared-schemas/schema --check
	$(PNPM) --filter @arco/schemas generate
	$(PNPM) exec prettier --write "packages/shared-schemas/ts/src/generated/**/*.ts" >/dev/null
	@git diff --exit-code -- packages/shared-schemas/schema packages/shared-schemas/ts/src/generated \
		|| (echo "schemas-check: generated files are stale; run 'make schemas' and commit" && exit 1)
	@echo "schemas-check: up to date"

# ---------------------------------------------------------------------------
# Quality
# ---------------------------------------------------------------------------
.PHONY: lint
lint: lint-py lint-ts ## Ruff + mypy + ESLint + Prettier

.PHONY: lint-py
lint-py:
	$(UV) run ruff check $(PY_DIRS)
	$(UV) run ruff format --check $(PY_DIRS)
	# agents separately: both services have tests/conftest.py (mypy duplicate-module)
	$(UV) run mypy packages/shared-schemas/python services/blackboard
	$(UV) run mypy services/agents
	$(UV) run mypy services/edge-capture scripts/export_split.py scripts/finetune.py
	$(UV) run mypy services/base-station
	$(UV) run mypy services/benchmark
	$(UV) run mypy services/simulator

.PHONY: lint-ts
lint-ts:
	$(PNPM) lint
	$(PNPM) typecheck

.PHONY: format
format: ## Auto-format Python and TS
	$(UV) run ruff check --fix $(PY_DIRS)
	$(UV) run ruff format $(PY_DIRS)
	$(PNPM) format

.PHONY: typecheck
typecheck: ## mypy + tsc only
	$(UV) run mypy packages/shared-schemas/python services/blackboard
	$(UV) run mypy services/agents
	$(UV) run mypy services/edge-capture scripts/export_split.py scripts/finetune.py
	$(UV) run mypy services/base-station
	$(UV) run mypy services/benchmark
	$(UV) run mypy services/simulator
	$(PNPM) typecheck

.PHONY: test
test: test-py test-ts ## All unit + (if DB reachable) integration tests

.PHONY: test-py
test-py:
	$(UV) run pytest

.PHONY: test-ts
test-ts:
	$(PNPM) test

.PHONY: test-blackboard
test-blackboard: ## Blackboard tables/repo/migration tests; requires `make db-up` (DB-down is a failure)
	ARCO_REQUIRE_DB=1 $(UV) run pytest services/blackboard/tests/test_migrations.py services/blackboard/tests/test_tables.py -q

.PHONY: test-blackboard-events
test-blackboard-events: ## Event bus (outbox + Redis + NOTIFY) + REST/WebSocket API tests; requires `make db-up`
	ARCO_REQUIRE_DB=1 $(UV) run pytest services/blackboard/tests/test_bus.py services/blackboard/tests/test_api.py -q

.PHONY: test-blackboard-all
test-blackboard-all: ## Every blackboard test incl. ingestion; requires `make db-up`
	ARCO_REQUIRE_DB=1 $(UV) run pytest services/blackboard/tests -q

.PHONY: test-cartographer
test-cartographer: ## 3.1 Cartographer: policy math + sever/penalize/restore on the Lahaina graph (needs DB)
	ARCO_REQUIRE_DB=1 $(UV) run pytest services/agents/tests/test_cartographer.py -q

.PHONY: test-navigator
test-navigator: ## 3.2 Navigator: A*, dispersal, measured <1s reroute + LangGraph pipeline (needs DB)
	ARCO_REQUIRE_DB=1 $(UV) run pytest services/agents/tests/test_navigator.py services/agents/tests/test_pipeline.py -q -s

.PHONY: test-consensus
test-consensus: ## 6.1 Consensus: signing, clustering, trust, smoke false-negative override (needs DB)
	ARCO_REQUIRE_DB=1 $(UV) run pytest services/agents/tests/test_consensus.py -q -p no:cacheprovider

.PHONY: test-agents
test-agents: ## Every agents test
	ARCO_REQUIRE_DB=1 $(UV) run pytest services/agents/tests -q

.PHONY: test-split
test-split: ## 4.1 ONNX split / INT8 quantization / packet codec / privacy guard (no DB)
	$(UV) run pytest services/edge-capture/tests -q -s

.PHONY: test-vision-e2e
test-vision-e2e: ## 4.2 edge -> base station -> PostGIS hazard + telemetry (needs DB)
	ARCO_REQUIRE_DB=1 $(UV) run pytest services/base-station/tests -q -s

.PHONY: test-vision
test-vision: test-split test-vision-e2e ## All Phase 4 tests

.PHONY: export-split
export-split: ## Export + split + INT8 quantize a YOLOv8 model (needs: uv sync --package arco-edge-capture --extra export --inexact)
	$(UV) run python scripts/export_split.py --weights $(or $(WEIGHTS),models/yolov8n-seg.pt) --imgsz 640 --split-index $(or $(SPLIT_INDEX),10)

.PHONY: serve-base-station
serve-base-station: ## Run the base station host-side (:8002)
	cd services/base-station && $(UV) run uvicorn arco_base.api:app --host 0.0.0.0 --port $${BASE_STATION_PORT:-8002} --reload

.PHONY: run-edge
run-edge: ## Run the edge node host-side against ARCO_BASE_STATION_URL (FRAMES=N to stop)
	cd services/edge-capture && $(UV) run python -m arco_edge.main $(if $(FRAMES),--frames $(FRAMES),)

# ---------------------------------------------------------------------------
# Benchmark (Phase 5)
# ---------------------------------------------------------------------------
.PHONY: bench
bench: ## FP32 full vs INT8 split on identical frames -> JSON + CSV (needs models/<id>/manifest.json; FRAMES, PACE_FPS, RECORD=1)
	$(UV) run arco-bench run --frames $(or $(FRAMES),60) --pace-fps $(or $(PACE_FPS),30) $(if $(RECORD),--record,) --out services/benchmark/results

.PHONY: bench-show
bench-show: ## Print the newest recorded run (or FILE=path)
	$(UV) run arco-bench show $(or $(FILE),$(shell ls -t services/benchmark/recorded/*.json | head -1))

.PHONY: test-benchmark
test-benchmark: ## Benchmark unit tests (+ a short real run when model files exist)
	$(UV) run pytest services/benchmark/tests -q -p no:cacheprovider -s

.PHONY: serve-benchmark
serve-benchmark: ## Telemetry WS :8005 replaying the newest recorded run (POST /run for a live one)
	cd services/benchmark && $(UV) run arco-bench serve --port $${BENCHMARK_PORT:-8005}

# ---------------------------------------------------------------------------
# Simulator (Phase 7)
# ---------------------------------------------------------------------------
.PHONY: validate-scenarios
validate-scenarios: ## 7.2 Validate every data/scenarios/*.json against the Scenario schema + referenced files
	$(UV) run arco-sim validate

.PHONY: test-sim
test-sim: ## 7.1 Scenario player unit tests + in-process Lahaina run through the real agents (needs DB)
	ARCO_REQUIRE_DB=1 $(UV) run pytest services/simulator/tests -q -p no:cacheprovider

.PHONY: serve-simulator
serve-simulator: ## Run the simulator API on $$SIMULATOR_PORT (default 8004) against ARCO_BLACKBOARD_URL / ARCO_AGENTS_URL
	$(UV) run uvicorn arco_sim.api:app --host 0.0.0.0 --port $${SIMULATOR_PORT:-8004} --reload --reload-dir services

.PHONY: play-scenario
play-scenario: ## Play SCENARIO (default lahaina_2023) headless at SPEED into the running blackboard, then check expected_blocked_edges
	$(UV) run arco-sim play --scenario $(or $(SCENARIO),lahaina_2023) --speed $(or $(SPEED),60) --check

.PHONY: render-feed
render-feed: ## Render a synthetic street-camera clip: SCENARIO, CAMERA, OUT (mp4), DURATION
	$(UV) run arco-sim render-feed --scenario $(or $(SCENARIO),lahaina_2023) --camera $(or $(CAMERA),cam-front-st) --out $(or $(OUT),data/cache/feed.mp4) --duration $(or $(DURATION),30)

# ---------------------------------------------------------------------------
# Dashboard (Phase 8)
# ---------------------------------------------------------------------------
.PHONY: dev-dashboard
dev-dashboard: ## Next.js dev server on $$DASHBOARD_PORT (default 3000) against the host-side service ports
	$(PNPM) --filter @arco/dashboard dev

.PHONY: test-dashboard
test-dashboard: ## 8.1 Dashboard unit tests (store reducer, reconnecting socket, replay player, layer math, components)
	$(PNPM) --filter @arco/dashboard test

.PHONY: e2e-dashboard
e2e-dashboard: ## 8.2 Playwright: live scenario + snap animation (needs compose stack) and backend-killed recorded replay
	$(PNPM) --filter @arco/dashboard e2e

.PHONY: record-replay
record-replay: ## Re-record the offline fallback (apps/dashboard/public/recorded/<id>.replay.json) from the running stack after `make play-scenario`
	$(PNPM) --filter @arco/dashboard record-replay

# ---------------------------------------------------------------------------
# Agents service (host side)
# ---------------------------------------------------------------------------
.PHONY: serve-agents
serve-agents: ## Run the agents API (Cartographer + Navigator) on $$AGENTS_PORT (default 8003)
	$(UV) run uvicorn arco_agents.api:app --host 0.0.0.0 --port $${AGENTS_PORT:-8003} --reload --reload-dir services

# ---------------------------------------------------------------------------
# Blackboard service (host side)
# ---------------------------------------------------------------------------
SAMPLE_GRAPHML := data/samples/osm/lahaina_drive.graphml

.PHONY: ingest-sample
ingest-sample: ## Load the committed Lahaina OSM sample (offline, real OSM geometry) into road_edges
	$(UV) run arco-blackboard-ingest --graphml $(SAMPLE_GRAPHML)

.PHONY: ingest-bbox
ingest-bbox: ## Download a road graph from OSM for BBOX="west,south,east,north" and ingest it
	@[ -n "$(BBOX)" ] || (echo "usage: make ingest-bbox BBOX=west,south,east,north" && exit 1)
	$(UV) run arco-blackboard-ingest --bbox "$(BBOX)" --save-graphml data/cache/osm_$(subst $(comma),_,$(BBOX)).graphml

.PHONY: serve-blackboard
serve-blackboard: ## Run the blackboard API on $$BLACKBOARD_PORT (default 8001) with reload
	$(UV) run uvicorn arco_blackboard.api:app --host 0.0.0.0 --port $${BLACKBOARD_PORT:-8001} --reload --reload-dir services

comma := ,

# ---------------------------------------------------------------------------
# Infra
# ---------------------------------------------------------------------------
.PHONY: db-up
db-up: ## Start Postgres/PostGIS + Redis only and wait until healthy
	$(COMPOSE) up -d --wait postgres redis

.PHONY: db-down
db-down: ## Stop infra containers (keeps volumes)
	$(COMPOSE) stop postgres redis

.PHONY: up
up: ## Start every compose service
	$(COMPOSE) up -d --wait

.PHONY: down
down: ## Stop and remove compose services (keeps volumes)
	$(COMPOSE) down

.PHONY: nuke
nuke: ## Stop compose services AND delete volumes (destroys DB data)
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Tail compose logs
	$(COMPOSE) logs -f --tail=100

.PHONY: healthcheck
healthcheck: ## Verify Postgres has PostGIS + pgRouting and Redis answers PING
	./scripts/healthcheck.sh

.PHONY: migrate
migrate: ## Run Alembic migrations against DATABASE_URL (host side)
	cd services/blackboard && $(UV) run alembic upgrade head

.PHONY: clean
clean: ## Remove caches and build artefacts
	rm -rf .mypy_cache .ruff_cache .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	find . -name dist -type d -prune -not -path "*/node_modules/*" -exec rm -rf {} + 2>/dev/null || true
