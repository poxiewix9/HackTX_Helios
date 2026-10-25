# Helios — Autonomous Relief Corridor Orchestrator

Helios is a live evacuation system for a city that is already on fire, flooded, or torn apart. It watches the map and the street at the same time, turns what it sees into hazards on a real road network, and redraws routes so people do not all pile into the same corridor. When cell service drops, phones keep passing those routes to each other.

Built for HackTX. Scenarios use real OpenStreetMap geometry from Lahaina (2023), Joplin (2011), and the Camp Fire (2018). The demo is simulated on that geometry. It is labeled as such in the console.

## What it does

Satellite and aerial imagery are the **macro** stream: fire fronts, smoke, floodwater, collapsed blocks, tornado paths, blocked roads. Street cameras are the **micro** stream: debris, fire, smoke, flooded pavement, crowd bottlenecks, vehicle blockages, downed lines. Both become polygons in WGS84.

Those polygons land on a shared spatial blackboard. Three agents share that board:

- **Cartographer** intersects hazards with the road graph and severs or penalizes edges. Penalties grow with confidence and with time, from `penalties.yaml`.
- **Navigator** recomputes several dispersing routes with weighted A*, so a new blockage snaps the corridors instead of sending everyone down the same street.
- **Consensus** scores community reports, clusters them, and will not let one phone invent a hazard the rest of the mesh disagrees with.

A phone on the mesh can drop a pin, carry it across a partition, and upload it when any gateway sees cellular again. The console shows the map, the cameras with detection boxes, the agent reasoning, pipeline latency, and the mesh in one place.

## Stack

One repo. Python services live in a `uv` workspace (3.11). The dashboard and phone app live in a pnpm workspace. Docker Compose runs Postgres, Redis, and the services. Contracts are Pydantic models in `packages/shared-schemas`, exported to JSON Schema and generated TypeScript so nobody hand-writes a second copy of a message.

### Vision — edge and base station

The detector is split so a camera node never ships raw frames.

| Piece | Where it runs | What it is |
| --- | --- | --- |
| Model | edge + base | YOLOv8 (nano/small) and MobileNetV2, exported from PyTorch to ONNX |
| Head | edge-capture | First layers, INT8, ONNX Runtime. Activations are quantized, packed with msgpack, and compressed with zstd |
| Tail | base-station | Remaining layers (FP16/INT8). TensorRT is used when the machine has it, otherwise ONNX Runtime |
| Geometry | base-station | Masks become contours, then Shapely polygons. Street cameras use a homography; satellite tiles use an affine into WGS84 |
| Transport | local network only | WebSocket feature packets. There is no endpoint that uploads raw video |

A full FP32 pipeline is kept beside the split INT8 path. `services/benchmark` runs both on the same frames and records stage timings (capture, head, serialize, network, tail, postprocess, georef, database). The console plots that comparison against an 80 ms / 30 fps target and badges each sample `LIVE` or `RECORDED`.

### Blackboard — the map's memory

PostgreSQL 16, PostGIS 3, and pgRouting. SQLAlchemy 2, GeoAlchemy2, and Alembic own the schema. Redis pub/sub fans events out; if Redis is down, Postgres `LISTEN/NOTIFY` is the fallback.

Tables that matter: `road_edges`, `hazards`, `edge_hazard_links`, `routes`, `safe_zones`, `community_reports`, `agent_events`. Every write is an event (`hazard.upserted`, `graph.updated`, `routes.updated`, `report.created`) with a sequence number, so clients reconnect from where they left off. Road graphs are extracted from OpenStreetMap with OSMnx and stored for the three scenarios.

The blackboard is the only place agents and the UI agree. Nothing routes off a private copy of the city.

### Agents

LangGraph runs Cartographer, Navigator, and Consensus on one shared state. The graph they search is NetworkX, with edge weights from the blackboard.

Pathfinding does not depend on a language model. A* with penalty weights is the route. An optional local SLM (Ollama or llama.cpp, about 1–3B, Qwen2.5 or Llama 3.2) only writes the explanation, breaks ties, and talks about dispersal. Speculative decoding is used when the runtime supports it. Pull the model and routes still compute.

Consensus keeps a trust score per device, clusters reports with ST-DBSCAN, and can override a smoked-out false negative when enough independent phones agree.

### Console

Next.js 15 (App Router, TypeScript, Tailwind). MapLibre GL draws the basemap. deck.gl draws hazard polygons, edge states, and route lines. A three.js layer, locked to the map camera, draws hazard volumes, crowd particles, and beacons. React Three Fiber draws the mesh graph.

Zustand holds the world. WebSockets to the blackboard, the agents, and the benchmark reconnect with backoff. uPlot draws the latency strip. If the backend is gone, the same reducer replays a recorded scenario and the header switches to `RECORDED REPLAY`. The clock on screen is the scenario clock from the simulator, not a timer in the browser.

### Phones and the mesh

Expo (TypeScript). The phone shows a heading arrow and accepts a signed hazard pin. Pins and routes sync with a Yjs CRDT. The transport is an interface: BLE and Wi-Fi Direct where the device has them, and a WebSocket relay for the demo that drops packets, partitions the group, and cuts cellular on command. An on-device A* fallback still produces a route when the phone cannot reach the base station. Reports are signed; the gateway uploads them only while the backhaul is up.

### Simulator

`services/simulator` is the scenario player: load, play, pause, seek, and speed, plus a chaos button that drops a hazard polygon or kills cellular. It fires the Lahaina, Joplin, and Camp Fire timelines through the real blackboard and the real agents. The console's scrubber follows that clock.

## Run it

Python 3.11, [uv](https://docs.astral.sh/uv/), Node 20, pnpm 10, Docker Compose, GNU make.

```bash
make bootstrap                 # uv sync, pnpm install, .env, git hooks
docker compose up -d --wait    # Postgres, Redis, migrate, blackboard, agents, simulator, benchmark
make demo                      # Lahaina, end to end, console included
```

`make demo-stop` stops the console and the mesh relay. `make help` lists tests, the headless scenario player, the phone app, and the benchmark.

Console only, once Compose is up:

```bash
make dev-dashboard             # http://localhost:3000
```

## Layout

```
packages/shared-schemas/   Pydantic models, JSON Schema, generated TypeScript
services/blackboard/       PostGIS schema, event bus, spatial API          :8001
services/base-station/     tail inference, georef, hazard writer           :8002
services/agents/           Cartographer, Navigator, Consensus               :8003
services/simulator/        scenario clock, seek, chaos                      :8004
services/benchmark/        FP32 vs INT8 harness, recorded runs              :8005
services/edge-capture/     INT8 head, feature packets, no raw-frame upload
apps/dashboard/            Helios console
apps/mobile/               Expo phone + mesh core
data/scenarios/            Lahaina, Joplin, Camp Fire
```

## Limits worth knowing

- Hazards in the three scenarios are placed on real streets from public damage reports. They are not a recording of a detector watching those fires.
- Benchmark figures shown as `RECORDED` come from a saved run on the machine that produced them. The console does not invent latencies.
- The mesh you can partition from the console is the simulated transport. Signing, the CRDT, and the upload rule are the same code the phone uses.
- Raw video stays on the local network. The feature packet is what crosses to the base station.
