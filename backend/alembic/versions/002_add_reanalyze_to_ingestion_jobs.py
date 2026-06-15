"""Add reanalyze flag to ingestion jobs

Revision ID: 002
Revises: 001
Create Date: 2026-06-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "ingestion_jobs" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("ingestion_jobs")}
    if "reanalyze" not in cols:
        op.add_column(
            "ingestion_jobs",
            sa.Column("reanalyze", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    op.drop_column("ingestion_jobs", "reanalyze")
