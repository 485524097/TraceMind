from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class DocumentIndexRequest(BaseModel):
    force: bool = False


class DocumentIndexStatusResponse(BaseModel):
    version_id: UUID
    index_status: str
    active_index_generation: UUID | None
    index_attempt_generation: UUID | None
    index_started_at: datetime | None
    indexed_at: datetime | None
    last_index_attempt_at: datetime | None
    indexed_chunk_count: int
    embedding_model: str | None
    embedding_dimension: int | None
    index_error_code: str | None
    index_error_message: str | None


class DocumentIndexRequestResponse(BaseModel):
    queued: bool
    version: DocumentIndexStatusResponse


class SemanticSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2_000)
    limit: int = Field(default=10, ge=1, le=50)
    language: str | None = Field(default=None, max_length=32)
    document_id: UUID | None = None

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        return stripped

    @field_validator("language")
    @classmethod
    def normalize_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class SemanticSearchResultResponse(BaseModel):
    score: float
    content: str
    knowledge_base_id: UUID
    document_id: UUID
    document_version_id: UUID
    chunk_id: UUID
    index_generation: UUID
    document_name: str
    version_number: int
    chunk_index: int
    content_hash: str
    chunk_type: str
    language: str | None
    section_title: str | None
    page_number: int | None
    start_line: int | None
    end_line: int | None
    ranking_mode: str | None = None
    retrieval_score: float | None = None
    rerank_score: float | None = None
    retrieval_rank: int | None = None


class SemanticSearchResponse(BaseModel):
    items: list[SemanticSearchResultResponse]
