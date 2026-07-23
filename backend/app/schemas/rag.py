from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class RagStreamRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2_000)
    language: str | None = Field(default=None, max_length=32)
    document_id: UUID | None = None

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
