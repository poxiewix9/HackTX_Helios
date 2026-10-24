"""Enable PostGIS and pgRouting.

Revision ID: 0001
Revises:
Create Date: 2026-10-24
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgrouting")


def downgrade() -> None:
    # Extensions are shared infrastructure; dropping them would also drop any table
    # with a geometry column. Leave them in place on downgrade.
    pass
