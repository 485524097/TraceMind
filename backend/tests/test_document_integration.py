import asyncio
import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic.config import Config
from sqlalchemy import event, inspect
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from alembic import command
from app.core.config import get_settings
from app.models.document import Document, DocumentVersion
from app.models.knowledge_base import KnowledgeBase
from app.repositories.document import DocumentRepository

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
    command.downgrade(config, "20260717_0001")

    async def table_names() -> set[str]:
        engine = create_async_engine(url)
        async with engine.connect() as connection:
            result = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
        await engine.dispose()
        return result

    downgraded_tables = asyncio.run(table_names())
    assert "knowledge_bases" in downgraded_tables
    assert "documents" not in downgraded_tables
    assert "document_versions" not in downgraded_tables
    command.upgrade(config, "head")

    async def inspect_schema() -> tuple[set[str], set[str], set[str], set[str], bool, set[str]]:
        engine = create_async_engine(url)
        async with engine.connect() as connection:

            def read(
                sync_connection: object,
            ) -> tuple[set[str], set[str], set[str], set[str], bool, set[str]]:
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
        timezone_columns,
        version_column_types,
    ) = asyncio.run(inspect_schema())
    assert {"documents", "document_versions"}.issubset(tables)
    assert "uq_documents_knowledge_base_normalized_name" in document_uniques
    assert "uq_document_versions_document_version" in version_uniques
    assert {
        "ck_document_versions_version_positive",
        "ck_document_versions_file_size_positive",
        "ck_document_versions_hash_length",
    }.issubset(checks)
    assert timezone_columns
    assert any(item.startswith("content_hash:CHAR(64)") for item in version_column_types)
    assert any(item.startswith("file_size:BIGINT") for item in version_column_types)
    get_settings.cache_clear()
