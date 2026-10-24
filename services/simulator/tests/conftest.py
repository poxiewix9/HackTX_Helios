from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from arco_blackboard.api import create_app as create_blackboard_app
from arco_blackboard.db import db_reachable, get_engine
from arco_blackboard.migrate import upgrade_head
from arco_blackboard.settings import get_settings as blackboard_settings
from arco_blackboard.testing import truncate_all, use_test_database
from arco_sim.clients import BlackboardClient

# Same isolation as the other suites: `<db>_test` + Redis prefix `blackboard-test.`.
TEST_DATABASE_URL = use_test_database()

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"
SCENARIOS_DIR = DATA_DIR / "scenarios"


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
def data_dir() -> Path:
    return DATA_DIR


@pytest.fixture(scope="session")
def scenarios_dir() -> Path:
    return SCENARIOS_DIR


@pytest.fixture(scope="session")
def engine() -> Engine:
    return get_engine()


@pytest.fixture
def blackboard(engine: Engine) -> Iterator[TestClient]:
    """A real blackboard API (migrated test DB, truncated) served in-process."""
    if not db_reachable(engine):
        pytest.skip("database unreachable")
    upgrade_head()
    truncate_all(engine)
    app = create_blackboard_app(blackboard_settings(), publisher_name="sim-tests")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def bb_client(blackboard: TestClient) -> BlackboardClient:
    return BlackboardClient("http://blackboard.test", http=cast(httpx.Client, blackboard))
