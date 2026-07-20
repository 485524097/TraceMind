from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DocumentImportAction(StrEnum):
    created = "created"
    version_created = "version_created"
    unchanged = "unchanged"


class DocumentVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version_number: int
    content_hash: str
    file_size: int
    mime_type: str | None
    extension: str
    created_at: datetime
    parse_status: str
    parser_name: str | None
    parser_version: str | None
    chunk_count: int
    parse_started_at: datetime | None
    parsed_at: datetime | None
    last_parse_attempt_at: datetime | None
    parse_error_code: str | None
    parse_error_message: str | None
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


class DocumentResponse(BaseModel):
    id: UUID
    knowledge_base_id: UUID
    name: str
    source_type: str
    created_at: datetime
    updated_at: datetime
    version_count: int
    latest_version: DocumentVersionResponse


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]
    total: int
    offset: int
    limit: int


class DocumentImportResponse(BaseModel):
    import_action: DocumentImportAction
    parsing_queued: bool
    document: DocumentResponse


class DocumentParseStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version_id: UUID
    parse_status: str
    parser_name: str | None
    parser_version: str | None
    chunk_count: int
    parse_started_at: datetime | None
    parsed_at: datetime | None
    last_parse_attempt_at: datetime | None
    parse_error_code: str | None
    parse_error_message: str | None


class DocumentParseRequestResponse(BaseModel):
    queued: bool
    version: DocumentParseStatusResponse


class DocumentChunkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    chunk_index: int
    content: str
    content_hash: str
    char_count: int
    page_number: int | None
    start_line: int | None
    end_line: int | None
    section_title: str | None
    chunk_type: str
    language: str | None
    created_at: datetime


class DocumentChunkListResponse(BaseModel):
    items: list[DocumentChunkResponse]
    total: int
    offset: int
    limit: int
    version: DocumentParseStatusResponse
