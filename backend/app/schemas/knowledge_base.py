from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def normalize_name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Knowledge base name must not be empty")
    return normalized


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(max_length=100)
    description: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return normalize_name(value)


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    description: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("Knowledge base name must not be null")
        return normalize_name(value)

    @model_validator(mode="after")
    def validate_at_least_one_field(self) -> Self:
        if not self.model_fields_set.intersection({"name", "description"}):
            raise ValueError("At least one modifiable field is required")
        return self


class KnowledgeBaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime


class KnowledgeBaseListResponse(BaseModel):
    items: list[KnowledgeBaseResponse]
    total: int
    offset: int
    limit: int
