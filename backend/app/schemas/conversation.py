from datetime import datetime
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_CONVERSATION_TITLE = "新会话"


def normalize_title(value: str) -> str:
    title = value.strip()
    if not title:
        raise ValueError("Conversation title must not be empty")
    return title


class ConversationCreate(BaseModel):
    title: str = Field(default=DEFAULT_CONVERSATION_TITLE, max_length=200)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return normalize_title(value)


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("Conversation title must not be null")
        return normalize_title(value)

    @model_validator(mode="after")
    def require_title(self) -> Self:
        if "title" not in self.model_fields_set:
            raise ValueError("Conversation title is required")
        return self


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    knowledge_base_id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    role: str
    status: str
    content: str
    trace_id: UUID | None
    sources: list[dict[str, Any]] | None
    generation_metadata: dict[str, Any] | None
    created_at: datetime


class ConversationDetailResponse(ConversationResponse):
    messages: list[ConversationMessageResponse]


class ConversationListResponse(BaseModel):
    items: list[ConversationResponse]
    total: int
    offset: int
    limit: int
