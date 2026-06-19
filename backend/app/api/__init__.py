from fastapi import APIRouter, Depends

from app.api import (
    agents,
    auth,
    conversations,
    health,
    ingest,
    insights,
    intent,
    jobs,
    pipeline,
    reports,
    scheduler,
    sentiment,
    slack,
    system,
)
from app.weekly.api.router import router as weekly_router
from app.auth.deps import get_current_user

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(slack.router, prefix="/slack", tags=["slack"])

protected = APIRouter(dependencies=[Depends(get_current_user)])
protected.include_router(agents.router, prefix="/agents", tags=["agents"])
protected.include_router(conversations.router, prefix="/conversations", tags=["conversations"])
protected.include_router(ingest.router, prefix="/ingest", tags=["ingest"])
protected.include_router(sentiment.router, prefix="/sentiment", tags=["sentiment"])
protected.include_router(intent.router, prefix="/intent", tags=["intent"])
protected.include_router(insights.router, prefix="/insights", tags=["insights"])
protected.include_router(reports.router, prefix="/reports", tags=["reports"])
protected.include_router(weekly_router, prefix="/weekly", tags=["weekly"])
protected.include_router(pipeline.router, prefix="/pipeline", tags=["pipeline"])
protected.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
protected.include_router(scheduler.router, prefix="/scheduler", tags=["scheduler"])
protected.include_router(system.router, prefix="/system", tags=["system"])
api_router.include_router(protected)
