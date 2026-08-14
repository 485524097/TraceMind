from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class RagStreamRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2_000)
    language: str | None = Field(default=None, max_length=32)
    document_id: UUID | None = None
    conversation_id: UUID | None = None

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value

    @field_validator("language")
    @classmethod
    def normalize_language(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class RagSource(BaseModel):
    source_id: str
    source_type: Literal["document", "knowledge_entry"] = "document"
    score: float
    content: str
    knowledge_base_id: UUID
    document_id: UUID | None = None
    document_version_id: UUID | None = None
    chunk_id: UUID
    index_generation: UUID
    document_name: str | None = None
    relative_path: str | None = None
    version_number: int | None = None
    chunk_index: int
    content_hash: str
    chunk_type: str
    language: str | None = None
    section_title: str | None = None
    page_number: int | None = None
    start_line: int | None = None
    end_line: int | None = None
    knowledge_entry_id: UUID | None = None
    knowledge_question: str | None = None
    knowledge_updated_at: datetime | None = None
    ranking_mode: str | None = None
    retrieval_score: float | None = None
    rerank_score: float | None = None
    retrieval_rank: int | None = None

    @model_validator(mode="after")
    def validate_source_identity(self) -> Self:
        if self.source_type == "document":
            required = (
                self.document_id,
                self.document_version_id,
                self.document_name,
                self.relative_path,
                self.version_number,
            )
            if any(value is None for value in required):
                raise ValueError("Document source identity is incomplete")
        elif self.knowledge_entry_id is None or not self.knowledge_question:
            raise ValueError("Knowledge entry source identity is incomplete")
        return self
