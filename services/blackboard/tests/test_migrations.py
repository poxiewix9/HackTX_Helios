"""Phase 1: migrations apply on a fresh DB and PostGIS + pgRouting are usable."""

from __future__ import annotations

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, text

from arco_blackboard.db import extension_versions
from arco_blackboard.migrate import ALEMBIC_INI, upgrade_head


def test_alembic_script_dir_has_single_head() -> None:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert heads == ["0001"]


@pytest.mark.integration
def test_upgrade_head_enables_extensions(engine: Engine) -> None:
    upgrade_head()
    versions = extension_versions(engine)
    assert versions["postgis"] is not None
    assert versions["pgrouting"] is not None
    with engine.connect() as conn:
        current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert current == "0001"


@pytest.mark.integration
def test_postgis_geometry_round_trip(engine: Engine) -> None:
    """Prove the spatial engine actually works, not just that the extension row exists."""
    upgrade_head()
    with engine.connect() as conn:
        area = conn.execute(
            text(
                "SELECT ST_Area(ST_GeomFromText("
                "'POLYGON((0 0, 0 1, 1 1, 1 0, 0 0))', 4326)::geography)"
            )
        ).scalar_one()
        srid = conn.execute(
            text("SELECT ST_SRID(ST_SetSRID(ST_MakePoint(0.5, 0.5), 4326))")
        ).scalar_one()
        pgr = conn.execute(text("SELECT pgr_version()")).scalar_one()
    # 1x1 degree at the equator is ~12,300 km^2
    assert 1.2e10 < float(area) < 1.25e10
    assert srid == 4326
    assert pgr
