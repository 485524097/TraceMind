import asyncio
import hashlib
import json
import os
import shutil
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4
from zipfile import ZipFile

import pytest
import pytest_asyncio
from alembic.config import Config
from fastapi import UploadFile
from sqlalchemy import delete, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from alembic import command
from app.core.config import get_settings
from app.models.conversation import Conversation, ConversationMessage
from app.models.document import Document, DocumentChunk, DocumentVersion
from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_entry import KnowledgeEntry
from app.repositories.knowledge_base_restore_lock import (
    RestoreAdvisoryLock,
    restore_advisory_lock_key,
)
from app.services.exceptions import ArchiveConflictError, ArchiveStorageError
from app.services.knowledge_base_archive import KnowledgeBaseArchiveService
from app.services.knowledge_base_restore import (
    KnowledgeBaseArchiveValidator,
    KnowledgeBaseRestoreRecoveryService,
    KnowledgeBaseRestoreService,
)
from app.storage.archive import ArchiveLimits, LocalArchiveStorage
from app.storage.local import LocalFileStorage
from tests.archive_restore_fixtures import build_restore_archive

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not configured"),
]


def require_test_database_url() -> str:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not configured")
    database_name = make_url(TEST_DATABASE_URL).database or ""
    if not database_name.endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must point to a database ending in '_test'")
    return TEST_DATABASE_URL


def run_migration() -> None:
    os.environ["DATABASE_URL"] = require_test_database_url()
    get_settings.cache_clear()
    command.upgrade(Config("alembic.ini"), "head")
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url = require_test_database_url()
    await asyncio.to_thread(run_migration)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


def limits() -> ArchiveLimits:
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


