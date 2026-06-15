from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.cache_read import require_cached_model, require_cached_model_list
from app.cache import conversation_cache_key, conversations_cache_key
from app.database import get_db
from app.schemas.conversation import (
    ConversationCreate,
    ConversationRead,
    ConversationStats,
    DeleteResponse,
    MessageCreate,
    MessageRead,
)
from app.schemas.insights import ConversationTimeline
from app.services.agent_service import AgentService
from app.services.conversation_service import ConversationService

router = APIRouter()


def get_service(db: Session = Depends(get_db)) -> ConversationService:
    return ConversationService(db)


@router.get("", response_model=list[ConversationRead])
def list_conversations(agent_id: str = Query(..., min_length=1)) -> list[ConversationRead]:
    return require_cached_model_list(conversations_cache_key(agent_id, "list"), ConversationRead)


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: ConversationCreate,
    service: ConversationService = Depends(get_service),
) -> ConversationRead:
    return service.create(payload)


@router.get("/review/sample", response_model=list[ConversationRead])
def review_sample(
    agent_id: str = Query(..., min_length=1),
    count: int = Query(default=5, ge=1, le=20),
) -> list[ConversationRead]:
    return require_cached_model_list(
        conversations_cache_key(agent_id, f"review_sample:{count}"),
        ConversationRead,
    )


@router.delete("/all", response_model=DeleteResponse)
def delete_all_conversations(service: ConversationService = Depends(get_service)) -> DeleteResponse:
    deleted = service.delete_all()
    return DeleteResponse(deleted=deleted)


@router.delete("", response_model=DeleteResponse)
def delete_conversations_by_agent(
    agent_id: str = Query(min_length=1),
    db: Session = Depends(get_db),
) -> DeleteResponse:
    agent_service = AgentService(db)
    deleted = agent_service.purge_conversations(agent_id)
    agent_service.purge_insights(agent_id)
    return DeleteResponse(deleted=deleted)


@router.delete("/{conversation_id}", response_model=DeleteResponse)
def delete_conversation(
    conversation_id: int,
    service: ConversationService = Depends(get_service),
) -> DeleteResponse:
    if not service.delete(conversation_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return DeleteResponse(deleted=1)


@router.get("/{conversation_id}", response_model=ConversationRead)
def get_conversation(conversation_id: int) -> ConversationRead:
    return require_cached_model(conversation_cache_key(conversation_id, "detail"), ConversationRead)


@router.post("/{conversation_id}/messages", response_model=MessageRead, status_code=status.HTTP_201_CREATED)
def add_message(
    conversation_id: int,
    payload: MessageCreate,
    service: ConversationService = Depends(get_service),
) -> MessageRead:
    message = service.add_message(conversation_id, payload.role, payload.content)
    if message is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return message


@router.get("/{conversation_id}/stats", response_model=ConversationStats)
def get_conversation_stats(conversation_id: int) -> ConversationStats:
    return require_cached_model(conversation_cache_key(conversation_id, "stats"), ConversationStats)


@router.get("/{conversation_id}/timeline", response_model=ConversationTimeline)
def get_conversation_timeline(conversation_id: int) -> ConversationTimeline:
    return require_cached_model(conversation_cache_key(conversation_id, "timeline"), ConversationTimeline)
