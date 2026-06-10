from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SentimentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    label: str
    score: float
    model_name: str
    analyzed_at: datetime


class MessageCreate(BaseModel):
    role: str = Field(min_length=1, max_length=50)
    content: str = Field(min_length=1)


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    created_at: datetime
    sentiment: SentimentRead | None = None


class ConversationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    messages: list[MessageCreate] = Field(default_factory=list)


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    created_at: datetime
    messages: list[MessageRead] = Field(default_factory=list)


class ConversationStats(BaseModel):
    conversation_id: int
    title: str
    message_count: int
    positive_count: int
    negative_count: int
    average_sentiment_score: float | None
