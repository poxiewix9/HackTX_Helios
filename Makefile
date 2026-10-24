# ARCO monorepo Makefile. Every task on TASKS.md has an exit command defined here.
SHELL := /bin/bash
.DEFAULT_GOAL := help

UV      ?= uv
PNPM    ?= pnpm
COMPOSE ?= docker compose
PY_DIRS := packages/shared-schemas/python packages/shared-schemas/tests services/blackboard

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
	$(UV) run mypy packages/shared-schemas/python services/blackboard/arco_blackboard

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
	$(UV) run mypy packages/shared-schemas/python services/blackboard/arco_blackboard
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
test-blackboard: ## Blackboard service tests (integration tests need `make db-up`)
	$(UV) run pytest services/blackboard/tests -m "integration or not integration"

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
