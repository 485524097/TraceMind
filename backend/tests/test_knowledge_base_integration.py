import asyncio
import os
from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.core.config import get_settings
from app.models.knowledge_base import KnowledgeBase
from app.repositories.knowledge_base import KnowledgeBaseRepository

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


def run_migration(revision: str) -> None:
    os.environ["DATABASE_URL"] = require_test_database_url()
    get_settings.cache_clear()
    command.upgrade(Config("alembic.ini"), revision)
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    url = require_test_database_url()
    await asyncio.to_thread(run_migration, "head")
    engine = create_async_engine(url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db_session:
        yield db_session
        await db_session.rollback()
    await engine.dispose()


async def test_repository_crud_and_timezones(session: AsyncSession) -> None:
    repository = KnowledgeBaseRepository(session)
    first = KnowledgeBase(name=f"Integration {uuid4()}", description="one")
    second = KnowledgeBase(name=f"Integration {uuid4()}", description="two")

    try:
        await repository.create(first)
        await repository.create(second)
        await session.commit()
        await session.refresh(first)
        await session.refresh(second)

        assert await repository.get_by_id(first.id) is first
        assert await repository.get_by_name(second.name) is second
        assert await repository.count() >= 2
        items = await repository.list(offset=0, limit=100)
        assert first in items and second in items
        assert first.created_at.utcoffset() is not None
        assert first.updated_at.utcoffset() is not None

        previous_updated_at = first.updated_at
        await repository.update(first, {"name": f"Updated {uuid4()}", "description": None})
        await session.commit()
        await session.refresh(first)
        assert first.description is None
        assert first.updated_at >= previous_updated_at - timedelta(microseconds=1)

        await repository.delete(second)
        await session.commit()
        assert await repository.get_by_id(second.id) is None
    finally:
        remaining = await repository.get_by_id(first.id)
        if remaining is not None:
            await repository.delete(remaining)
        await session.commit()


async def test_database_unique_constraint(session: AsyncSession) -> None:
    name = f"Unique {uuid4()}"
    repository = KnowledgeBaseRepository(session)
    first = KnowledgeBase(name=name)
    second = KnowledgeBase(name=name)

    try:
        await repository.create(first)
        await session.commit()
        with pytest.raises(IntegrityError):
            await repository.create(second)
            await session.commit()
        await session.rollback()
    finally:
        existing = await repository.get_by_name(name)
        if existing is not None:
            await repository.delete(existing)
            await session.commit()


def test_migration_upgrade_downgrade_upgrade() -> None:
    url = require_test_database_url()
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    config = Config("alembic.ini")

    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")

    async def inspect_table() -> tuple[set[str], bool, set[str]]:
        engine = create_async_engine(url)
        async with engine.connect() as connection:

            def read_schema(sync_connection: object) -> tuple[set[str], bool, set[str]]:
                inspector = inspect(sync_connection)
                columns = inspector.get_columns("knowledge_bases")
                names = {column["name"] for column in columns}
                timestamps_have_timezone = all(
                    getattr(column["type"], "timezone", False)
                    for column in columns
                    if column["name"] in {"created_at", "updated_at"}
                )
                unique_names = {
                    constraint["name"]
                    for constraint in inspector.get_unique_constraints("knowledge_bases")
                }
                return names, timestamps_have_timezone, unique_names

            result = await connection.run_sync(read_schema)
        await engine.dispose()
        return result

    columns, timestamps_have_timezone, unique_constraints = asyncio.run(inspect_table())
    assert columns == {"id", "name", "description", "created_at", "updated_at"}
    assert timestamps_have_timezone
    assert "uq_knowledge_bases_name" in unique_constraints
    get_settings.cache_clear()
