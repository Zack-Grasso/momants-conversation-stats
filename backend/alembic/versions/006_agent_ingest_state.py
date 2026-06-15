"""Add agent ingest state and ingest job skip counters

Revision ID: 006
Revises: 005
"""

from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_ingest_states",
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("last_sync_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_conversations_imported", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_conversations_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_conversations_imported", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("agent_id"),
    )
    op.add_column("ingestion_jobs", sa.Column("skipped", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ingestion_jobs", sa.Column("sync_start_date", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("ingestion_jobs", "sync_start_date")
    op.drop_column("ingestion_jobs", "skipped")
    op.drop_table("agent_ingest_states")
