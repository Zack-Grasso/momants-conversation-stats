"""Add referred_intent_jobs table for doorverwezen intent labeling

Revision ID: 008
Revises: 007
"""

from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "referred_intent_jobs" in inspector.get_table_names():
        return

    op.create_table(
        "referred_intent_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="running"),
        sa.Column("phase", sa.String(length=50), nullable=False, server_default="intents"),
        sa.Column("processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("limit", sa.Integer(), nullable=True),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("messages_analyzed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reanalyze", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("phase_detail", sa.Text(), nullable=True),
        sa.Column("phase_progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("phase_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scope_conversation_ids", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("referred_intent_jobs")
