from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, Field


class WeeklyTopQuestion(BaseModel):
    rank: int
    count: int
    text: str


class WeeklyAgentSummary(BaseModel):
    agent_id: str
    agent_name: str | None = None
    status: str
    error: str | None = None
    pdf_available: bool = False
    counts: dict[str, int] = Field(default_factory=dict)
    top_questions: list[WeeklyTopQuestion] = Field(default_factory=list)
    value_stats: dict | None = None


class WeeklyRunSummary(BaseModel):
    week_id: str
    since: datetime
    until: datetime
    status: str
    agent_count: int = 0
    counts: dict[str, int] = Field(default_factory=dict)
    zip_available: bool = False
    agents: list[WeeklyAgentSummary] = Field(default_factory=list)


class WeeklySettingsResponse(BaseModel):
    cron: str
    days: int
    enabled: bool
    agent_id: str
    scoped: bool
    running: bool
    next_run_at: datetime | None = None
    run_state: dict | None = None


class WeeklySettingsUpdate(BaseModel):
    cron: str | None = None
    days: int | None = Field(default=None, ge=1, le=30)
    enabled: bool | None = None
    agent_id: str | None = None


class WeeklyRunTriggerResponse(BaseModel):
    status: str
    message: str
