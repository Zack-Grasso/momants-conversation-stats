from fastapi import APIRouter

from app.api import conversations, health

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(conversations.router, prefix="/conversations", tags=["conversations"])
