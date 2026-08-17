import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4
from zipfile import ZIP_STORED, ZipFile

from app.schemas.knowledge_base_archive import (
    CONVERSATIONS_DATA_PATH,
    DOCUMENT_VERSIONS_DATA_PATH,
    DOCUMENTS_DATA_PATH,
    KNOWLEDGE_BASE_DATA_PATH,
    KNOWLEDGE_ENTRIES_DATA_PATH,
    MESSAGES_DATA_PATH,
    ArchiveDataEntry,
    ArchiveDocumentFileEntry,
    ArchiveEntityCounts,
    ArchiveKnowledgeBaseSummary,
    ConversationArchiveRecord,
    ConversationMessageArchiveRecord,
    DocumentArchiveRecord,
    DocumentVersionArchiveRecord,
    KnowledgeBaseArchiveManifest,
    KnowledgeBaseArchiveRecord,
    KnowledgeEntryArchiveRecord,
    StrictArchiveModel,
)
from app.storage.archive import MANIFEST_PATH, document_file_archive_path


@dataclass(frozen=True)
class RestoreArchiveFixture:
    path: Path
    knowledge_base_id: UUID
    document_id: UUID
    version_id: UUID
    conversation_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID
    knowledge_entry_id: UUID
    content: bytes
    created_at: datetime


def json_bytes(model: StrictArchiveModel) -> bytes:
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def jsonl_bytes(models: list[StrictArchiveModel]) -> bytes:
    if not models:
        return b""
    return b"\n".join(json_bytes(model) for model in models) + b"\n"


def build_restore_archive(tmp_path: Path) -> RestoreArchiveFixture:
    created_at = datetime(2026, 8, 14, 9, 15, tzinfo=UTC)
    knowledge_base_id, document_id, version_id = uuid4(), uuid4(), uuid4()
    conversation_id, user_message_id, assistant_message_id = uuid4(), uuid4(), uuid4()
    knowledge_entry_id = uuid4()
    content = b"restore source of truth"
    content_hash = hashlib.sha256(content).hexdigest()
    knowledge_base = KnowledgeBaseArchiveRecord(
        id=knowledge_base_id,
        name=f"Restore Fixture {knowledge_base_id}",
        description="source of truth",
        created_at=created_at,
        updated_at=created_at,
    )
    document = DocumentArchiveRecord(
        id=document_id,
        knowledge_base_id=knowledge_base_id,
        name="Guide.MD",
        relative_path="Docs/Guide.MD",
        source_type="upload",
        created_at=created_at,
        updated_at=created_at,
    )
    version = DocumentVersionArchiveRecord(
        id=version_id,
        document_id=document_id,
        version_number=1,
        content_hash=content_hash,
        file_size=len(content),
        mime_type="text/markdown",
        extension=".md",
        created_at=created_at,
    )
    conversation = ConversationArchiveRecord(
        id=conversation_id,
        knowledge_base_id=knowledge_base_id,
        title="Restore conversation",
        created_at=created_at,
        updated_at=created_at,
    )
    user_message = ConversationMessageArchiveRecord(
        id=user_message_id,
        conversation_id=conversation_id,
        role="user",
        status="completed",
        content="Why?",
        trace_id=uuid4(),
        sources=None,
        generation_metadata=None,
        created_at=created_at,
    )
    assistant_message = ConversationMessageArchiveRecord(
        id=assistant_message_id,
        conversation_id=conversation_id,
        role="assistant",
        status="completed",
        content="Because.",
        trace_id=uuid4(),
        sources=[{"document_id": str(document_id), "quote": "evidence"}],
        generation_metadata={"provider": "fixture", "latency_ms": 8},
        created_at=created_at,
    )
    knowledge_entry = KnowledgeEntryArchiveRecord(
        id=knowledge_entry_id,
        knowledge_base_id=knowledge_base_id,
        question="Why?",
        background="Background",
        root_cause="Cause",
        solution="Solution",
        failed_attempts=["Attempt"],
        validation_status="verified",
        tags=["restore"],
        source_conversation_id=conversation_id,
        source_user_message_id=user_message_id,
        source_assistant_message_id=assistant_message_id,
        question_snapshot="Why?",
        answer_snapshot="Because.",
        sources_snapshot=[{"document_id": str(document_id), "quote": "evidence"}],
        generation_metadata_snapshot={"provider": "fixture", "latency_ms": 8},
        created_at=created_at,
        updated_at=created_at,
    )
    data = {
        KNOWLEDGE_BASE_DATA_PATH: json_bytes(knowledge_base),
        DOCUMENTS_DATA_PATH: jsonl_bytes([document]),
        DOCUMENT_VERSIONS_DATA_PATH: jsonl_bytes([version]),
        CONVERSATIONS_DATA_PATH: jsonl_bytes([conversation]),
        MESSAGES_DATA_PATH: jsonl_bytes([user_message, assistant_message]),
        KNOWLEDGE_ENTRIES_DATA_PATH: jsonl_bytes([knowledge_entry]),
    }
    counts = {
        KNOWLEDGE_BASE_DATA_PATH: 1,
        DOCUMENTS_DATA_PATH: 1,
        DOCUMENT_VERSIONS_DATA_PATH: 1,
        CONVERSATIONS_DATA_PATH: 1,
        MESSAGES_DATA_PATH: 2,
        KNOWLEDGE_ENTRIES_DATA_PATH: 1,
    }
    file_path = document_file_archive_path(version_id, ".md")
    manifest = KnowledgeBaseArchiveManifest(
        archive_id=uuid4(),
        tracemind_version="fixture",
        exported_at=created_at,
        knowledge_base=ArchiveKnowledgeBaseSummary(**knowledge_base.model_dump()),
        entity_counts=ArchiveEntityCounts(
            documents=1,
            document_versions=1,
            conversations=1,
            messages=2,
            knowledge_entries=1,
        ),
        data_entries=[
            ArchiveDataEntry(
                path=path,
                size=len(value),
                sha256=hashlib.sha256(value).hexdigest(),
                record_count=counts[path],
            )
            for path, value in data.items()
        ],
        document_files=[
            ArchiveDocumentFileEntry(
                document_version_id=version_id,
                path=file_path,
                size=len(content),
                sha256=content_hash,
            )
        ],
    )
    path = tmp_path / "fixture.tracemind.zip"
    with ZipFile(path, mode="w", compression=ZIP_STORED) as archive:
        archive.writestr(MANIFEST_PATH, json_bytes(manifest))
        for entry_path, value in data.items():
            archive.writestr(entry_path, value)
        archive.writestr(file_path, content)
    return RestoreArchiveFixture(
        path=path,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        version_id=version_id,
        conversation_id=conversation_id,
        user_message_id=user_message_id,
        assistant_message_id=assistant_message_id,
        knowledge_entry_id=knowledge_entry_id,
        content=content,
        created_at=created_at,
    )


def rewrite_archive(
    source: Path,
    destination: Path,
    replacements: dict[str, bytes | None],
    *,
    extra_entries: list[tuple[str, bytes]] | None = None,
) -> Path:
    with ZipFile(source, mode="r") as original:
        entries = [(info.filename, original.read(info.filename)) for info in original.infolist()]
    with ZipFile(destination, mode="w", compression=ZIP_STORED) as rewritten:
        for name, content in entries:
            replacement = replacements.get(name, content)
            if replacement is not None:
                rewritten.writestr(name, replacement)
        for name, content in extra_entries or []:
            rewritten.writestr(name, content)
    return destination
