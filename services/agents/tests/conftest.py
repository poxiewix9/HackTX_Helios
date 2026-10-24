from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

from arco_agents.penalties import PenaltyPolicy
from arco_agents.routing_config import RoutingConfig
from arco_agents.settings import DEFAULT_PENALTIES, DEFAULT_ROUTING
from arco_blackboard.bus import Publisher, make_publisher
from arco_blackboard.db import db_reachable, get_engine
from arco_blackboard.ingest import ingest
from arco_blackboard.migrate import upgrade_head
from arco_blackboard.service import BlackboardService
from arco_blackboard.settings import Settings, get_settings
from arco_blackboard.testing import reset_edges, truncate_all, use_test_database

# Isolate from the dev database + the live `agents` container (which would otherwise
# consume our hazard events and race the in-test Cartographer/Navigator).
TEST_DATABASE_URL = use_test_database()

REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLE_GRAPHML = REPO_ROOT / "data" / "samples" / "osm" / "lahaina_drive.graphml"
DEMO_POPULATION = REPO_ROOT / "data" / "samples" / "lahaina" / "demo_population.json"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
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
def publisher(settings: Settings) -> Iterator[Publisher]:
    pub = make_publisher(settings, publisher_name="agents-tests")
    yield pub
    pub.close()


@pytest.fixture(scope="session")
def lahaina_db(engine: Engine, publisher: Publisher) -> Engine:
    """Migrated DB with the Lahaina sample road graph ingested once per session."""
    if not db_reachable(engine):
        pytest.skip("database unreachable")
    upgrade_head()
    truncate_all(engine)
    result = ingest("graphml_file", str(SAMPLE_GRAPHML), engine=engine, publisher=publisher)
    assert result.edges > 0
    return engine


@pytest.fixture
def service(lahaina_db: Engine, publisher: Publisher) -> BlackboardService:
    """Fresh blackboard state on top of the ingested graph: edges reopened, no hazards."""
    reset_edges(lahaina_db)
    return BlackboardService(lahaina_db, publisher)


@pytest.fixture(scope="session")
def policy() -> PenaltyPolicy:
    return PenaltyPolicy.load(DEFAULT_PENALTIES)


@pytest.fixture(scope="session")
def routing_config() -> RoutingConfig:
    return RoutingConfig.load(DEFAULT_ROUTING)
