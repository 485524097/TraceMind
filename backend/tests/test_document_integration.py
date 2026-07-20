import asyncio
import os
import threading
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic.config import Config
from sqlalchemy import event, inspect, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from alembic import command
from app.core.config import Settings, get_settings
from app.models.document import Document, DocumentChunk, DocumentVersion
from app.models.knowledge_base import KnowledgeBase
from app.parsing import ParseContext, ParsedBlock, ParsedDocument, ParserRegistry
from app.parsing.chunker import ChunkDraft
from app.repositories.document import DocumentRepository
from app.repositories.document_parsing import DocumentParsingRepository
from app.services.document_parsing import DocumentParsingService
from app.storage.local import LocalFileStorage

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not configured"),
]


def require_test_database_url() -> str:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not configured")
    if not (make_url(TEST_DATABASE_URL).database or "").endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must point to a database ending in '_test'")
    return TEST_DATABASE_URL


def migrate(revision: str) -> None:
    os.environ["DATABASE_URL"] = require_test_database_url()
    get_settings.cache_clear()
    command.upgrade(Config("alembic.ini"), revision)
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def database() -> AsyncIterator[tuple[AsyncSession, AsyncEngine]]:
    await asyncio.to_thread(migrate, "head")
    engine = create_async_engine(require_test_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session, engine
        await session.rollback()
    await engine.dispose()


def make_document(knowledge_base_id: object, name: str) -> Document:
    return Document(
        id=uuid4(),
        knowledge_base_id=knowledge_base_id,
        name=name,
        normalized_name=name.casefold(),
        source_type="upload",
    )


def make_version(document_id: object, number: int, suffix: str = "a") -> DocumentVersion:
    return DocumentVersion(
        id=uuid4(),
        document_id=document_id,
        version_number=number,
        content_hash=suffix * 64,
        file_size=number,
        mime_type="text/markdown",
        extension=".md",
        storage_path=f"safe/{uuid4()}/content.md",
    )


def make_chunk(version_id: object, index: int = 0) -> DocumentChunk:
    content = f"chunk {index}"
    return DocumentChunk(
        id=uuid4(),
        document_version_id=version_id,
        chunk_index=index,
        content=content,
        content_hash="c" * 64,
        char_count=len(content),
        page_number=1,
        start_line=None,
        end_line=None,
        section_title=None,
        chunk_type="page_text",
        language=None,
    )


class BlockingParser:
    parser_name = "blocking"
    parser_version = "1"
    supported_extensions = frozenset({".md"})

    def __init__(self, started: threading.Event, release: threading.Event) -> None:
        self.started = started
        self.release = release

    def parse(self, _path: Path, _context: ParseContext) -> ParsedDocument:
        self.started.set()
        if not self.release.wait(timeout=30):
            raise RuntimeError("Parser release timed out")
        return ParsedDocument(
            [ParsedBlock("old worker content", "paragraph", start_line=1, end_line=1)],
            self.parser_name,
            self.parser_version,
        )


class BlockingRegistry:
    def __init__(self, parser: BlockingParser) -> None:
        self.parser = parser

    def get(self, _extension: str) -> BlockingParser:
        return self.parser


async def test_repository_constraints_cascade_restrict_and_query_shape(
    database: tuple[AsyncSession, AsyncEngine],
) -> None:
    session, engine = database
    first_kb = KnowledgeBase(name=f"Documents {uuid4()}")
    second_kb = KnowledgeBase(name=f"Documents {uuid4()}")
    session.add_all([first_kb, second_kb])
    await session.flush()
    first_kb_id = first_kb.id
    second_kb_id = second_kb.id
    repository = DocumentRepository(session)
    document = make_document(first_kb_id, "Sample.md")
    document_id = document.id
    await repository.create_document(document)
    version_one = make_version(document.id, 1, "a")
    version_two = make_version(document.id, 2, "b")
    await repository.create_version(version_one)
    await repository.create_version(version_two)
    await session.commit()
    version_one_id = version_one.id
    version_two_id = version_two.id

    latest = await repository.get_latest_version(first_kb_id, document_id)
    versions = await repository.list_versions(first_kb_id, document_id)
    assert latest is not None and latest.version_number == 2
    assert [version.version_number for version in versions] == [2, 1]
    assert document.created_at.utcoffset() is not None
    assert version_one.created_at.utcoffset() is not None

    select_count = 0

    def count_selects(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        nonlocal select_count
        if statement.lstrip().upper().startswith("SELECT"):
            select_count += 1

    event.listen(engine.sync_engine, "before_cursor_execute", count_selects)
    try:
        records = await repository.list_documents(first_kb_id, offset=0, limit=20, query="sample")
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_selects)
    assert len(records) == 1 and records[0].version_count == 2
    assert select_count == 1

    duplicate = make_document(first_kb_id, "SAMPLE.MD")
    with pytest.raises(IntegrityError):
        await repository.create_document(duplicate)
    await session.rollback()

    same_name_other_kb = make_document(second_kb_id, "SAMPLE.MD")
    same_name_other_kb_id = same_name_other_kb.id
    await repository.create_document(same_name_other_kb)
    await repository.create_version(make_version(same_name_other_kb.id, 1, "c"))
    await session.commit()

    duplicate_version = make_version(document_id, 2, "d")
    with pytest.raises(IntegrityError):
        await repository.create_version(duplicate_version)
    await session.rollback()

    first_kb_for_delete = await session.get(KnowledgeBase, first_kb_id)
    assert first_kb_for_delete is not None
    await session.delete(first_kb_for_delete)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()

    persisted_document = await repository.get_document_by_id(first_kb_id, document_id)
    assert persisted_document is not None
    await repository.delete_document(persisted_document)
    await session.commit()
    assert await session.get(DocumentVersion, version_one_id) is None
    assert await session.get(DocumentVersion, version_two_id) is None

    other_document = await repository.get_document_by_id(second_kb_id, same_name_other_kb_id)
    assert other_document is not None
    await repository.delete_document(other_document)
    await session.delete(await session.get(KnowledgeBase, first_kb_id))
    await session.delete(await session.get(KnowledgeBase, second_kb_id))
    await session.commit()


def test_document_migration_upgrade_downgrade_upgrade() -> None:
    url = require_test_database_url()
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    config = Config("alembic.ini")

    command.upgrade(config, "head")
    command.downgrade(config, "20260717_0002")

    async def table_names() -> set[str]:
        engine = create_async_engine(url)
        async with engine.connect() as connection:
            result = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
        await engine.dispose()
        return result

    downgraded_tables = asyncio.run(table_names())
    assert "knowledge_bases" in downgraded_tables
    assert "documents" in downgraded_tables
    assert "document_versions" in downgraded_tables
    assert "document_chunks" not in downgraded_tables
    command.upgrade(config, "head")

    async def inspect_schema() -> tuple[
        set[str], set[str], set[str], set[str], set[str], bool, set[str]
    ]:
        engine = create_async_engine(url)
        async with engine.connect() as connection:

            def read(
                sync_connection: object,
            ) -> tuple[set[str], set[str], set[str], set[str], set[str], bool, set[str]]:
                inspector = inspect(sync_connection)
                tables = set(inspector.get_table_names())
                document_uniques = {
                    item["name"] for item in inspector.get_unique_constraints("documents")
                }
                version_uniques = {
                    item["name"] for item in inspector.get_unique_constraints("document_versions")
                }
                checks = {
                    item["name"] for item in inspector.get_check_constraints("document_versions")
                }
                chunk_checks = {
                    item["name"] for item in inspector.get_check_constraints("document_chunks")
                }
                timezone_columns = all(
                    getattr(column["type"], "timezone", False)
                    for table in ("documents", "document_versions")
                    for column in inspector.get_columns(table)
                    if column["name"] in {"created_at", "updated_at"}
                )
                version_column_types = {
                    f"{column['name']}:{column['type']}"
                    for column in inspector.get_columns("document_versions")
                }
                return (
                    tables,
                    document_uniques,
                    version_uniques,
                    checks,
                    chunk_checks,
                    timezone_columns,
                    version_column_types,
                )

            result = await connection.run_sync(read)
        await engine.dispose()
        return result

    (
        tables,
        document_uniques,
        version_uniques,
        checks,
        chunk_checks,
        timezone_columns,
        version_column_types,
    ) = asyncio.run(inspect_schema())
    assert {"documents", "document_versions", "document_chunks"}.issubset(tables)
    assert "uq_documents_knowledge_base_normalized_name" in document_uniques
    assert "uq_document_versions_document_version" in version_uniques
    assert {
        "ck_document_versions_version_positive",
        "ck_document_versions_file_size_positive",
        "ck_document_versions_hash_length",
        "ck_document_versions_chunk_count_nonnegative",
        "ck_document_versions_parse_status",
    }.issubset(checks)
    assert {
        "ck_document_chunks_index_nonnegative",
        "ck_document_chunks_char_count_positive",
        "ck_document_chunks_hash_length",
        "ck_document_chunks_page_positive",
        "ck_document_chunks_line_pair",
        "ck_document_chunks_line_order",
    }.issubset(chunk_checks)
    assert timezone_columns
    assert any(item.startswith("content_hash:CHAR(64)") for item in version_column_types)
    assert any(item.startswith("file_size:BIGINT") for item in version_column_types)
    get_settings.cache_clear()


async def test_document_chunks_constraints_and_cascade(
    database: tuple[AsyncSession, AsyncEngine],
) -> None:
    session, _ = database
    knowledge_base = KnowledgeBase(name=f"Chunks {uuid4()}")
    session.add(knowledge_base)
    await session.flush()
    knowledge_base_id = knowledge_base.id
    document = make_document(knowledge_base.id, "chunks.md")
    session.add(document)
    await session.flush()
    document_id = document.id
    version = make_version(document.id, 1)
    session.add(version)
    await session.flush()
    version_id = version.id
    session.add_all([make_chunk(version_id, 0), make_chunk(version_id, 1)])
    await session.commit()

    chunks = (
        (
            await session.execute(
                select(DocumentChunk)
                .where(DocumentChunk.document_version_id == version_id)
                .order_by(DocumentChunk.chunk_index)
            )
        )
        .scalars()
        .all()
    )
    assert [chunk.chunk_index for chunk in chunks] == [0, 1]
    assert version.parse_status == "pending"
    assert version.chunk_count == 0

    session.add(make_chunk(version_id, 1))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()

    persisted = await session.get(Document, document_id)
    assert persisted is not None
    await session.delete(persisted)
    await session.commit()
    remaining = await session.execute(
        select(DocumentChunk).where(DocumentChunk.document_version_id == version_id)
    )
    assert remaining.scalars().all() == []
    await session.delete(await session.get(KnowledgeBase, knowledge_base_id))
    await session.commit()


async def test_real_parsing_and_failed_reparse_preserve_chunks(
    database: tuple[AsyncSession, AsyncEngine], tmp_path: Path
) -> None:
    session, _ = database
    storage = LocalFileStorage(tmp_path / "uploads", max_size=1024, chunk_size=64)
    knowledge_base = KnowledgeBase(name=f"Parsing {uuid4()}")
    session.add(knowledge_base)
    await session.flush()
    knowledge_base_id = knowledge_base.id
    document = make_document(knowledge_base_id, "sample.md")
    session.add(document)
    await session.flush()
    document_id = document.id
    version = make_version(document_id, 1)
    version.storage_path = storage.final_relative_path(
        knowledge_base_id, document_id, version.id, ".md"
    )
    stored = storage.resolve_relative(version.storage_path, must_exist=False)
    stored.parent.mkdir(parents=True)
    stored.write_text("# 标题\n第一行\n第二行", encoding="utf-8")
    session.add(version)
    await session.commit()
    version_id = version.id
    service = DocumentParsingService(
        session,
        storage,
        Settings(
            document_storage_root=storage.root,
            document_chunk_max_chars=12,
            document_chunk_overlap_chars=2,
        ),
    )

    assert await service.parse_version(version_id)
    await session.refresh(version)
    original_chunks = (
        (
            await session.execute(
                select(DocumentChunk)
                .where(DocumentChunk.document_version_id == version_id)
                .order_by(DocumentChunk.chunk_index)
            )
        )
        .scalars()
        .all()
    )
    original_contents = [chunk.content for chunk in original_chunks]
    assert version.parse_status == "succeeded"
    assert version.chunk_count == len(original_chunks) > 0

    stored.write_bytes(b"\xff")
    assert not await service.parse_version(version_id, force=True)
    await session.refresh(version)
    preserved = (
        (
            await session.execute(
                select(DocumentChunk)
                .where(DocumentChunk.document_version_id == version_id)
                .order_by(DocumentChunk.chunk_index)
            )
        )
        .scalars()
        .all()
    )
    assert [chunk.content for chunk in preserved] == original_contents
    assert version.parse_status == "succeeded"
    assert version.parse_error_code == "invalid_encoding"

    persisted = await session.get(Document, document_id)
    assert persisted is not None
    await session.delete(persisted)
    await session.delete(await session.get(KnowledgeBase, knowledge_base_id))
    await session.commit()


async def test_superseded_parse_attempt_cannot_overwrite_new_transaction(
    database: tuple[AsyncSession, AsyncEngine], tmp_path: Path
) -> None:
    session, engine = database
    storage = LocalFileStorage(tmp_path / "uploads", max_size=1024, chunk_size=64)
    knowledge_base = KnowledgeBase(name=f"Attempt ownership {uuid4()}")
    session.add(knowledge_base)
    await session.flush()
    knowledge_base_id = knowledge_base.id
    document = make_document(knowledge_base_id, "ownership.md")
    session.add(document)
    await session.flush()
    document_id = document.id
    version = make_version(document_id, 1)
    version.storage_path = storage.final_relative_path(
        knowledge_base_id, document_id, version.id, ".md"
    )
    stored = storage.resolve_relative(version.storage_path, must_exist=False)
    stored.parent.mkdir(parents=True)
    stored.write_text("content", encoding="utf-8")
    session.add(version)
    await session.commit()
    version_id = version.id

    started = threading.Event()
    release = threading.Event()
    service = DocumentParsingService(
        session,
        storage,
        Settings(document_storage_root=storage.root),
        registry=cast(ParserRegistry, BlockingRegistry(BlockingParser(started, release))),
    )
    old_worker = asyncio.create_task(service.parse_version(version_id))
    assert await asyncio.to_thread(started.wait, 5)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    newer_attempt = datetime.now(UTC) + timedelta(seconds=1)
    async with factory() as worker_b:
        repository = DocumentParsingRepository(worker_b)
        record = await repository.lock_version_for_parsing(version_id)
        assert record is not None
        await repository.mark_processing(record.version, newer_attempt)
        await worker_b.commit()

        record = await repository.lock_version_for_parsing(version_id)
        assert record is not None
        await repository.delete_chunks_by_version(version_id)
        await repository.create_chunks(
            version_id,
            [
                ChunkDraft(
                    chunk_index=0,
                    content="new worker content",
                    content_hash="b" * 64,
                    char_count=18,
                    page_number=None,
                    start_line=1,
                    end_line=1,
                    section_title=None,
                    chunk_type="paragraph",
                    language=None,
                )
            ],
        )
        await repository.mark_succeeded(
            record.version,
            parser_name="new-worker",
            parser_version="1",
            chunk_count=1,
            parsed_at=datetime.now(UTC),
        )
        await worker_b.commit()

    release.set()
    assert not await old_worker

    async with factory() as verification:
        persisted_version = await verification.get(DocumentVersion, version_id)
        chunks = (
            (
                await verification.execute(
                    select(DocumentChunk).where(DocumentChunk.document_version_id == version_id)
                )
            )
            .scalars()
            .all()
        )
        assert persisted_version is not None
        assert persisted_version.parse_status == "succeeded"
        assert persisted_version.parse_started_at == newer_attempt
        assert persisted_version.parser_name == "new-worker"
        assert persisted_version.chunk_count == 1
        assert [chunk.content for chunk in chunks] == ["new worker content"]

        persisted_document = await verification.get(Document, document_id)
        persisted_knowledge_base = await verification.get(KnowledgeBase, knowledge_base_id)
        assert persisted_document is not None
        assert persisted_knowledge_base is not None
        await verification.delete(persisted_document)
        await verification.commit()
        await verification.delete(persisted_knowledge_base)
        await verification.commit()
