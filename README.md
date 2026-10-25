# Helios — Autonomous Relief Corridor Orchestrator

Helios is a live operations console for disaster evacuation. It watches hazards on a real road network, cuts streets that are no longer safe, and draws routes that spread people out instead of funneling them into the same corridor. Neighbors can still pass those routes around when cell service is gone.

Built for HackTX. The services in this repo use the internal name ARCO (Autonomous Relief Corridor Orchestrator). The full specification is in `PROJECT_SPEC.md`. Data flow and limits are in `docs/ARCHITECTURE.md`.

The demo runs on **real event geometry** from Lahaina 2023, Joplin 2011, and the 2018 Camp Fire. It is a simulation of that geometry, not a replay of live inference from those disasters.

## What you see

- A satellite map with hazard polygons, severed roads, and evacuation routes that snap as soon as the graph changes.
- Street cameras playing real footage, covered in tracking boxes for people, vehicles, cycles, and buses. The boxes are scripted so the view works without a live detector on those clips.
- A decision feed from three agents: Cartographer (what is blocked), Navigator (where to send people), and Consensus (which community reports to trust).
- A latency strip comparing a full FP32 vision pipeline with a split INT8 pipeline that keeps raw video on the local network.
- A community mesh: phones hand hazard pins toward a gateway and upload them when cellular comes back.

## How it fits together

1. Satellite and street streams turn hazards into map polygons. Raw frames never leave the machine.
2. A PostGIS blackboard holds the road graph, hazards, and routes.
3. Agents sever or penalize edges and compute dispersing routes. Pathfinding is ordinary weighted search. A small local model only writes explanations, and the system still routes with that model off.
4. The Next.js dashboard and an Expo phone app follow the same WebSocket feed. If the backend dies, the dashboard replays a recorded run and says so.

## Run it

You need Python 3.11, [uv](https://docs.astral.sh/uv/), Node 20, pnpm 10, Docker Compose, and GNU make.

```bash
make bootstrap                 # install deps, copy .env, install hooks
docker compose up -d --wait    # Postgres, Redis, blackboard, agents, simulator, benchmark
make demo                      # Lahaina, end to end, dashboard included
```

`make demo-stop` stops the dashboard and mesh relay. `make help` lists every target (tests, headless scenario player, mobile, benchmarks).

Dashboard only, once the stack is up:

```bash
make dev-dashboard             # http://localhost:3000
```

## Layout

```
packages/shared-schemas/   the message contract (Pydantic → JSON Schema → TypeScript)
services/blackboard/       PostGIS graph, hazards, event bus
services/agents/           Cartographer, Navigator, Consensus
services/edge-capture/     on-edge vision head; raw video stays here
services/base-station/     finishes inference, writes hazards
services/simulator/        scenario clock, chaos buttons, synthetic feeds
services/benchmark/        FP32 vs INT8 timings, recorded for the demo
apps/dashboard/            Helios console (map, cameras, mesh, telemetry)
apps/mobile/               Expo app: arrows, signed pins, offline mesh
data/scenarios/            Lahaina, Joplin, Camp Fire
```
