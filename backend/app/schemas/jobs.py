from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class RunningJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_type: Literal["ingest", "insights"]
    id: int
    agent_id: str
    status: str
    processed: int
    limit: int | None = None
    failed: int = 0
    messages_analyzed: int = 0
    reanalyze: bool | None = None
    phase: str | None = None
    phase_detail: str | None = None
    phase_progress: int = 0
    phase_total: int = 0
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class RunningJobsResponse(BaseModel):
    jobs: list[RunningJobRead]
    global_running: int
    global_limit: int
    ingest_running: int
    ingest_limit: int
    ingest_slots_left: int
    insights_running: int
    insights_limit: int
    insights_slots_left: int
    agent_running: int
