from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

ARCHIVE_FORMAT: Literal["tracemind.knowledge-base"] = "tracemind.knowledge-base"
ARCHIVE_VERSION: Literal[1] = 1

KNOWLEDGE_BASE_DATA_PATH = "data/knowledge_base.json"
DOCUMENTS_DATA_PATH = "data/documents.jsonl"
DOCUMENT_VERSIONS_DATA_PATH = "data/document_versions.jsonl"
CONVERSATIONS_DATA_PATH = "data/conversations.jsonl"
MESSAGES_DATA_PATH = "data/messages.jsonl"
KNOWLEDGE_ENTRIES_DATA_PATH = "data/knowledge_entries.jsonl"


class StrictArchiveModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class KnowledgeBaseArchiveRecord(StrictArchiveModel):
    model_config = ConfigDict(extra="forbid", strict=True, from_attributes=True)

    id: UUID
    name: str = Field(min_length=1, max_length=100)
    description: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class DocumentArchiveRecord(StrictArchiveModel):
    model_config = ConfigDict(extra="forbid", strict=True, from_attributes=True)

    id: UUID
    knowledge_base_id: UUID
    name: str = Field(min_length=1, max_length=255)
    relative_path: str = Field(min_length=1, max_length=1024)
    source_type: str = Field(min_length=1, max_length=32)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class DocumentVersionArchiveRecord(StrictArchiveModel):
    model_config = ConfigDict(extra="forbid", strict=True, from_attributes=True)

    id: UUID
    document_id: UUID
    version_number: int = Field(gt=0)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    file_size: int = Field(gt=0)
    mime_type: str | None = Field(default=None, max_length=255)
    extension: str = Field(pattern=r"^\.[a-z0-9]{1,31}$")
    created_at: AwareDatetime


class ConversationArchiveRecord(StrictArchiveModel):
    model_config = ConfigDict(extra="forbid", strict=True, from_attributes=True)

    id: UUID
    knowledge_base_id: UUID
    title: str = Field(min_length=1, max_length=200)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ConversationMessageArchiveRecord(StrictArchiveModel):
    model_config = ConfigDict(extra="forbid", strict=True, from_attributes=True)

    id: UUID
    conversation_id: UUID
    role: Literal["user", "assistant"]
    status: Literal["completed", "no_answer", "failed", "cancelled"]
    content: str
    trace_id: UUID | None
    sources: list[dict[str, Any]] | None
    generation_metadata: dict[str, Any] | None
    created_at: AwareDatetime


class KnowledgeEntryArchiveRecord(StrictArchiveModel):
    model_config = ConfigDict(extra="forbid", strict=True, from_attributes=True)

    id: UUID
    knowledge_base_id: UUID
    question: str
    background: str | None
    root_cause: str | None
    solution: str
    failed_attempts: list[str]
    validation_status: Literal["unverified", "verified", "outdated"]
    tags: list[str]
    source_conversation_id: UUID | None
    source_user_message_id: UUID | None
    source_assistant_message_id: UUID | None
    question_snapshot: str
    answer_snapshot: str
    sources_snapshot: list[dict[str, Any]]
    generation_metadata_snapshot: dict[str, Any] | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ArchiveKnowledgeBaseSummary(StrictArchiveModel):
    id: UUID
    name: str
    description: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ArchiveEntityCounts(StrictArchiveModel):
    knowledge_bases: Literal[1] = 1
    documents: int = Field(ge=0)
    document_versions: int = Field(ge=0)
    conversations: int = Field(ge=0)
    messages: int = Field(ge=0)
    knowledge_entries: int = Field(ge=0)


class ArchiveDataEntry(StrictArchiveModel):
    path: str
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    record_count: int = Field(ge=0)


class ArchiveDocumentFileEntry(StrictArchiveModel):
    document_version_id: UUID
    path: str
    size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class KnowledgeBaseArchiveManifest(StrictArchiveModel):
    archive_format: Literal["tracemind.knowledge-base"] = ARCHIVE_FORMAT
    archive_version: Literal[1] = ARCHIVE_VERSION
    archive_id: UUID
    tracemind_version: str = Field(min_length=1)
    exported_at: AwareDatetime
    knowledge_base: ArchiveKnowledgeBaseSummary
    entity_counts: ArchiveEntityCounts
    data_entries: list[ArchiveDataEntry]
    document_files: list[ArchiveDocumentFileEntry]


class KnowledgeBaseArchiveRestoreResponse(StrictArchiveModel):
    knowledge_base_id: UUID
    archive_id: UUID
    entity_counts: ArchiveEntityCounts
    restore_status: Literal["succeeded"] = "succeeded"
    rebuild_status: Literal["not_started"] = "not_started"


class RestoreJournalFile(StrictArchiveModel):
    document_version_id: UUID
    path: str
    size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class KnowledgeBaseRestoreJournal(StrictArchiveModel):
    journal_version: Literal[1] = 1
    operation_id: UUID
    knowledge_base_id: UUID
    staging_path: str
    final_path: str
    promoted: bool = False
    created_at: AwareDatetime
    files: list[RestoreJournalFile]
