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

## Quick start (Phase 1)

```bash
make bootstrap          # uv sync, pnpm install, .env, pre-commit hooks
docker compose up -d --wait postgres redis
make migrate            # Alembic -> PostGIS + pgRouting extensions
make healthcheck        # containers healthy, extensions present, Redis PONG
make lint               # ruff + mypy --strict + eslint + prettier + tsc
make test               # pytest (unit + integration) + vitest
make schemas            # regenerate JSON Schema + TS types from Pydantic
make schemas-check      # fail if generated files are stale
```

`docker compose up -d --wait` also runs the `blackboard-migrate` one-shot container, so a
fresh machine needs no host-side Python to get a migrated database.

## Repository layout

```
packages/shared-schemas/   Pydantic models -> JSON Schema -> TS types (the contract)
services/blackboard/       PostGIS schema, Alembic migrations, event bus, spatial API
services/{edge-capture,base-station,agents,simulator,benchmark}/   (later phases)
apps/{dashboard,mobile}/   Next.js dashboard, Expo community app (later phases)
data/{scenarios,samples}/  scenario JSON + small sample assets
scripts/                   healthcheck, loop_check (agent hook), export/quantize (later)
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
