"""road_edges.capacity_factor: Cartographer-applied effective-capacity reduction.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-24 15:05:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "road_edges",
        sa.Column("capacity_factor", sa.Float(), nullable=False, server_default="1.0"),
    )


def downgrade() -> None:
    op.drop_column("road_edges", "capacity_factor")
