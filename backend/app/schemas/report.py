from pydantic import BaseModel, Field


class ReportContextResponse(BaseModel):
    agent_id: str
    event_name: str
    variables: dict[str, str] = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list)
    static_sections: list[str] = Field(default_factory=list)
    charts_generated: bool = False
    chart_source: str = "local"
