"""Insights metrics and question clusters

Revision ID: 003
Revises: 002
Create Date: 2026-06-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "insights_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="running"),
        sa.Column("phase", sa.String(50), nullable=False, server_default="metrics"),
        sa.Column("processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("limit", sa.Integer(), nullable=True),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("messages_analyzed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_insights_jobs_agent_id", "insights_jobs", ["agent_id"])

    op.create_table(
        "conversation_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), unique=True),
        sa.Column("agent_id", sa.String(36), nullable=True),
        sa.Column("start_stars", sa.Integer(), nullable=True),
        sa.Column("end_stars", sa.Integer(), nullable=True),
        sa.Column("delta_stars", sa.Integer(), nullable=True),
        sa.Column("avg_stars", sa.Float(), nullable=True),
        sa.Column("low_point_stars", sa.Integer(), nullable=True),
        sa.Column("high_point_stars", sa.Integer(), nullable=True),
        sa.Column("trajectory", sa.String(50), nullable=True),
        sa.Column("timeline_json", sa.Text(), nullable=True),
        sa.Column("first_response_seconds", sa.Float(), nullable=True),
        sa.Column("median_response_seconds", sa.Float(), nullable=True),
        sa.Column("max_response_seconds", sa.Float(), nullable=True),
        sa.Column("unanswered_member_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_messages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("member_messages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("agent_messages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("depth_ratio", sa.Float(), nullable=True),
        sa.Column("depth_bucket", sa.String(20), nullable=True),
        sa.Column("intent_label", sa.String(100), nullable=True),
        sa.Column("intent_score", sa.Float(), nullable=True),
        sa.Column("unanswered_question_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unanswered_no_reply_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unanswered_weak_answer_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unanswered_semantic_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_unanswered_question_text", sa.Text(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_conversation_metrics_agent_id", "conversation_metrics", ["agent_id"])

    op.create_table(
        "unanswered_questions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id", ondelete="CASCADE"), unique=True),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id", ondelete="CASCADE")),
        sa.Column("agent_id", sa.String(36), nullable=True),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("agent_reply_message_id", sa.Integer(), sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("agent_reply_text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("similarity_score", sa.Float(), nullable=True),
        sa.Column("nli_label", sa.String(100), nullable=True),
        sa.Column("nli_score", sa.Float(), nullable=True),
        sa.Column("intent_label", sa.String(100), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_unanswered_questions_agent_id", "unanswered_questions", ["agent_id"])

    op.create_table(
        "question_clusters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("insights_job_id", sa.Integer(), sa.ForeignKey("insights_jobs.id", ondelete="CASCADE")),
        sa.Column("agent_id", sa.String(36), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("representative_text", sa.Text(), nullable=False),
        sa.Column("examples_json", sa.Text(), nullable=True),
        sa.Column("intent_label", sa.String(100), nullable=True),
        sa.Column("intent_score", sa.Float(), nullable=True),
    )
    op.create_index("ix_question_clusters_agent_id", "question_clusters", ["agent_id"])


def downgrade() -> None:
    op.drop_table("question_clusters")
    op.drop_table("unanswered_questions")
    op.drop_table("conversation_metrics")
    op.drop_table("insights_jobs")
