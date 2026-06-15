import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.integrations.momants_client import get_momants_client
from app.schemas.agent import AgentOption, AgentPurgeResponse
from app.services.agent_service import AgentService

logger = logging.getLogger(__name__)

router = APIRouter()


def get_service(db: Session = Depends(get_db)) -> AgentService:
    return AgentService(db)


@router.get("", response_model=list[AgentOption])
def list_agents() -> list[AgentOption]:
    """List the account's agents (id + name) from Momants, sorted by name."""
    client = get_momants_client()
    try:
        agents = client.list_agents()
    except Exception as exc:
        logger.exception("Failed to list agents from Momants")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not load agents from Momants: {exc}",
        ) from exc
    finally:
        client.close()

    options = [
        AgentOption(id=agent["id"], name=agent["name"] or f"Agent {agent['id'][:8]}")
        for agent in agents
    ]
    options.sort(key=lambda option: option.name.lower())
    return options


@router.delete("/{agent_id}", response_model=AgentPurgeResponse)
def delete_all_agent_data(
    agent_id: str,
    service: AgentService = Depends(get_service),
) -> AgentPurgeResponse:
    deleted = service.purge_all(agent_id)
    return AgentPurgeResponse(agent_id=agent_id, deleted=deleted)


@router.delete("/{agent_id}/insights", response_model=AgentPurgeResponse)
def delete_agent_insights(
    agent_id: str,
    service: AgentService = Depends(get_service),
) -> AgentPurgeResponse:
    deleted = service.purge_insights(agent_id)
    return AgentPurgeResponse(agent_id=agent_id, deleted=deleted)


@router.delete("/{agent_id}/conversations", response_model=AgentPurgeResponse)
def delete_agent_conversations(
    agent_id: str,
    purge_insights: bool = Query(default=True, description="Also remove orphaned insights and top questions"),
    service: AgentService = Depends(get_service),
) -> AgentPurgeResponse:
    conversations_deleted = service.purge_conversations(agent_id)
    deleted = {"conversations": conversations_deleted}
    if purge_insights:
        deleted.update(service.purge_insights(agent_id))
    else:
        service.clear_agent_cache(agent_id)
    return AgentPurgeResponse(agent_id=agent_id, deleted=deleted)