async def test_real_postgresql_snapshot_exports_committed_entities(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    now = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)
    knowledge_base_id, document_id, version_id = uuid4(), uuid4(), uuid4()
    storage = LocalFileStorage(tmp_path / "uploads", max_size=1_000, chunk_size=4)
    relative_path = storage.final_relative_path(knowledge_base_id, document_id, version_id, ".md")
    source = storage.resolve_relative(relative_path, must_exist=False)
    source.parent.mkdir(parents=True)
    content = b"postgres archive integration"
    source.write_bytes(content)

    knowledge_base = KnowledgeBase(
        id=knowledge_base_id,
        name=f"Archive Integration {knowledge_base_id}",
        description="PostgreSQL snapshot",
        created_at=now,
        updated_at=now,
    )
    document = Document(
        id=document_id,
        knowledge_base_id=knowledge_base_id,
        name="integration.md",
        normalized_name="integration.md",
        relative_path="integration.md",
        normalized_path="integration.md",
        source_type="upload",
        created_at=now,
        updated_at=now,
    )
    version = DocumentVersion(
        id=version_id,
        document_id=document_id,
        version_number=1,
        content_hash=hashlib.sha256(content).hexdigest(),
        file_size=len(content),
        mime_type="text/markdown",
        extension=".md",
        storage_path=relative_path,
        created_at=now,
    )
    async with session_factory() as setup_session:
        setup_session.add_all([knowledge_base, document, version])
        await setup_session.commit()

    archive_storage = LocalArchiveStorage(storage.root, limits())
    exported_path: Path | None = None
    try:
        async with session_factory() as export_session:
            service = KnowledgeBaseArchiveService(
                export_session,
                storage,
                archive_storage,
                "integration",
            )
            exported = await service.export(knowledge_base_id)
            exported_path = exported.path
        with ZipFile(exported.path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["knowledge_base"]["id"] == str(knowledge_base_id)
            assert manifest["entity_counts"]["documents"] == 1
            assert manifest["entity_counts"]["document_versions"] == 1
            assert archive.read(f"files/document_versions/{version_id}/content.md") == content
    finally:
        if exported_path is not None:
            await archive_storage.discard_archive(exported_path)
        async with session_factory() as cleanup_session:
            await cleanup_session.execute(
                delete(KnowledgeEntry).where(KnowledgeEntry.knowledge_base_id == knowledge_base_id)
            )
            await cleanup_session.execute(
                delete(Conversation).where(Conversation.knowledge_base_id == knowledge_base_id)
            )
            await cleanup_session.execute(
                delete(Document).where(Document.knowledge_base_id == knowledge_base_id)
            )
            await cleanup_session.execute(
                delete(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id)
            )
            await cleanup_session.commit()


async def test_export_clean_database_and_storage_then_restore_preserves_source_truth(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    now = datetime(2026, 8, 14, 10, 30, tzinfo=UTC)
    knowledge_base_id, document_id, version_id, chunk_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    conversation_id, user_message_id, assistant_message_id = uuid4(), uuid4(), uuid4()
    verified_id, unverified_id, outdated_id = uuid4(), uuid4(), uuid4()
    content = b"full source-of-truth restore integration"
    content_hash = hashlib.sha256(content).hexdigest()
    storage = LocalFileStorage(tmp_path / "restore-uploads", max_size=10_000, chunk_size=4)
    storage_path = storage.final_relative_path(knowledge_base_id, document_id, version_id, ".md")
    source_file = storage.resolve_relative(storage_path, must_exist=False)
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(content)
    knowledge_base = KnowledgeBase(
        id=knowledge_base_id,
        name=f"Round Trip {knowledge_base_id}",
        description="restore integration",
        created_at=now,
        updated_at=now,
    )
    document = Document(
        id=document_id,
        knowledge_base_id=knowledge_base_id,
        name="Guide.MD",
        normalized_name="guide.md",
        relative_path="Docs/Guide.MD",
        normalized_path="docs/guide.md",
        source_type="upload",
        created_at=now,
        updated_at=now,
    )
    version = DocumentVersion(
        id=version_id,
        document_id=document_id,
        version_number=1,
        content_hash=content_hash,
        file_size=len(content),
        mime_type="text/markdown",
        extension=".md",
        storage_path=storage_path,
        parse_status="succeeded",
        parser_name="markdown",
        parser_version="old",
        chunk_count=1,
        parsed_at=now,
        index_status="succeeded",
        active_index_generation=uuid4(),
        indexed_at=now,
        indexed_chunk_count=1,
        embedding_model="old-model",
        embedding_dimension=1024,
        created_at=now,
    )
    chunk = DocumentChunk(
        id=chunk_id,
        document_version_id=version_id,
        chunk_index=0,
        content="derived chunk",
        content_hash=hashlib.sha256(b"derived chunk").hexdigest(),
        char_count=len("derived chunk"),
        page_number=None,
        start_line=1,
        end_line=1,
        section_title="Derived",
        chunk_type="text",
        language="markdown",
        created_at=now,
    )
    conversation = Conversation(
        id=conversation_id,
        knowledge_base_id=knowledge_base_id,
        title="Restore conversation",
        created_at=now,
        updated_at=now,
    )
    user_message = ConversationMessage(
        id=user_message_id,
        conversation_id=conversation_id,
        role="user",
        status="completed",
        content="Why?",
        trace_id=uuid4(),
        sources=None,
        generation_metadata=None,
        created_at=now,
    )
    evidence = [{"document_id": str(document_id), "quote": "real evidence"}]
    generation = {"provider": "integration", "latency_ms": 11}
    assistant_message = ConversationMessage(
        id=assistant_message_id,
        conversation_id=conversation_id,
        role="assistant",
        status="completed",
        content="Because.",
        trace_id=uuid4(),
        sources=evidence,
        generation_metadata=generation,
        created_at=now,
    )
    verified = KnowledgeEntry(
        id=verified_id,
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
        sources_snapshot=evidence,
        generation_metadata_snapshot=generation,
        index_status="succeeded",
        active_index_generation=uuid4(),
        indexed_at=now,
        indexed_source_updated_at=now,
        indexed_chunk_count=1,
        embedding_model="old-model",
        embedding_dimension=1024,
        created_at=now,
        updated_at=now,
    )
    other_entries = [
        KnowledgeEntry(
            id=entry_id,
            knowledge_base_id=knowledge_base_id,
            question=status,
            background=None,
            root_cause=None,
            solution="Keep source truth",
            failed_attempts=[],
            validation_status=status,
            tags=[],
            source_conversation_id=None,
            source_user_message_id=None,
            source_assistant_message_id=None,
            question_snapshot=status,
            answer_snapshot="snapshot",
            sources_snapshot=[],
            generation_metadata_snapshot=None,
            index_status="failed",
            index_error_code="old_error",
            index_error_message="old error",
            created_at=now,
            updated_at=now,
        )
        for entry_id, status in [
            (unverified_id, "unverified"),
            (outdated_id, "outdated"),
        ]
    ]
    async with session_factory() as setup_session:
        setup_session.add(knowledge_base)
        await setup_session.flush()
        setup_session.add(document)
        await setup_session.flush()
        setup_session.add_all([version, conversation])
        await setup_session.flush()
        setup_session.add_all([chunk, user_message, assistant_message])
        await setup_session.flush()
        setup_session.add_all([verified, *other_entries])
        await setup_session.commit()

    archive_storage = LocalArchiveStorage(storage.root, limits())
    exported_path: Path | None = None
    try:
        async with session_factory() as export_session:
            exported = await KnowledgeBaseArchiveService(
                export_session, storage, archive_storage, "integration"
            ).export(knowledge_base_id)
            exported_path = exported.path
        async with session_factory() as clean_session:
            await clean_session.execute(
                delete(KnowledgeEntry).where(KnowledgeEntry.knowledge_base_id == knowledge_base_id)
            )
            await clean_session.execute(
                delete(Conversation).where(Conversation.knowledge_base_id == knowledge_base_id)
            )
            await clean_session.execute(
                delete(Document).where(Document.knowledge_base_id == knowledge_base_id)
            )
            await clean_session.execute(
                delete(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id)
            )
            await clean_session.commit()
        shutil.rmtree(storage.root / str(knowledge_base_id))

        async with session_factory() as restore_session:
            restore_service = KnowledgeBaseRestoreService(
                restore_session,
                storage,
                archive_storage,
                {".md", ".txt"},
            )
            result = await restore_service.restore(
                UploadFile(
                    filename="round-trip.tracemind.zip",
                    file=BytesIO(exported.path.read_bytes()),
                )
            )
        assert result.knowledge_base_id == knowledge_base_id
        assert result.restore_status == "succeeded"
        assert result.rebuild_status == "not_started"

        async with session_factory() as verify_session:
            restored_kb = await verify_session.get(KnowledgeBase, knowledge_base_id)
            restored_document = await verify_session.get(Document, document_id)
            restored_version = await verify_session.get(DocumentVersion, version_id)
            restored_conversation = await verify_session.get(Conversation, conversation_id)
            restored_user = await verify_session.get(ConversationMessage, user_message_id)
            restored_assistant = await verify_session.get(ConversationMessage, assistant_message_id)
            entries = {
                item.id: item
                for item in (
                    await verify_session.execute(
                        select(KnowledgeEntry).where(
                            KnowledgeEntry.knowledge_base_id == knowledge_base_id
                        )
                    )
                )
                .scalars()
                .all()
            }
            chunk_count = int(
                (
                    await verify_session.execute(
                        select(func.count())
                        .select_from(DocumentChunk)
                        .where(DocumentChunk.document_version_id == version_id)
                    )
                ).scalar_one()
            )
        assert restored_kb is not None and restored_kb.created_at == now
        assert restored_document is not None and restored_document.id == document_id
        assert restored_document.normalized_name == "guide.md"
        assert restored_document.normalized_path == "docs/guide.md"
        assert restored_document.created_at == now and restored_document.updated_at == now
        assert restored_version is not None and restored_version.id == version_id
        assert restored_version.created_at == now
        assert restored_version.storage_path == storage_path
        assert restored_version.parse_status == "pending"
        assert restored_version.parser_name is None and restored_version.chunk_count == 0
        assert restored_version.index_status == "pending"
        assert restored_version.active_index_generation is None
        assert restored_version.embedding_model is None
        assert chunk_count == 0
        assert restored_conversation is not None and restored_conversation.id == conversation_id
        assert restored_conversation.created_at == now and restored_conversation.updated_at == now
        assert restored_user is not None and restored_user.id == user_message_id
        assert restored_assistant is not None and restored_assistant.id == assistant_message_id
        assert restored_assistant.sources == evidence
        assert restored_assistant.generation_metadata == generation
        assert set(entries) == {verified_id, unverified_id, outdated_id}
        assert entries[verified_id].source_assistant_message_id == assistant_message_id
        assert entries[verified_id].sources_snapshot == evidence
        assert entries[verified_id].generation_metadata_snapshot == generation
        assert entries[verified_id].index_status == "pending"
        assert entries[verified_id].active_index_generation is None
        assert entries[unverified_id].index_status == "not_indexed"
        assert entries[outdated_id].index_status == "not_indexed"
        assert all(item.created_at == now and item.updated_at == now for item in entries.values())
        restored_file = storage.resolve_relative(restored_version.storage_path)
        assert restored_file.read_bytes() == content
        assert hashlib.sha256(restored_file.read_bytes()).hexdigest() == content_hash

        async with session_factory() as duplicate_session:
            duplicate_service = KnowledgeBaseRestoreService(
                duplicate_session,
                storage,
                archive_storage,
                {".md", ".txt"},
            )
            with pytest.raises(ArchiveConflictError):
                await duplicate_service.restore(
                    UploadFile(
                        filename="round-trip.tracemind.zip",
                        file=BytesIO(exported.path.read_bytes()),
                    )
                )
    finally:
        if exported_path is not None:
            await archive_storage.discard_archive(exported_path)
        async with session_factory() as cleanup_session:
            await cleanup_session.execute(
                delete(KnowledgeEntry).where(KnowledgeEntry.knowledge_base_id == knowledge_base_id)
            )
            await cleanup_session.execute(
                delete(Conversation).where(Conversation.knowledge_base_id == knowledge_base_id)
            )
            await cleanup_session.execute(
                delete(Document).where(Document.knowledge_base_id == knowledge_base_id)
            )
            await cleanup_session.execute(
                delete(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id)
            )
            await cleanup_session.commit()
        shutil.rmtree(storage.root / str(knowledge_base_id), ignore_errors=True)


async def test_real_postgresql_rolls_back_when_filesystem_promotion_fails(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = build_restore_archive(tmp_path)
    storage = LocalFileStorage(tmp_path / "failure-uploads", max_size=10_000, chunk_size=4)
    archive_storage = LocalArchiveStorage(storage.root, limits())
    monkeypatch.setattr(
        archive_storage,
        "promote_restore",
        AsyncMock(side_effect=ArchiveStorageError("promotion failed")),
    )

    async with session_factory() as restore_session:
        service = KnowledgeBaseRestoreService(
            restore_session,
            storage,
            archive_storage,
            {".md", ".txt"},
        )
        with pytest.raises(ArchiveStorageError):
            await service.restore(
                UploadFile(
                    filename="failure.tracemind.zip",
                    file=BytesIO(fixture.path.read_bytes()),
                )
            )

    async with session_factory() as verify_session:
        assert await verify_session.get(KnowledgeBase, fixture.knowledge_base_id) is None
    assert not (storage.root / str(fixture.knowledge_base_id)).exists()
    assert list(archive_storage.upload_root.iterdir()) == []
    assert list(archive_storage.journal_root.iterdir()) == []
    assert [
        item for item in archive_storage.restore_root.iterdir() if item.name != "journals"
    ] == []


async def test_restore_recovery_cannot_delete_promoted_source_before_commit(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = build_restore_archive(tmp_path)
    storage = LocalFileStorage(tmp_path / "race-uploads", max_size=10_000, chunk_size=4)
    archive_storage = LocalArchiveStorage(storage.root, limits())
    engine = cast(AsyncEngine, session_factory.kw["bind"])
    promoted = asyncio.Event()
    allow_commit = asyncio.Event()
    original_mark_promoted = archive_storage.mark_restore_promoted

    async def pause_after_promotion(path: Path, journal: object) -> None:
        await original_mark_promoted(path, journal)  # type: ignore[arg-type]
        promoted.set()
        await allow_commit.wait()

    monkeypatch.setattr(archive_storage, "mark_restore_promoted", pause_after_promotion)
    async with session_factory() as restore_session:
        service = KnowledgeBaseRestoreService(
            restore_session,
            storage,
            archive_storage,
            {".md", ".txt"},
            restore_lock=RestoreAdvisoryLock(engine),
        )
        restore_task = asyncio.create_task(
            service.restore(
                UploadFile(
                    filename="race.tracemind.zip",
                    file=BytesIO(fixture.path.read_bytes()),
                )
            )
        )
        await asyncio.wait_for(promoted.wait(), timeout=10)
        try:
            async with session_factory() as recovery_session:
                await KnowledgeBaseRestoreRecoveryService(
                    recovery_session,
                    archive_storage,
                    restore_lock=RestoreAdvisoryLock(engine),
                ).recover()
            final_path = storage.root / str(fixture.knowledge_base_id)
            assert final_path.is_dir()
            assert list(archive_storage.journal_root.glob("*.json"))
        finally:
            allow_commit.set()
        result = await restore_task

    assert result.knowledge_base_id == fixture.knowledge_base_id
    async with session_factory() as verify_session:
        assert await verify_session.get(KnowledgeBase, fixture.knowledge_base_id) is not None
        version = await verify_session.get(DocumentVersion, fixture.version_id)
        assert version is not None
        assert storage.resolve_relative(version.storage_path).read_bytes() == fixture.content
        await verify_session.execute(
            delete(KnowledgeEntry).where(
                KnowledgeEntry.knowledge_base_id == fixture.knowledge_base_id
            )
        )
        await verify_session.execute(
            delete(Conversation).where(Conversation.knowledge_base_id == fixture.knowledge_base_id)
        )
        await verify_session.execute(
            delete(Document).where(Document.knowledge_base_id == fixture.knowledge_base_id)
        )
        await verify_session.execute(
            delete(KnowledgeBase).where(KnowledgeBase.id == fixture.knowledge_base_id)
        )
        await verify_session.commit()
    shutil.rmtree(storage.root / str(fixture.knowledge_base_id), ignore_errors=True)


async def test_restore_recovery_takes_over_after_lock_connection_dies(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    fixture = build_restore_archive(tmp_path)
    storage = LocalFileStorage(tmp_path / "crash-uploads", max_size=10_000, chunk_size=4)
    archive_storage = LocalArchiveStorage(storage.root, limits())
    validator = KnowledgeBaseArchiveValidator(archive_storage, storage, {".md", ".txt"})
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
    await archive_storage.promote_restore(staged)
    await archive_storage.mark_restore_promoted(journal_path, journal)

    engine = cast(AsyncEngine, session_factory.kw["bind"])
    crashed_connection = await engine.connect()
    await crashed_connection.execute(
        text("SELECT pg_advisory_lock(:key)"),
        {"key": restore_advisory_lock_key(fixture.knowledge_base_id)},
    )
    await crashed_connection.invalidate()
    await crashed_connection.close()

    async with session_factory() as recovery_session:
        await KnowledgeBaseRestoreRecoveryService(
            recovery_session,
            archive_storage,
            restore_lock=RestoreAdvisoryLock(engine),
        ).recover()

    assert not staged.final_path.exists()
    assert not journal_path.exists()
