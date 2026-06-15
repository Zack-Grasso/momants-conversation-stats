from datetime import datetime

from pydantic import BaseModel, Field


class IngestCreate(BaseModel):
    agent_id: str = Field(min_length=1, max_length=36)
    limit: int = Field(ge=1, le=10000)
    reanalyze: bool = False


class IngestJobRead(BaseModel):
    id: int
    agent_id: str
    limit: int
    reanalyze: bool
    status: str
    processed: int
    skipped: int = 0
    failed: int
    messages_analyzed: int
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class IngestStartResponse(BaseModel):
    job_id: int
    status: str
