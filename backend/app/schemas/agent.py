from pydantic import BaseModel, Field


class AgentPurgeResponse(BaseModel):
    agent_id: str
    deleted: dict[str, int] = Field(default_factory=dict)


class AgentOption(BaseModel):
    id: str
    name: str
