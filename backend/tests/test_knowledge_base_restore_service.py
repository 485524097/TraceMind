from io import BytesIO
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock
from zipfile import ZipFile

import pytest
from fastapi import UploadFile
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.knowledge_base_archive import (
    KnowledgeBaseArchiveRepository,
    KnowledgeBaseRestoreEntities,
    RestoreConflictCheck,
)
from app.services.exceptions import (
    ArchiveConflictError,
    ArchiveStorageError,
    ArchiveValidationError,
)
from app.services.knowledge_base_restore import KnowledgeBaseRestoreService
from app.storage.archive import ArchiveLimits, LocalArchiveStorage
from app.storage.local import LocalFileStorage
from tests.archive_restore_fixtures import build_restore_archive, rewrite_archive


class FakeTransaction:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> bool:
        if exc_type is None and self.failure is not None:
            raise self.failure
        return False


class FakeSession:
    def __init__(self, commit_failure: Exception | None = None) -> None:
        self.commit_failure = commit_failure
        self.rollback_count = 0
        self.begin_count = 0

    async def rollback(self) -> None:
        self.rollback_count += 1

    def begin(self) -> FakeTransaction:
        self.begin_count += 1
        return FakeTransaction(self.commit_failure)


class FakeRestoreRepository:
    def __init__(
        self,
        conflicts: list[str] | None = None,
        add_failure: Exception | None = None,
    ) -> None:
        self.conflicts = conflicts or []
        self.add_failure = add_failure
        self.checks: list[RestoreConflictCheck] = []
        self.entities: KnowledgeBaseRestoreEntities | None = None

    async def find_restore_conflicts(self, check: RestoreConflictCheck) -> list[str]:
        self.checks.append(check)
        return list(self.conflicts)

    async def add_restore_entities(self, entities: KnowledgeBaseRestoreEntities) -> None:
        if self.add_failure is not None:
            raise self.add_failure
        self.entities = entities

    async def knowledge_base_exists(self, knowledge_base_id: object) -> bool:
        return False


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


def make_service(
    tmp_path: Path,
    *,
    conflicts: list[str] | None = None,
    add_failure: Exception | None = None,
    commit_failure: Exception | None = None,
) -> tuple[
    KnowledgeBaseRestoreService,
    FakeSession,
    FakeRestoreRepository,
    LocalArchiveStorage,
]:
    session = FakeSession(commit_failure)
    repository = FakeRestoreRepository(conflicts, add_failure)
    document_storage = LocalFileStorage(tmp_path / "uploads", max_size=1_000, chunk_size=4)
    archive_storage = LocalArchiveStorage(document_storage.root, archive_limits())
    service = KnowledgeBaseRestoreService(
        cast(AsyncSession, session),
        document_storage,
        archive_storage,
        {".md", ".txt"},
        cast(KnowledgeBaseArchiveRepository, repository),
    )
    return service, session, repository, archive_storage


def upload(path: Path) -> UploadFile:
    return UploadFile(filename="fixture.tracemind.zip", file=BytesIO(path.read_bytes()))


def assert_no_temporary_restore_state(storage: LocalArchiveStorage) -> None:
    assert list(storage.upload_root.iterdir()) == []
    assert list(storage.journal_root.iterdir()) == []
    operation_dirs = [item for item in storage.restore_root.iterdir() if item.name != "journals"]
    assert operation_dirs == []


