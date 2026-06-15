from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class InsightsRunCreate(BaseModel):
    agent_id: str = Field(min_length=1, max_length=36)


class InsightsJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_id: str
    status: str
    phase: str
    processed: int
    limit: int | None = None
    failed: int
    messages_analyzed: int
    phase_detail: str | None = None
    phase_progress: int = 0
    phase_total: int = 0
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class InsightsStartResponse(BaseModel):
    job_id: int
    status: str


class InsightsDeleteResponse(BaseModel):
    agent_id: str
    deleted: dict[str, int]


class DepthDistribution(BaseModel):
    shallow: int = 0
    medium: int = 0
    deep: int = 0


class UnansweredBreakdown(BaseModel):
    no_reply: int = 0
    weak_answer: int = 0
    not_answered: int = 0


class InsightsOverview(BaseModel):
    agent_id: str
    conversation_count: int
    average_stars: float | None = None
    improving_pct: float = 0
    worsening_pct: float = 0
    mixed_pct: float = 0
    median_response_seconds: float | None = None
    p95_response_seconds: float | None = None
    sla_met_pct: float | None = None
    depth_distribution: DepthDistribution
    intent_breakdown: dict[str, int] = Field(default_factory=dict)
    unanswered_pct: float = 0
    unanswered_breakdown: UnansweredBreakdown


class QuestionClusterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rank: int
    count: int
    representative_text: str
    examples_json: str | None = None
    intent_label: str | None = None
    intent_score: float | None = None


class UnansweredQuestionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    message_id: int
    conversation_id: int
    question_text: str
    agent_reply_text: str | None = None
    status: str
    similarity_score: float | None = None
    nli_label: str | None = None
    nli_score: float | None = None
    intent_label: str | None = None


class TimelinePoint(BaseModel):
    message_id: int
    role: str
    content: str
    source_created_at: str
    response_seconds: float | None = None
    sentiment: dict | None = None
    unanswered_status: str | None = None
    similarity_score: float | None = None
    nli_label: str | None = None


class ConversationTimeline(BaseModel):
    conversation_id: int
    title: str
    trajectory: str | None = None
    intent_label: str | None = None
    depth_bucket: str | None = None
    timeline: list[dict] = Field(default_factory=list)
    messages: list[TimelinePoint] = Field(default_factory=list)
