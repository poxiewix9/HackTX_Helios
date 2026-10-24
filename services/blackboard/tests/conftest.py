from __future__ import annotations

import pytest
from sqlalchemy import Engine

from arco_blackboard.db import db_reachable, get_engine


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip integration tests (not fail) when the compose Postgres is not reachable."""
    if db_reachable():
        return
    skip = pytest.mark.skip(reason="database unreachable; run `make db-up` first")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def engine() -> Engine:
    return get_engine()
