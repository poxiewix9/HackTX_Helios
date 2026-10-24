# services/blackboard

PostGIS blackboard: schema, Alembic migrations, event bus (transactional outbox +
Redis pub/sub with Postgres LISTEN/NOTIFY fallback), spatial REST API, `/ws/blackboard`
stream, and OSMnx road-graph ingestion. Row shapes are the `arco_schemas` models; this
service never defines its own wire types.

## Modules

| Module                  | Role                                                                                   |
|-------------------------|----------------------------------------------------------------------------------------|
| `settings`, `db`, `logging` | `Settings` from env/.env, engine + `session_scope()`, structlog JSON                 |
| `models`                | SQLAlchemy/GeoAlchemy2 rows: `hazards`, `road_edges`, `edge_hazard_links`, `community_reports`, `safe_zones`, `routes`, `agent_events`, `graph_state`, `bus_outbox` |
| `convert`               | `arco_schemas` model ⇄ ORM row (GeoJSON ⇄ WKB, SRID 4326)                              |
| `repo`                  | Spatial queries: bbox filters (`&&` on GiST), `edges_intersecting()` with metric overlap fraction, `reports_near()` (geography `ST_DWithin`), graph version, routes, outbox |
| `bus`                   | `Publisher.publish(session, message)` — same transaction as the row write; `NotifySubscriber` / `RedisSubscriber`; `read_outbox()` catch-up |
| `service`               | `BlackboardService`: one method per blackboard operation, each = write + event        |
| `api`                   | FastAPI REST + `/ws/blackboard` fan-out (`Broadcaster`, bounded per-client queues)    |
| `ingest`                | OSMnx MultiDiGraph → `road_edges` rows; cost/capacity from `config/ingest.yaml`       |
| `migrate`               | waits for DB, `alembic upgrade head`, verifies PostGIS + pgRouting                    |

## Event bus semantics

- Every write that other services care about goes through `Publisher.publish()` inside
  the **same transaction** as the row change. It allocates `seq` from
  `bus_outbox_id_seq`, inserts the `BusEnvelope` JSON into `bus_outbox`, and calls
  `pg_notify('blackboard_events', seq)`. The notify fires only on commit.
- If `REDIS_URL` is reachable the envelope is also published to `blackboard.<topic>`
  **after commit** (SQLAlchemy `after_commit`). Redis is optional; `GET /health` reports
  `bus_mode: redis+notify | notify`.
- `seq` is monotonic and equals the outbox id, so consumers resume with
  `GET /events?since_seq=N` or `ws://…/ws/blackboard?since_seq=N`.
- Topics = the part of `type` before the first dot: `hazard`, `edge`, `graph`, `report`,
  `safe_zone`, `agent`, `routes`.

## REST (port `BLACKBOARD_PORT`, default 8001)

| Method | Path                      | Notes                                                      |
|--------|---------------------------|------------------------------------------------------------|
| GET    | `/health`, `/metrics`     | extensions, bus mode, ws clients, row counts (Prometheus)  |
| GET    | `/hazards?bbox=w,s,e,n&status=` | `GET /hazards/{id}`                                   |
| POST   | `/hazards?scenario_t=`    | upsert; returns `HazardEvent` (`hazard.created|updated`)   |
| POST   | `/hazards/{id}/status?status=` | `resolved` / `disputed` / `active`                    |
| GET    | `/edges?bbox=&status=&ids=` | `POST /edges/intersecting {geom}` → `EdgeOverlap[]`      |
| GET    | `/graph/version`          | bumped on every `road_edges` state change / reload         |
| GET    | `/routes?crowd_group_id=` | current routes only by default                             |
| GET/PUT| `/safe-zones`, `/safe-zones/{id}` |                                                    |
| GET/POST | `/reports?bbox=&since=` | community reports (`report.created`)                       |
| GET/POST | `/agent-events?agent=`  | agent reasoning trail                                      |
| GET    | `/events?since_seq=&topics=` | outbox catch-up                                        |
| WS     | `/ws/blackboard?since_seq=&topics=` | one `BlackboardMessage` JSON per frame           |

Bad `bbox` → 422. Request bodies are validated with `extra="forbid"`.

## Ingestion

```bash
make ingest-sample                         # offline: data/samples/osm/lahaina_drive.graphml (637 edges)
make ingest-bbox BBOX=-156.695,20.862,-156.662,20.897   # Overpass download, cached in data/cache
uv run arco-blackboard-ingest --place "Joplin, Missouri, USA" --save-graphml data/cache/joplin.graphml
```

`base_cost` = `length_m / speed_m_s[highway]` (seconds at evacuation speed);
`capacity_estimate` = `capacity_people_per_min[highway]`; both from
`config/ingest.yaml`. A reload replaces all edges, deletes hazard links, bumps
`graph_version` and publishes `graph.updated(full_rebuild=true)`. Sample metadata and OSM
attribution live next to the graphml in `lahaina_drive.json`.

## Run / test

```bash
docker compose up -d --wait               # postgres, redis, migrate one-shot, blackboard API
make serve-blackboard                     # or run the API host-side with reload
make test-blackboard                      # 2.1 tables/migrations (needs DB; DB-down fails)
make test-blackboard-events               # 2.2 bus + REST + WS
make test-blackboard-all                  # everything incl. ingestion
```

Without `ARCO_REQUIRE_DB=1`, integration tests skip when the DB is unreachable.

## Config (env / .env)

| Var               | Default                                                          |
|-------------------|------------------------------------------------------------------|
| `DATABASE_URL`    | `postgresql+psycopg://arco:arco_dev_password@localhost:5432/arco` |
| `REDIS_URL`       | unset / unreachable → NOTIFY-only mode                           |
| `BLACKBOARD_PORT` | `8001`                                                           |
| `ARCO_LOG_JSON`   | `true`                                                           |
