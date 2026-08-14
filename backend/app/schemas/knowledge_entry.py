from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ValidationStatus = Literal["unverified", "verified", "outdated"]
KnowledgeIndexStatus = Literal["not_indexed", "pending", "processing", "succeeded", "failed"]


def normalize_required_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Value must not be empty")
    return normalized


def normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def normalize_tags(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip().casefold()
        if not normalized or normalized in seen:
            continue
        if len(normalized) > 50:
            raise ValueError("Each tag must contain at most 50 characters")
        seen.add(normalized)
        result.append(normalized)
    if len(result) > 20:
        raise ValueError("At most 20 tags are allowed")
    return result


def normalize_failed_attempts(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized:
            continue
        if len(normalized) > 5_000:
            raise ValueError("Each failed attempt must contain at most 5000 characters")
        result.append(normalized)
    if len(result) > 20:
        raise ValueError("At most 20 failed attempts are allowed")
    return result


class KnowledgeEntryCreate(BaseModel):
    source_assistant_message_id: UUID
    question: str = Field(min_length=1, max_length=4_000)
    background: str | None = Field(default=None, max_length=20_000)
    root_cause: str | None = Field(default=None, max_length=20_000)
    solution: str = Field(min_length=1, max_length=50_000)
    failed_attempts: list[str] = Field(default_factory=list)
    validation_status: ValidationStatus = "unverified"
    tags: list[str] = Field(default_factory=list)

    _question = field_validator("question")(normalize_required_text)
    _solution = field_validator("solution")(normalize_required_text)
    _optional = field_validator("background", "root_cause")(normalize_optional_text)
    _tags = field_validator("tags")(normalize_tags)
    _failed_attempts = field_validator("failed_attempts")(normalize_failed_attempts)


class KnowledgeEntryUpdate(BaseModel):
    question: str | None = Field(default=None, min_length=1, max_length=4_000)
    background: str | None = Field(default=None, max_length=20_000)
    root_cause: str | None = Field(default=None, max_length=20_000)
    solution: str | None = Field(default=None, min_length=1, max_length=50_000)
    failed_attempts: list[str] | None = None
    validation_status: ValidationStatus | None = None
    tags: list[str] | None = None

    @field_validator("question", "solution")
    @classmethod
    def validate_required_update(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("Required knowledge fields must not be null")
        return normalize_required_text(value)

    _optional = field_validator("background", "root_cause")(normalize_optional_text)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str] | None) -> list[str] | None:
        return normalize_tags(value) if value is not None else None

    @field_validator("failed_attempts")
    @classmethod
    def validate_failed_attempts(cls, value: list[str] | None) -> list[str] | None:
        return normalize_failed_attempts(value) if value is not None else None

    @field_validator("validation_status")
    @classmethod
    def validate_status(cls, value: ValidationStatus | None) -> ValidationStatus:
        if value is None:
            raise ValueError("Validation status must not be null")
        return value

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one modifiable field is required")
        return self


class KnowledgeEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    knowledge_base_id: UUID
    question: str
    background: str | None
    root_cause: str | None
    solution: str
    failed_attempts: list[str]
    validation_status: ValidationStatus
    tags: list[str]
    source_conversation_id: UUID | None
    source_user_message_id: UUID | None
    source_assistant_message_id: UUID | None
    question_snapshot: str
    answer_snapshot: str
    sources_snapshot: list[dict[str, Any]]
    generation_metadata_snapshot: dict[str, Any] | None
    index_status: KnowledgeIndexStatus
    active_index_generation: UUID | None
    index_started_at: datetime | None
    indexed_at: datetime | None
    indexed_chunk_count: int
    embedding_model: str | None
    embedding_dimension: int | None
    index_error_code: str | None
    index_error_message: str | None
    created_at: datetime
    updated_at: datetime


class KnowledgeEntryListResponse(BaseModel):
    items: list[KnowledgeEntryResponse]
    total: int
    offset: int
    limit: int
    available_tags: list[str]


class KnowledgeEntryIndexRequest(BaseModel):
    force: bool = False
