"""Revision ID: 005_insights_job_scope
Revises: 004_insights_job_progress
Create Date: 2026-06-10
"""

from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("insights_jobs", sa.Column("ingest_job_id", sa.Integer(), nullable=True))
    op.add_column("insights_jobs", sa.Column("scope_conversation_ids", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_insights_jobs_ingest_job_id",
        "insights_jobs",
        "ingestion_jobs",
        ["ingest_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_insights_jobs_ingest_job_id", "insights_jobs", ["ingest_job_id"])


def downgrade() -> None:
    op.drop_index("ix_insights_jobs_ingest_job_id", table_name="insights_jobs")
    op.drop_constraint("fk_insights_jobs_ingest_job_id", "insights_jobs", type_="foreignkey")
    op.drop_column("insights_jobs", "scope_conversation_ids")
    op.drop_column("insights_jobs", "ingest_job_id")
