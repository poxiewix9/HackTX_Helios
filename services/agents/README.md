# services/agents — Cartographer + Navigator (Phase 3) + Consensus (Phase 6)

Deterministic LangGraph agents that react to blackboard events. The SLM only ever
*explains* routes; `ARCO_SLM_ENABLED=false` (the default) is fully supported and is what
tests and the compose stack run.

```
hazard.* ──► Cartographer ──apply_edge_updates──► graph.updated ──► Navigator ──publish_routes──► routes.updated
                 │                                                      │
                 └── AgentEvent hazard.applied / .noop / .resolved / .expired   └── AgentEvent routes.recomputed[.overload]
report.* ──► Consensus ──verify sig → trust/rate gate → ST-DBSCAN → quorum──► upsert community hazard ──► Cartographer ──► Navigator
                 └── AgentEvent report.pending / community.hazard.created / hazard.corroborated / hazard.disputed / report.rejected …
```

## Modules (`arco_agents/`)

| Module           | Role                                                                                                   |
|------------------|--------------------------------------------------------------------------------------------------------|
| `penalties`      | Loads `config/penalties.yaml` (keyed by `HazardClass`); `ClassPolicy.decide(hazard, f)` → ignore / sever / penalize / capacity; `combine()` merges several hazards on one edge (any-sever, MAX multiplier, MIN capacity) |
| `geo`            | Metric buffers (AEQD), haversine, bearings, line concatenation                                          |
| `cartographer`   | `apply / release / expire / regrow`: buffer hazard → `edges_intersecting` → per-edge state → `BlackboardService.apply_edge_updates(...)` (edge + graph events in one transaction). Pure `plan_edge_update()` for unit tests |
| `routing_config` | `config/routing.yaml`; BPR congestion `cost·(1+α(load/cap)^β)`                                          |
| `graph`          | NetworkX mirror of `road_edges` (severed edges removed, parallel edges keep the cheapest), A* with haversine/heuristic-speed heuristic, k-diverse paths by penalized successive shortest paths, incremental `apply_edges()` |
| `dispersal`      | Crowd dispersal: candidates per cell (k paths × nearest zones) → Frank–Wolfe path-based assignment with edge + zone congestion → `split_groups()` into integer crowd groups |
| `explain`        | `Explainer` protocol: `DeterministicExplainer` (template), `SlmExplainer` (Ollama `/api/generate`, falls back on any failure or if it drops a road name), `CachedExplainer` keyed by route hash |
| `population`     | Population cells + safe zones from a Scenario file or `{population_cells, safe_zones}` JSON              |
| `navigator`      | Rebuilds routes on `graph.updated`/`edge.updated`, publishes with measured `compute_ms`, writes edge loads back |
| `reports`        | Ed25519 report signing/verification over a canonical JSON form (`canonical_bytes`); `DeviceKey` (seeded keys for scenarios), `reporter_hash = sha256(raw pubkey)` |
| `clustering`     | Hand-written spatial-temporal DBSCAN: neighbours when `max(d_m/eps_m, dt_s/eps_s) ≤ 1`                 |
| `consensus`      | `Consensus.on_report`: signature → per-reporter trust + sliding-window rate limit → cluster pending pins → quorum of **k independent trusted reporters** at a **vision-negative** spot → community hazard (`source=community`, convex hull ⊕ `pin_radius_m`); pins near an active vision hazard corroborate it (+confidence, +trust); denial quorums mark vision hazards `disputed` (never deleted) and resolve community hazards; `observe_hazard` credits pending pins when vision later agrees. Policy in `config/consensus.yaml` |
| `pipeline`       | LangGraph `StateGraph`: route → (consensus →) cartographer → navigator, each node fault-isolated (`_guard`), graph_version + hazard-seq dedup |
| `runner`         | `AgentsRuntime`: bus subscriber (topics hazard/graph/edge/clock/report), periodic tick (TTL expiry + buffer regrow) |
| `api`            | FastAPI on `:8003`: `/health`, `/routes`, `/agent-events`, `POST /navigator/population`, `POST /navigator/recompute`, `WS /ws/routes` |

