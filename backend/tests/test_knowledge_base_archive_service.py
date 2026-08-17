import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, ConversationMessage
from app.models.document import Document, DocumentVersion
from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_entry import KnowledgeEntry
from app.repositories.knowledge_base_archive import (
    KnowledgeBaseArchiveRepository,
    KnowledgeBaseArchiveSnapshot,
)
from app.schemas.knowledge_base_archive import ARCHIVE_FORMAT, ARCHIVE_VERSION
from app.services.exceptions import ArchiveSourceIntegrityError, KnowledgeBaseNotFoundError
from app.services.knowledge_base_archive import KnowledgeBaseArchiveService
from app.storage.archive import ArchiveLimits, LocalArchiveStorage
from app.storage.local import LocalFileStorage


class FakeArchiveRepository:
    def __init__(self, snapshot: KnowledgeBaseArchiveSnapshot | None) -> None:
        self.snapshot = snapshot
        self.calls: list[UUID] = []

    async def load_export_snapshot(
        self, knowledge_base_id: UUID
    ) -> KnowledgeBaseArchiveSnapshot | None:
        self.calls.append(knowledge_base_id)
        return self.snapshot


def archive_limits() -> ArchiveLimits:
    return ArchiveLimits(
        max_upload_size=1_000_000,
        max_single_file_size=100_000,
        max_total_extracted_size=500_000,
        max_entries=100,
        max_json_size=100_000,
        max_jsonl_records=1_000,
        max_compression_ratio=100.0,
        io_chunk_size=4,
    )


def write_version_file(
    storage: LocalFileStorage,
    knowledge_base_id: UUID,
    document_id: UUID,
    version_id: UUID,
    content: bytes,
) -> tuple[str, str]:
    relative_path = storage.final_relative_path(knowledge_base_id, document_id, version_id, ".md")
    path = storage.resolve_relative(relative_path, must_exist=False)
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    return relative_path, hashlib.sha256(content).hexdigest()


def make_snapshot(storage: LocalFileStorage) -> KnowledgeBaseArchiveSnapshot:
    now = datetime(2026, 8, 14, 7, 30, tzinfo=UTC)
    knowledge_base_id, document_id = uuid4(), uuid4()
    first_version_id, second_version_id = uuid4(), uuid4()
    first_path, first_hash = write_version_file(
        storage, knowledge_base_id, document_id, first_version_id, b"historical source"
    )
    second_path, second_hash = write_version_file(
        storage, knowledge_base_id, document_id, second_version_id, b"latest source"
    )
    knowledge_base = KnowledgeBase(
        id=knowledge_base_id,
        name="工程 / 知识库",
        description="Archive fixture",
        created_at=now,
        updated_at=now,
    )
    document = Document(
        id=document_id,
        knowledge_base_id=knowledge_base_id,
        name="Guide.md",
        normalized_name="guide.md",
        relative_path="docs/Guide.md",
        normalized_path="docs/guide.md",
        source_type="upload",
        created_at=now,
        updated_at=now,
    )
    versions = (
        DocumentVersion(
            id=first_version_id,
            document_id=document_id,
            version_number=1,
            content_hash=first_hash,
            file_size=len(b"historical source"),
            mime_type="text/markdown",
            extension=".md",
            storage_path=first_path,
            parse_status="succeeded",
            index_status="succeeded",
            created_at=now,
        ),
        DocumentVersion(
            id=second_version_id,
            document_id=document_id,
            version_number=2,
            content_hash=second_hash,
            file_size=len(b"latest source"),
            mime_type="text/markdown",
            extension=".md",
            storage_path=second_path,
            parse_status="processing",
            index_status="processing",
            created_at=now,
        ),
    )
    conversation_id, user_id, assistant_id = uuid4(), uuid4(), uuid4()
    conversation = Conversation(
        id=conversation_id,
        knowledge_base_id=knowledge_base_id,
        title="Why does it fail?",
        created_at=now,
        updated_at=now,
    )
    messages = (
        ConversationMessage(
            id=user_id,
            conversation_id=conversation_id,
            role="user",
            status="completed",
            content="Why?",
            trace_id=uuid4(),
            sources=None,
            generation_metadata=None,
            created_at=now,
        ),
        ConversationMessage(
            id=assistant_id,
            conversation_id=conversation_id,
            role="assistant",
            status="completed",
            content="Because of the configuration.",
            trace_id=uuid4(),
            sources=[{"document_id": str(document_id), "quote": "real evidence"}],
            generation_metadata={"provider": "fixture", "latency_ms": 12},
            created_at=now,
        ),
    )
    entry = KnowledgeEntry(
        id=uuid4(),
        knowledge_base_id=knowledge_base_id,
        question="Why?",
        background="Local setup",
        root_cause="Configuration",
        solution="Update the setting",
        failed_attempts=["Restarted only"],
        validation_status="verified",
        tags=["configuration"],
        source_conversation_id=conversation_id,
        source_user_message_id=user_id,
        source_assistant_message_id=assistant_id,
        question_snapshot="Why?",
        answer_snapshot="Because of the configuration.",
        sources_snapshot=[{"document_id": str(document_id), "quote": "real evidence"}],
        generation_metadata_snapshot={"provider": "fixture", "latency_ms": 12},
        index_status="succeeded",
        active_index_generation=uuid4(),
        indexed_chunk_count=1,
        embedding_model="should-not-export",
        embedding_dimension=1024,
        created_at=now,
        updated_at=now,
    )
    return KnowledgeBaseArchiveSnapshot(
        knowledge_base=knowledge_base,
        documents=(document,),
        document_versions=versions,
        conversations=(conversation,),
        messages=messages,
        knowledge_entries=(entry,),
    )


