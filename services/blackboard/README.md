# services/blackboard

PostGIS blackboard: schema, Alembic migrations, event bus (Redis + LISTEN/NOTIFY
fallback), spatial REST API and `/ws/blackboard` stream.

## Phase 1 (current)

- `arco_blackboard.db` — engine/session from `DATABASE_URL`, `db_reachable()`,
  `extension_versions()`.
- `arco_blackboard.migrate` — waits for the DB, runs `alembic upgrade head`, verifies
  PostGIS + pgRouting, logs JSON.
- `alembic/versions/0001_extensions.py` — `CREATE EXTENSION postgis, pgrouting`.
- `Dockerfile` — used by the compose `blackboard-migrate` one-shot (build context = repo
  root).

## Run

```bash
docker compose up -d --wait postgres redis
make migrate                      # host-side
# or
docker compose run --rm blackboard-migrate
make test-blackboard              # integration tests skip if the DB is unreachable
```

## Config (env / .env)

| Var              | Default                                                   |
|------------------|-----------------------------------------------------------|
| `DATABASE_URL`   | `postgresql+psycopg://arco:arco_dev_password@localhost:5432/arco` |
| `REDIS_URL`      | unset -> LISTEN/NOTIFY fallback                           |
| `BLACKBOARD_PORT`| `8001`                                                    |
| `ARCO_LOG_JSON`  | `true`                                                    |

## Phase 2 (next, owner: blackboard)

Tables `hazards`, `road_edges`, `edge_hazard_links`, `community_reports`, `routes`,
`safe_zones`, `agent_events` with GiST indexes; event bus; FastAPI spatial API; OSMnx
ingestion. Row shapes must match `arco_schemas` models.
