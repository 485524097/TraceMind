from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.knowledge_base_archive import KnowledgeBaseArchiveRepository
from app.repositories.knowledge_base_restore_lock import RestoreAdvisoryLock
from app.schemas.knowledge_base_archive import KnowledgeBaseRestoreJournal
from app.services.knowledge_base_restore import (
    KnowledgeBaseArchiveValidator,
    KnowledgeBaseRestoreRecoveryService,
)
from app.storage.archive import (
    RESTORE_MARKER_NAME,
    ArchiveLimits,
    LocalArchiveStorage,
    StagedKnowledgeBaseRestore,
)
from app.storage.local import LocalFileStorage
from tests.archive_restore_fixtures import build_restore_archive


class FakeRecoverySession:
    def __init__(self) -> None:
        self.rollback_count = 0

    async def rollback(self) -> None:
        self.rollback_count += 1


class FakeRecoveryRepository:
    def __init__(self, exists: bool) -> None:
        self.exists = exists

    async def knowledge_base_exists(self, knowledge_base_id: object) -> bool:
        return self.exists


class FakeRestoreLock:
    def __init__(self, acquired: bool) -> None:
        self.acquired = acquired

    @asynccontextmanager
    async def try_hold(self, _knowledge_base_id: object) -> AsyncIterator[bool]:
        yield self.acquired


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


async def stage_and_journal(
    tmp_path: Path,
) -> tuple[
    LocalArchiveStorage,
    StagedKnowledgeBaseRestore,
    Path,
    KnowledgeBaseRestoreJournal,
]:
    fixture = build_restore_archive(tmp_path)
    document_storage = LocalFileStorage(tmp_path / "uploads", max_size=1_000, chunk_size=4)
    archive_storage = LocalArchiveStorage(document_storage.root, archive_limits())
    validator = KnowledgeBaseArchiveValidator(archive_storage, document_storage, {".md", ".txt"})
    validated = await validator.validate(fixture.path)
    staged = await archive_storage.stage_restore_files(
        fixture.path,
        uuid4(),
        fixture.knowledge_base_id,
        list(validated.restore_files),
    )
    journal_path, journal = await archive_storage.create_restore_journal(
        staged, list(validated.restore_files)
    )
    return archive_storage, staged, journal_path, journal


async def recover(
    storage: LocalArchiveStorage,
    exists: bool,
    *,
    lock_acquired: bool = True,
) -> FakeRecoverySession:
    session = FakeRecoverySession()
    repository = FakeRecoveryRepository(exists)
    service = KnowledgeBaseRestoreRecoveryService(
        cast(AsyncSession, session),
        storage,
        cast(KnowledgeBaseArchiveRepository, repository),
        cast(RestoreAdvisoryLock, FakeRestoreLock(lock_acquired)),
    )
    await service.recover()
    return session


async def test_recovery_removes_staging_when_database_has_no_knowledge_base(
    tmp_path: Path,
) -> None:
    storage, staged, journal_path, _ = await stage_and_journal(tmp_path)

    session = await recover(storage, exists=False)

    assert session.rollback_count == 1
    assert not staged.operation_root.exists()
    assert not journal_path.exists()


async def test_recovery_removes_owned_final_when_database_commit_did_not_happen(
    tmp_path: Path,
) -> None:
    storage, staged, journal_path, _ = await stage_and_journal(tmp_path)
    await storage.promote_restore(staged)
    ordinary = storage.document_storage_root / "ordinary-document-directory"
    ordinary.mkdir()
    (ordinary / "keep.txt").write_text("keep", encoding="utf-8")

    await recover(storage, exists=False)

    assert not staged.final_path.exists()
    assert not journal_path.exists()
    assert (ordinary / "keep.txt").read_text(encoding="utf-8") == "keep"


async def test_recovery_defers_cleanup_while_active_restore_holds_lock(tmp_path: Path) -> None:
    storage, staged, journal_path, _ = await stage_and_journal(tmp_path)
    await storage.promote_restore(staged)

    session = await recover(storage, exists=False, lock_acquired=False)

    assert session.rollback_count == 0
    assert staged.final_path.is_dir()
    assert journal_path.is_file()


async def test_recovery_keeps_complete_final_when_database_commit_exists(
    tmp_path: Path,
) -> None:
    storage, staged, journal_path, _ = await stage_and_journal(tmp_path)
    await storage.promote_restore(staged)

    await recover(storage, exists=True)

    assert staged.final_path.is_dir()
    assert not (staged.final_path / RESTORE_MARKER_NAME).exists()
    assert not journal_path.exists()
    assert list(staged.final_path.glob("*/*/content.*"))


async def test_forged_journal_without_operation_marker_cannot_delete_ordinary_directory(
    tmp_path: Path,
) -> None:
    document_storage = LocalFileStorage(tmp_path / "uploads", max_size=1_000, chunk_size=4)
    storage = LocalArchiveStorage(document_storage.root, archive_limits())
    operation_id, knowledge_base_id = uuid4(), uuid4()
    ordinary = storage.document_storage_root / str(knowledge_base_id)
    ordinary.mkdir()
    protected = ordinary / "ordinary.txt"
    protected.write_text("protected", encoding="utf-8")
    journal = KnowledgeBaseRestoreJournal(
        operation_id=operation_id,
        knowledge_base_id=knowledge_base_id,
        staging_path=f".restore-tmp/{operation_id}/{knowledge_base_id}",
        final_path=str(knowledge_base_id),
        created_at=datetime(2026, 8, 14, 10, 0, tzinfo=UTC),
        files=[],
    )
    journal_path = storage.journal_root / f"{operation_id}.json"
    storage._write_journal(journal_path, journal)

    await recover(storage, exists=False)

    assert protected.read_text(encoding="utf-8") == "protected"
    assert not journal_path.exists()
