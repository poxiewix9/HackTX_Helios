"""Run Alembic migrations programmatically (used by the compose `blackboard-migrate`
one-shot and by `make migrate`). Waits for the database to accept connections first."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from alembic import command
from alembic.config import Config

from arco_blackboard.db import db_reachable, extension_versions, get_engine
from arco_blackboard.logging import configure_logging, get_logger

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def wait_for_db(timeout_s: float = 60.0, interval_s: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if db_reachable():
            return True
        time.sleep(interval_s)
    return False


def upgrade_head() -> None:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
    command.upgrade(cfg, "head")


def main() -> int:
    configure_logging()
    log = get_logger("blackboard.migrate")
    if not wait_for_db():
        log.error("database unreachable after timeout")
        return 1
    upgrade_head()
    versions = extension_versions(get_engine())
    log.info("migrations applied", **versions)
    if versions["postgis"] is None or versions["pgrouting"] is None:
        log.error("required extensions missing", **versions)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
