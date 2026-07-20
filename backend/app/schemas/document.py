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
    document: DocumentResponse