def make_service(
    tmp_path: Path, snapshot: KnowledgeBaseArchiveSnapshot | None
) -> tuple[
    KnowledgeBaseArchiveService,
    AsyncMock,
    FakeArchiveRepository,
    LocalArchiveStorage,
]:
    session = AsyncMock(spec=AsyncSession)
    document_storage = LocalFileStorage(tmp_path / "uploads", max_size=1_000, chunk_size=4)
    repository = FakeArchiveRepository(snapshot)
    archive_storage = LocalArchiveStorage(document_storage.root, archive_limits())
    service = KnowledgeBaseArchiveService(
        cast(AsyncSession, session),
        document_storage,
        archive_storage,
        "1.0.0-test",
        cast(KnowledgeBaseArchiveRepository, repository),
    )
    return service, session, repository, archive_storage


def jsonl(archive: ZipFile, path: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in archive.read(path).decode("utf-8").splitlines()]


async def test_export_contains_only_source_of_truth_with_checksums(tmp_path: Path) -> None:
    document_storage = LocalFileStorage(tmp_path / "uploads", max_size=1_000, chunk_size=4)
    snapshot = make_snapshot(document_storage)
    service, session, repository, archive_storage = make_service(tmp_path, snapshot)

    exported = await service.export(snapshot.knowledge_base.id)

    assert repository.calls == [snapshot.knowledge_base.id]
    session.connection.assert_awaited_once_with(
        execution_options={"isolation_level": "REPEATABLE READ"}
    )
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()
    assert exported.filename == "工程-知识库.tracemind.zip"
    assert list(archive_storage.staging_root.iterdir()) == []

    with ZipFile(exported.path) as archive:
        names = archive.namelist()
        assert names == [
            "manifest.json",
            "data/knowledge_base.json",
            "data/documents.jsonl",
            "data/document_versions.jsonl",
            "data/conversations.jsonl",
            "data/messages.jsonl",
            "data/knowledge_entries.jsonl",
            *[
                f"files/document_versions/{version.id}/content.md"
                for version in snapshot.document_versions
            ],
        ]
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["archive_format"] == ARCHIVE_FORMAT
        assert manifest["archive_version"] == ARCHIVE_VERSION
        assert manifest["tracemind_version"] == "1.0.0-test"
        assert manifest["entity_counts"] == {
            "knowledge_bases": 1,
            "documents": 1,
            "document_versions": 2,
            "conversations": 1,
            "messages": 2,
            "knowledge_entries": 1,
        }
        for data_entry in manifest["data_entries"]:
            content = archive.read(data_entry["path"])
            assert data_entry["size"] == len(content)
            assert data_entry["sha256"] == hashlib.sha256(content).hexdigest()
        for file_entry in manifest["document_files"]:
            content = archive.read(file_entry["path"])
            assert file_entry["size"] == len(content)
            assert file_entry["sha256"] == hashlib.sha256(content).hexdigest()

        knowledge_base = json.loads(archive.read("data/knowledge_base.json"))
        assert knowledge_base["id"] == str(snapshot.knowledge_base.id)
        assert (
            datetime.fromisoformat(knowledge_base["created_at"])
            == snapshot.knowledge_base.created_at
        )
        document = jsonl(archive, "data/documents.jsonl")[0]
        assert document["relative_path"] == "docs/Guide.md"
        assert "normalized_name" not in document
        assert "normalized_path" not in document
        version = jsonl(archive, "data/document_versions.jsonl")[0]
        assert "storage_path" not in version
        assert "parse_status" not in version
        assert "index_status" not in version
        message = jsonl(archive, "data/messages.jsonl")[1]
        assert message["sources"] == [
            {"document_id": str(snapshot.documents[0].id), "quote": "real evidence"}
        ]
        assert message["generation_metadata"] == {"latency_ms": 12, "provider": "fixture"}
        entry = jsonl(archive, "data/knowledge_entries.jsonl")[0]
        assert entry["source_assistant_message_id"] == str(snapshot.messages[1].id)
        assert entry["answer_snapshot"] == "Because of the configuration."
        assert entry["sources_snapshot"] == message["sources"]
        assert entry["generation_metadata_snapshot"] == message["generation_metadata"]
        assert "index_status" not in entry
        assert "active_index_generation" not in entry
        assert "embedding_model" not in entry
        assert all("chunk" not in name for name in names)
        assert all("qdrant" not in name.lower() for name in names)

    await service.discard_export(exported.path)
    assert not exported.path.exists()


