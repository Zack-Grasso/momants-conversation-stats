"""Phase 1 ingestion schema

Revision ID: 001
Revises:
Create Date: 2026-06-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "conversations" not in tables:
        op.create_table(
            "conversations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("external_id", sa.String(36), nullable=True),
            sa.Column("agent_id", sa.String(36), nullable=True),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("member_name", sa.String(255), nullable=True),
            sa.Column("integration_type", sa.String(100), nullable=True),
            sa.Column("resolved", sa.Boolean(), nullable=True),
            sa.Column("takeover", sa.Boolean(), nullable=True),
            sa.Column("source_last_seen", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_conversations_external_id", "conversations", ["external_id"], unique=True)
        op.create_index("ix_conversations_agent_id", "conversations", ["agent_id"])
    else:
        cols = {c["name"] for c in inspector.get_columns("conversations")}
        if "external_id" not in cols:
            op.add_column("conversations", sa.Column("external_id", sa.String(36), nullable=True))
            op.create_index("ix_conversations_external_id", "conversations", ["external_id"], unique=True)
        if "agent_id" not in cols:
            op.add_column("conversations", sa.Column("agent_id", sa.String(36), nullable=True))
            op.create_index("ix_conversations_agent_id", "conversations", ["agent_id"])
        for col, col_type in [
            ("member_name", sa.String(255)),
            ("integration_type", sa.String(100)),
            ("resolved", sa.Boolean()),
            ("takeover", sa.Boolean()),
            ("source_last_seen", sa.DateTime(timezone=True)),
        ]:
            if col not in cols:
                op.add_column("conversations", sa.Column(col, col_type, nullable=True))

    if "messages" not in tables:
        op.create_table(
            "messages",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("external_id", sa.String(36), nullable=True),
            sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id", ondelete="CASCADE")),
            sa.Column("role", sa.String(50), nullable=False),
            sa.Column("from_agent", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_messages_external_id", "messages", ["external_id"], unique=True)
    else:
        cols = {c["name"] for c in inspector.get_columns("messages")}
        if "external_id" not in cols:
            op.add_column("messages", sa.Column("external_id", sa.String(36), nullable=True))
            op.create_index("ix_messages_external_id", "messages", ["external_id"], unique=True)
        if "from_agent" not in cols:
            op.add_column("messages", sa.Column("from_agent", sa.Boolean(), nullable=False, server_default=sa.false()))
        if "source_created_at" not in cols:
            op.add_column("messages", sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=True))

    if "sentiment_analyses" not in tables:
        op.create_table(
            "sentiment_analyses",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id", ondelete="CASCADE"), unique=True),
            sa.Column("stars", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("label", sa.String(50), nullable=False),
            sa.Column("score", sa.Float(), nullable=False),
            sa.Column("model_name", sa.String(255), nullable=False),
            sa.Column("raw_label", sa.String(100), nullable=True),
            sa.Column("raw_score", sa.Float(), nullable=True),
            sa.Column("low_confidence", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("analyzed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    else:
        cols = {c["name"] for c in inspector.get_columns("sentiment_analyses")}
        if "stars" not in cols:
            op.add_column("sentiment_analyses", sa.Column("stars", sa.Integer(), nullable=False, server_default="3"))
        if "raw_label" not in cols:
            op.add_column("sentiment_analyses", sa.Column("raw_label", sa.String(100), nullable=True))
        if "raw_score" not in cols:
            op.add_column("sentiment_analyses", sa.Column("raw_score", sa.Float(), nullable=True))
        if "low_confidence" not in cols:
            op.add_column(
                "sentiment_analyses",
                sa.Column("low_confidence", sa.Boolean(), nullable=False, server_default=sa.false()),
            )

    if "ingestion_jobs" not in tables:
        op.create_table(
            "ingestion_jobs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("agent_id", sa.String(36), nullable=False),
            sa.Column("limit", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
            sa.Column("processed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("messages_analyzed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("llm_fallback_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_ingestion_jobs_agent_id", "ingestion_jobs", ["agent_id"])


def downgrade() -> None:
    op.drop_table("ingestion_jobs")
    op.drop_column("sentiment_analyses", "low_confidence")
    op.drop_column("sentiment_analyses", "raw_score")
    op.drop_column("sentiment_analyses", "raw_label")
    op.drop_column("sentiment_analyses", "stars")
