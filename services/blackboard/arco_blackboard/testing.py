"""Test builders + DB reset helpers shared by the blackboard and agents test suites.

Not used at runtime; lives in the package so other services can import it."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, create_engine, make_url, text

from arco_schemas import Hazard, Polygon

DEFAULT_DATABASE_URL = "postgresql+psycopg://arco:arco_dev_password@localhost:5432/arco"
TEST_REDIS_DB = 9
TEST_BUS_PREFIX = "blackboard-test."


def use_test_database() -> str:
    """Point `DATABASE_URL` / `REDIS_URL` at an isolated test database for this process.

    Test suites truncate tables and publish bus events; running them against the dev
    database would wipe the running stack *and* let the live `agents` container race
    the in-test agents on every hazard event. So tests use `<dbname>_test` (created on
    demand; migrations create the PostGIS/pgRouting extensions) and a separate Redis
    logical database. Override with `ARCO_TEST_DATABASE_URL` / `ARCO_TEST_REDIS_URL`.
    Must be called before `arco_blackboard.settings.get_settings()` is first used.
    Returns the test database URL. Idempotent.
    """
    base = os.environ.get("DATABASE_URL") or _dotenv_value("DATABASE_URL") or DEFAULT_DATABASE_URL
    test_url = os.environ.get("ARCO_TEST_DATABASE_URL")
    if not test_url:
        url = make_url(base)
        name = url.database or "arco"
        test_url = url.set(
            database=name if name.endswith("_test") else f"{name}_test"
        ).render_as_string(hide_password=False)
    os.environ["DATABASE_URL"] = test_url

    redis = os.environ.get("ARCO_TEST_REDIS_URL")
    if not redis:
        base_redis = os.environ.get("REDIS_URL") or _dotenv_value("REDIS_URL")
        if base_redis:
            redis = base_redis.rsplit("/", 1)[0] + f"/{TEST_REDIS_DB}"
    if redis:
        os.environ["REDIS_URL"] = redis
    # Redis Pub/Sub is server-wide: a distinct channel prefix keeps the live stack's
    # subscribers (e.g. the `agents` container) from hearing test events and vice versa.
    os.environ.setdefault("ARCO_BUS_PREFIX", TEST_BUS_PREFIX)

    _ensure_database(base, test_url)
    return test_url


def _dotenv_value(key: str) -> str | None:
    """Minimal `.env` reader (pydantic-settings reads it too, but we need the value
    before Settings is constructed)."""
    path = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip("'\"") or None
    return None


def _ensure_database(admin_url: str, test_url: str) -> None:
    test_db = make_url(test_url).database
    if make_url(admin_url).database == test_db:
        return
    try:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": test_db}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{test_db}"'))
        admin.dispose()
    except Exception:  # DB down: callers skip/exit via db_reachable()
        return


TABLES = (
    "edge_hazard_links",
    "routes",
    "hazards",
    "road_edges",
    "community_reports",
    "safe_zones",
    "agent_events",
    "bus_outbox",
)


def truncate_all(engine: Engine) -> None:
    """Empty every blackboard table and reset graph_state (keeps the schema)."""
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE"))
        conn.execute(text("UPDATE graph_state SET version = 0 WHERE id = 1"))


def reset_edges(engine: Engine) -> None:
    """Reopen every edge and drop hazards/links/routes/events, keeping road_edges rows so a
    test module can ingest the road graph once."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE edge_hazard_links, routes, hazards, agent_events, bus_outbox "
                "RESTART IDENTITY CASCADE"
            )
        )
        conn.execute(
            text(
                "UPDATE road_edges SET status='open', current_cost=base_cost, current_load=0, "
                "capacity_factor=1.0"
            )
        )


def square(lon: float, lat: float, half: float) -> Polygon:
    return Polygon(
        coordinates=[
            [
                [lon - half, lat - half],
                [lon - half, lat + half],
                [lon + half, lat + half],
                [lon + half, lat - half],
                [lon - half, lat - half],
            ]
        ]
    )


def make_hazard(geom: Polygon, **overrides: Any) -> Hazard:
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "id": uuid4(),
        "geom": geom,
        "hazard_class": "fire_front",
        "confidence": 0.9,
        "source": "simulated",
        "stream": "macro",
        "camera_id": "tile_0",
        "first_seen": now,
        "last_seen": now,
        "ttl_s": 600.0,
        "status": "active",
        "severity": 1.0,
        "velocity_m_s": 0.5,
    }
    base.update(overrides)
    return Hazard(**base)
