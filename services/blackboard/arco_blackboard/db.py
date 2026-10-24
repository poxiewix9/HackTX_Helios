"""SQLAlchemy engine / session factory and the declarative base shared by all tables."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from arco_blackboard.settings import get_settings


class Base(DeclarativeBase):
    """Declarative base. Table models (phase 2) subclass this."""


_engine: Engine | None = None


def get_engine(url: str | None = None) -> Engine:
    """Process-wide engine. Pass `url` to override (tests)."""
    global _engine  # noqa: PLW0603 - single process-wide engine is intentional
    if url is not None:
        return create_engine(url, pool_pre_ping=True, future=True)
    if _engine is None:
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True, future=True)
    return _engine


def session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=engine or get_engine(), expire_on_commit=False)


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    session = session_factory(engine)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def db_reachable(engine: Engine | None = None, timeout_s: float = 2.0) -> bool:
    """True if `SELECT 1` succeeds. Used by health endpoints and test skips."""
    try:
        eng = engine or get_engine()
        with eng.connect().execution_options(timeout=timeout_s) as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def extension_versions(engine: Engine | None = None) -> dict[str, str | None]:
    """Installed PostGIS / pgRouting versions (None if the extension is missing)."""
    eng = engine or get_engine()
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT extname, extversion FROM pg_extension "
                "WHERE extname IN ('postgis', 'pgrouting')"
            )
        ).all()
    found = {name: version for name, version in rows}
    return {"postgis": found.get("postgis"), "pgrouting": found.get("pgrouting")}