async def test_missing_knowledge_base_rolls_back_without_creating_archive(
    tmp_path: Path,
) -> None:
    service, session, _, archive_storage = make_service(tmp_path, None)

    with pytest.raises(KnowledgeBaseNotFoundError):
        await service.export(uuid4())

    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    assert list(archive_storage.root.glob("*.tracemind.zip")) == []


async def test_commit_failure_removes_completed_archive(tmp_path: Path) -> None:
    document_storage = LocalFileStorage(tmp_path / "uploads", max_size=1_000, chunk_size=4)
    snapshot = make_snapshot(document_storage)
    service, session, _, archive_storage = make_service(tmp_path, snapshot)
    session.commit.side_effect = OperationalError("commit", {}, Exception("offline"))

    with pytest.raises(OperationalError):
        await service.export(snapshot.knowledge_base.id)

    session.rollback.assert_awaited_once()
    assert list(archive_storage.root.glob("*.tracemind.zip")) == []
    assert list(archive_storage.staging_root.iterdir()) == []


async def test_changed_source_rolls_back_and_does_not_publish_archive(tmp_path: Path) -> None:
    document_storage = LocalFileStorage(tmp_path / "uploads", max_size=1_000, chunk_size=4)
    snapshot = make_snapshot(document_storage)
    snapshot.document_versions[0].content_hash = "0" * 64
    service, session, _, archive_storage = make_service(tmp_path, snapshot)

    with pytest.raises(ArchiveSourceIntegrityError):
        await service.export(snapshot.knowledge_base.id)

    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    assert list(archive_storage.root.glob("*.tracemind.zip")) == []
    assert list(archive_storage.staging_root.iterdir()) == []