## Policy (see the commented math in `config/penalties.yaml`)

`w = overlap_fraction × confidence`. Ignore below `min_confidence`; **sever** when the
class action is `sever` and `f ≥ sever_fraction ∧ c ≥ sever_confidence`; otherwise
`multiplier = 1 + (max_multiplier − 1)·w·(0.5 + 0.5·severity)`; `crowd_bottleneck` scales
`capacity_estimate` by `capacity_factor^w`. Expanding classes are buffered by
`min(max_buffer_m, base_margin_m + v·(horizon_s + elapsed_s))` and re-applied on every
tick while the buffer still grows. Every decision is an `AgentEvent`.

## Run / test

```bash
docker compose up -d --wait && make healthcheck   # agents container on :8003
make test-cartographer                            # 3.1  (needs DB)
make test-navigator                               # 3.2  (prints REROUTE compute_ms / wall_ms)
make test-consensus                               # 6.1  (signing, DBSCAN, trust, smoke false-negative override)
make test-agents                                  # all of the above + pipeline
make serve-agents                                 # host-side API with reload
uv run arco-agents-run                            # headless runtime (no HTTP)
websocat ws://localhost:8003/ws/routes            # routes.updated + agent.event frames
```

Live check: `POST :8001/hazards?scenario_t=…` with a `fire_front` polygon over a current
route → `/ws/routes` delivers the recomputed routes; `POST …/status?status=resolved`
restores the edges and reroutes again.

Tests run against `<db>_test` (created on demand) with Redis channel prefix
`blackboard-test.` so they never touch the dev database or the running `agents`
container. See `arco_blackboard.testing.use_test_database()`.

## Config (env / `.env`)

| Var                      | Default                                      | Notes                                   |
|--------------------------|----------------------------------------------|-----------------------------------------|
| `DATABASE_URL`, `REDIS_URL` | as blackboard                             | shared bus                              |
| `AGENTS_PORT`            | `8003`                                       |                                         |
| `ARCO_SLM_ENABLED`       | `false`                                      | `true` → Ollama explanations w/ fallback |
| `OLLAMA_URL`, `ARCO_SLM_MODEL`, `ARCO_SLM_TIMEOUT_S` | `http://localhost:11434`, `qwen2.5:1.5b-instruct`, `4.0` | |
| `ARCO_PENALTIES_PATH`, `ARCO_ROUTING_PATH`, `ARCO_CONSENSUS_PATH` | `services/agents/config/*.yaml` |                     |
| `ARCO_POPULATION_PATH`   | unset (compose: `data/samples/lahaina/demo_population.json`) | or `POST /navigator/population` |
| `ARCO_AGENTS_TICK_S`     | `5`                                          | expiry + regrow period (wall seconds)   |
| `ARCO_BUS_PREFIX`        | `blackboard.`                                | Redis channel prefix (shared w/ blackboard) |

## Known limitations

- Crowd dispersal is a static path-based assignment (Frank–Wolfe, few iterations), not a
  time-expanded simulation; `max_edge_load_ratio` and `routes.recomputed.overload` flag
  congestion rather than guaranteeing feasibility.
- Population cells are snapped to the nearest graph node; the demo population file is an
  approximation ("Simulated on real event geometry"), not census data.
- Trust is an in-memory opinion of one agent instance (persisted per report as
  `trust_score`); a restart resets reporters to the prior. There is no cross-instance ledger.
- "Independent" means distinct device keys. A Sybil with many keys can still reach quorum;
  the rate limit and the per-reporter trust floor only slow this down.
- Vision-negative means "no *active* vision hazard of that class within
  `vision_negative_radius_m`" — a recently *resolved* vision hazard at the same spot is
  marked `disputed` instead of silently trusted.
- The SLM path is wired but unverified against a local Ollama in this environment.
