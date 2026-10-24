from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn

from arco_blackboard.db import db_reachable
from arco_blackboard.testing import use_test_database

# Isolated test DB / Redis prefix (never the dev stack); must run before settings are read.
TEST_DATABASE_URL = use_test_database()

REPO_ROOT = Path(__file__).resolve().parents[3]
GEOREF_DIR = REPO_ROOT / "data" / "samples" / "vision" / "georef"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if db_reachable():
        return
    if os.environ.get("ARCO_REQUIRE_DB") == "1":
        pytest.exit("ARCO_REQUIRE_DB=1 but the database is unreachable; run `make db-up`", 1)
    skip = pytest.mark.skip(reason="database unreachable; run `make db-up` first")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass
class Served:
    url: str
    server: uvicorn.Server
    thread: threading.Thread

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


def serve_in_thread(app: Any, *, ready_path: str = "/health", timeout_s: float = 20.0) -> Served:
    """Run an ASGI app on a real loopback socket in a background thread (so the edge's
    WebSocket client and the base station's httpx client use actual network paths)."""
    port = free_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="on",
        timeout_graceful_shutdown=1,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if httpx.get(url + ready_path, timeout=1.0).status_code == 200:
                return Served(url, server, thread)
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    server.should_exit = True
    raise RuntimeError(f"server on {url} did not become ready")


@pytest.fixture
def served_factory() -> Iterator[Any]:
    servers: list[Served] = []

    def _serve(app: Any, **kw: Any) -> Served:
        s = serve_in_thread(app, **kw)
        servers.append(s)
        return s

    yield _serve
    for s in reversed(servers):
        s.stop()
