"""Add dual-sentiment (stage 2) columns and sentiment_jobs table

Revision ID: 007
Revises: 006

Additive only: new columns on sentiment_analyses are nullable so existing rows
written by the legacy single-model path remain valid, and the new sentiment_jobs
table is independent of existing tables.
"""

from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "sentiment_analyses" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("sentiment_analyses")}
        if "polarity" not in cols:
            op.add_column("sentiment_analyses", sa.Column("polarity", sa.String(length=20), nullable=True))
        if "polarity_score" not in cols:
            op.add_column("sentiment_analyses", sa.Column("polarity_score", sa.Float(), nullable=True))
        if "emotions_json" not in cols:
            op.add_column("sentiment_analyses", sa.Column("emotions_json", sa.Text(), nullable=True))
        if "original_language" not in cols:
            op.add_column("sentiment_analyses", sa.Column("original_language", sa.String(length=8), nullable=True))
        if "translated" not in cols:
            op.add_column(
                "sentiment_analyses",
                sa.Column("translated", sa.Boolean(), nullable=True, server_default=sa.false()),
            )

    if "sentiment_jobs" not in inspector.get_table_names():
        op.create_table(
            "sentiment_jobs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("agent_id", sa.String(length=36), nullable=False),
            sa.Column("status", sa.String(length=50), nullable=False, server_default="running"),
            sa.Column("phase", sa.String(length=50), nullable=False, server_default="sentiment"),
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
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_sentiment_jobs_agent_id", "sentiment_jobs", ["agent_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "sentiment_jobs" in inspector.get_table_names():
        op.drop_index("ix_sentiment_jobs_agent_id", table_name="sentiment_jobs")
        op.drop_table("sentiment_jobs")

    if "sentiment_analyses" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("sentiment_analyses")}
        for column in ("translated", "original_language", "emotions_json", "polarity_score", "polarity"):
            if column in cols:
                op.drop_column("sentiment_analyses", column)