async def test_restore_preserves_source_truth_and_resets_derived_state(tmp_path: Path) -> None:
    fixture = build_restore_archive(tmp_path)
    service, session, repository, archive_storage = make_service(tmp_path)

    result = await service.restore(upload(fixture.path))

    assert result.knowledge_base_id == fixture.knowledge_base_id
    assert result.restore_status == "succeeded"
    assert result.rebuild_status == "not_started"
    assert session.begin_count == 1
    assert len(repository.checks) == 1
    entities = repository.entities
    assert entities is not None
    assert entities.knowledge_base.id == fixture.knowledge_base_id
    assert entities.knowledge_base.created_at == fixture.created_at
    assert entities.documents[0].id == fixture.document_id
    assert entities.documents[0].normalized_name == "guide.md"
    assert entities.documents[0].normalized_path == "docs/guide.md"
    version = entities.document_versions[0]
    assert version.id == fixture.version_id
    assert version.parse_status == "pending"
    assert version.parser_name is None
    assert version.chunk_count == 0
    assert version.index_status == "pending"
    assert version.active_index_generation is None
    assert version.embedding_model is None
    assert version.storage_path == (
        f"{fixture.knowledge_base_id}/{fixture.document_id}/{fixture.version_id}/content.md"
    )
    assert entities.conversations[0].id == fixture.conversation_id
    assert entities.messages[1].id == fixture.assistant_message_id
    assert entities.messages[1].sources == [
        {"document_id": str(fixture.document_id), "quote": "evidence"}
    ]
    entry = entities.knowledge_entries[0]
    assert entry.id == fixture.knowledge_entry_id
    assert entry.source_assistant_message_id == fixture.assistant_message_id
    assert entry.sources_snapshot == entities.messages[1].sources
    assert entry.generation_metadata_snapshot == {"latency_ms": 8, "provider": "fixture"}
    assert entry.index_status == "pending"
    assert entry.active_index_generation is None
    final_file = archive_storage.document_storage_root / Path(*version.storage_path.split("/"))
    assert final_file.read_bytes() == fixture.content
    assert not (final_file.parents[2] / ".tracemind-restore.json").exists()
    assert_no_temporary_restore_state(archive_storage)


@pytest.mark.parametrize(
    "conflict",
    [
        "knowledge_base_id",
        "knowledge_base_name",
        "document_id",
        "document_version_id",
        "conversation_id",
        "message_id",
        "knowledge_entry_id",
        "normalized_document_path",
        "knowledge_source_assistant",
    ],
)
async def test_conflict_rejects_entire_archive_before_staging(
    tmp_path: Path, conflict: str
) -> None:
    fixture = build_restore_archive(tmp_path)
    service, session, repository, archive_storage = make_service(tmp_path, conflicts=[conflict])

    with pytest.raises(ArchiveConflictError):
        await service.restore(upload(fixture.path))

    assert len(repository.checks) == 1
    assert repository.entities is None
    assert session.begin_count == 0
    assert not (archive_storage.document_storage_root / str(fixture.knowledge_base_id)).exists()
    assert_no_temporary_restore_state(archive_storage)


async def test_invalid_checksum_does_not_query_database_or_stage(tmp_path: Path) -> None:
    fixture = build_restore_archive(tmp_path)
    with ZipFile(fixture.path) as archive:
        file_path = next(name for name in archive.namelist() if name.startswith("files/"))
    invalid = rewrite_archive(
        fixture.path,
        tmp_path / "invalid.tracemind.zip",
        {file_path: b"invalid"},
    )
    service, session, repository, archive_storage = make_service(tmp_path)

    with pytest.raises(ArchiveValidationError):
        await service.restore(upload(invalid))

    assert repository.checks == []
    assert session.begin_count == 0
    assert_no_temporary_restore_state(archive_storage)


@pytest.mark.parametrize("failure_point", ["flush", "commit", "promotion"])
async def test_failures_roll_back_files_journal_and_temporary_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    fixture = build_restore_archive(tmp_path)
    database_error = OperationalError(failure_point, {}, Exception("offline"))
    service, session, repository, archive_storage = make_service(
        tmp_path,
        add_failure=database_error if failure_point == "flush" else None,
        commit_failure=database_error if failure_point == "commit" else None,
    )
    if failure_point == "promotion":
        monkeypatch.setattr(
            archive_storage,
            "promote_restore",
            AsyncMock(side_effect=ArchiveStorageError("promotion failed")),
        )

    with pytest.raises((OperationalError, ArchiveStorageError)):
        await service.restore(upload(fixture.path))

    assert session.rollback_count >= 2
    assert not (archive_storage.document_storage_root / str(fixture.knowledge_base_id)).exists()
    assert_no_temporary_restore_state(archive_storage)


async def test_filesystem_staging_failure_leaves_no_database_or_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = build_restore_archive(tmp_path)
    service, session, repository, archive_storage = make_service(tmp_path)
    monkeypatch.setattr(
        archive_storage,
        "stage_restore_files",
        AsyncMock(side_effect=ArchiveStorageError("staging failed")),
    )

    with pytest.raises(ArchiveStorageError):
        await service.restore(upload(fixture.path))

    assert repository.entities is None
    assert session.begin_count == 0
    assert_no_temporary_restore_state(archive_storage)
