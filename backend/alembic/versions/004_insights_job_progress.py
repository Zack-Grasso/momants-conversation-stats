"""Add phase progress fields to insights jobs

Revision ID: 004
Revises: 003
Create Date: 2026-06-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("insights_jobs", sa.Column("phase_detail", sa.Text(), nullable=True))
    op.add_column("insights_jobs", sa.Column("phase_progress", sa.Integer(), server_default="0", nullable=False))
    op.add_column("insights_jobs", sa.Column("phase_total", sa.Integer(), server_default="0", nullable=False))


def downgrade() -> None:
    op.drop_column("insights_jobs", "phase_total")
    op.drop_column("insights_jobs", "phase_progress")
    op.drop_column("insights_jobs", "phase_detail")
