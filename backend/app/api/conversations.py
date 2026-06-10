from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.conversation import (
    ConversationCreate,
    ConversationRead,
    ConversationStats,
    MessageCreate,
    MessageRead,
)
from app.services.conversation_service import ConversationService

router = APIRouter()


def get_service(db: Session = Depends(get_db)) -> ConversationService:
    return ConversationService(db)


@router.get("", response_model=list[ConversationRead])
def list_conversations(service: ConversationService = Depends(get_service)) -> list[ConversationRead]:
    return service.list_conversations()


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: ConversationCreate,
    service: ConversationService = Depends(get_service),
) -> ConversationRead:
    return service.create(payload)


@router.get("/{conversation_id}", response_model=ConversationRead)
def get_conversation(
    conversation_id: int,
    service: ConversationService = Depends(get_service),
) -> ConversationRead:
    conversation = service.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation


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
def get_conversation_stats(
    conversation_id: int,
    service: ConversationService = Depends(get_service),
) -> ConversationStats:
    stats = service.get_stats(conversation_id)
    if stats is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return stats
