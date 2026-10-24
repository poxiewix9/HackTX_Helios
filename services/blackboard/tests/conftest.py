from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from arco_blackboard.bus import Publisher, make_publisher
from arco_blackboard.db import db_reachable, get_engine
from arco_blackboard.migrate import upgrade_head
from arco_blackboard.service import BlackboardService
from arco_blackboard.settings import Settings, get_settings

REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLE_GRAPHML = REPO_ROOT / "data" / "samples" / "osm" / "lahaina_drive.graphml"

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


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Integration tests skip when the DB is down, unless ARCO_REQUIRE_DB=1 (CI / make
    targets) in which case they fail loudly instead of silently passing."""
    if db_reachable():
        return
    if os.environ.get("ARCO_REQUIRE_DB") == "1":
        pytest.exit("ARCO_REQUIRE_DB=1 but the database is unreachable; run `make db-up`", 1)
    skip = pytest.mark.skip(reason="database unreachable; run `make db-up` first")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return get_settings()


@pytest.fixture(scope="session")
def engine() -> Engine:
    return get_engine()


@pytest.fixture(scope="session")
def migrated(engine: Engine) -> None:
    if db_reachable(engine):
        upgrade_head()


@pytest.fixture
def clean_db(engine: Engine, migrated: None) -> Iterator[Engine]:
    """Truncate every blackboard table before the test (keeps schema + graph_state)."""
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE"))
        conn.execute(text("UPDATE graph_state SET version = 0 WHERE id = 1"))
    yield engine


@pytest.fixture
def publisher(settings: Settings) -> Iterator[Publisher]:
    pub = make_publisher(settings, publisher_name="tests")
    yield pub
    pub.close()


@pytest.fixture
def service(clean_db: Engine, publisher: Publisher) -> BlackboardService:
    return BlackboardService(clean_db, publisher)


@pytest.fixture
def sample_graphml() -> Path:
    assert SAMPLE_GRAPHML.exists(), f"missing {SAMPLE_GRAPHML}"
    return SAMPLE_GRAPHML
