from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

RebuildStatus = Literal[
    "not_started",
    "queued",
    "running",
    "partially_failed",
    "failed",
    "succeeded",
]


class KnowledgeBaseRebuildResponse(BaseModel):
    knowledge_base_id: UUID
    operation_id: UUID | None
    status: RebuildStatus
    document_versions_total: int = 0
    document_versions_parsed: int = 0
    document_versions_failed: int = 0
    documents_total: int = 0
    documents_indexed: int = 0
    documents_failed: int = 0
    knowledge_entries_total: int = 0
    knowledge_entries_indexed: int = 0
    knowledge_entries_failed: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
