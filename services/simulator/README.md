# arco-simulator (Phase 7)

Scenario player for ARCO. Replays a scripted disaster timeline (**simulated on real event
geometry**) into the running blackboard so the Cartographer / Navigator / Consensus agents
react exactly as they would to live detections. It is a **pure HTTP client**: no database
access, no direct edits to `road_edges` or `routes`. Every hazard enters through
`POST /hazards` on the blackboard; every community pin is a **signed** `CommunityReport`
posted to `/reports`; the scenario clock is published via `POST /clock`; the chaos button
toggles cellular via `POST /network`.

```
data/scenarios/*.json ──▶ arco_sim.player.Player ──HTTP──▶ blackboard :8001 ──outbox──▶ agents :8003
                              │                                   ▲
                              ├─ clock.tick (2 Hz while playing) ─┘
                              └─ GET /feeds/<cam>/frame.jpg  (synthetic street feed, watermarked)
```

## Scenarios

| id | event | duration | timeline | community pins | OSM graph (ODbL) |
|----|-------|----------|----------|----------------|------------------|
| `lahaina_2023` | Maui wildfire, 2023-08-08 | 1800 s | 13 | 5 | `data/samples/osm/lahaina_drive` (637 edges) |
| `joplin_2011` | EF5 tornado, 2011-05-22 | 1500 s | 14 | 5 | `data/samples/osm/joplin_drive` (4926 edges) |
| `camp_fire_2018` | Camp Fire / Paradise, 2018-11-08 | 1800 s | 13 | 5 | `data/samples/osm/paradise_drive` (3392 edges) |

Each file's `notes` names the data sources and licenses and states that hazard polygons
are **digitised approximations** of public damage surveys; they are not forensic
reconstructions. The road graphs are present-day OSM snapshots (roads may differ from the
event date). `expected_blocked_edges` holds a 40-edge sample of the edges the pipeline
severs by the end of the scenario (measured with the real agents, see `make test-sim`);
`GET /check` / `arco-sim play --check` verifies them against the live blackboard.

The scenario clock is the single source of truth: `advance()` fires timeline and community
events merged in chronological order, each stamped with its own `t`; `seek()` reconciles
the blackboard (resolves hazards that should not exist yet, re-fires skipped pins, restores
the network state) via the same HTTP path instead of touching rows.

## Run

```bash
make validate-scenarios                  # schema + referenced-file check for data/scenarios/*.json
make test-sim                            # 14 tests incl. an in-process Lahaina run through the real agents (needs DB)
docker compose up -d --wait simulator    # :8004, loads lahaina_2023, waits for /play
curl -s localhost:8004/health
curl -s -X POST localhost:8004/speed -H 'content-type: application/json' -d '{"speed": 60}'
curl -s -X POST localhost:8004/play
curl -s localhost:8004/state | jq '{t, playing, active_hazards, reports_sent, last_error}'
make play-scenario SCENARIO=joplin_2011 SPEED=120   # headless CLI player against the running stack, then --check
make render-feed CAMERA=cam-front-st OUT=data/cache/front.mp4  # synthetic clip (cv2), watermarked "SIMULATED street feed"
```

### HTTP API (`:8004`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | `status`, blackboard/agents reachability, loaded scenario, `t`, `playing`, `last_error` |
| GET | `/scenarios` | summaries of every scenario in `ARCO_SCENARIOS_DIR` |
| GET | `/scenario` | the loaded `Scenario` (full JSON) |
| GET | `/state` | `PlayerState`: cursors, active hazard ids, reports sent, event log |
| GET | `/check` | `expected_blocked_edges` vs. edges currently severed on the blackboard |
| POST | `/load` `{scenario_id}` | load (and ingest its road graph via blackboard `POST /graph/ingest` if the graph version differs) |
| POST | `/play`, `/pause`, `/speed {speed}`, `/seek {t}` | transport controls; every change publishes a `clock.tick` |
| POST | `/chaos/hazard` `{geom, hazard_class, confidence, velocity_m_s, note}` | inject an ad-hoc hazard (`source="simulated"`) |
| POST | `/chaos/cellular` `{up, reason}` | flip the simulated cellular network (`network.cellular` event) |
| GET | `/feeds/{camera_id}/frame.jpg?t=` | one synthetic street frame at scenario time `t` |
| GET | `/feeds/{camera_id}/clip.mp4?t0=&duration=&fps=` | rendered clip |

Settings (env / `.env`): `SIMULATOR_PORT`, `ARCO_BLACKBOARD_URL`, `ARCO_AGENTS_URL`,
`ARCO_SCENARIOS_DIR`, `ARCO_DATA_DIR`, `ARCO_DEFAULT_SCENARIO`, `ARCO_SIM_AUTOLOAD`,
`ARCO_SIM_AUTOPLAY_SPEED` (0 = load only), `ARCO_SIM_CLOCK_HZ`.

## Determinism

- Hazard ids are `uuid5(SIM_NS, "<scenario>:<hazard_id>")`, so replaying a scenario updates
  the same blackboard rows instead of creating duplicates.
- Community reporters derive their Ed25519 key from
  `DeviceKey.from_seed("arco-sim:<scenario>:<reporter>")`, so trust accumulates per phone
  across runs and the Consensus agent sees two *independent* keys where the script says so.
- Synthetic feeds are seeded per camera; frames at equal `t` are identical.

## Known limitations

- Footage is synthetic (flat-shaded street + animated overlays), watermarked, and meant to
  exercise the vision pipeline's plumbing, not its accuracy.
- Macro tiles referenced by scenarios are optional; a labelled synthetic tile is used when
  the PNG is absent (`validate` warns).
- `expected_blocked_edges` is a sample, not the full severed set (full sets are 200–2300
  edges); it is re-measured whenever polygons change.
- Reconciliation on backward `seek` resolves hazards through the blackboard; agent events
  logged during the skipped interval remain (they are history, not state).
