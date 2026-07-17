from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_base import KnowledgeBase
from app.repositories.knowledge_base import KnowledgeBaseRepository
from app.schemas.knowledge_base import KnowledgeBaseCreate, KnowledgeBaseUpdate
from app.services.exceptions import (
    KnowledgeBaseNameConflictError,
    KnowledgeBaseNotFoundError,
)
from app.services.knowledge_base import KnowledgeBaseService


def make_knowledge_base(name: str = "Backend Notes") -> KnowledgeBase:
    now = datetime.now(UTC)
    return KnowledgeBase(
        id=uuid4(),
        name=name,
        description="Notes",
        created_at=now,
        updated_at=now,
    )


def make_service() -> tuple[KnowledgeBaseService, AsyncMock, AsyncMock]:
    session = AsyncMock(spec=AsyncSession)
    repository = AsyncMock(spec=KnowledgeBaseRepository)
    service = KnowledgeBaseService(
        cast(AsyncSession, session),
        cast(KnowledgeBaseRepository, repository),
    )
    return service, session, repository


async def test_create_knowledge_base() -> None:
    service, session, repository = make_service()
    repository.get_by_name.return_value = None
    repository.create.side_effect = lambda knowledge_base: knowledge_base

    result = await service.create(KnowledgeBaseCreate(name="  Backend Notes  ", description="x"))

    assert result.name == "Backend Notes"
    repository.create.assert_awaited_once()
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(result)


async def test_create_rejects_existing_name() -> None:
    service, session, repository = make_service()
    repository.get_by_name.return_value = make_knowledge_base()

    with pytest.raises(KnowledgeBaseNameConflictError):
        await service.create(KnowledgeBaseCreate(name="Backend Notes"))

    session.commit.assert_not_awaited()


async def test_create_converts_unique_constraint_error() -> None:
    service, session, repository = make_service()
    repository.get_by_name.return_value = None
    repository.create.side_effect = IntegrityError("insert", {}, Exception("duplicate"))

    with pytest.raises(KnowledgeBaseNameConflictError):
        await service.create(KnowledgeBaseCreate(name="Backend Notes"))

    session.rollback.assert_awaited_once()


async def test_get_existing_and_missing_knowledge_base() -> None:
    service, _, repository = make_service()
    existing = make_knowledge_base()
    repository.get_by_id.return_value = existing

    assert await service.get(existing.id) is existing

    repository.get_by_id.return_value = None
    with pytest.raises(KnowledgeBaseNotFoundError):
        await service.get(uuid4())


async def test_list_returns_items_and_total() -> None:
    service, _, repository = make_service()
    items = [make_knowledge_base("A"), make_knowledge_base("B")]
    repository.list.return_value = items
    repository.count.return_value = 2

    result_items, total = await service.list(offset=10, limit=20)

    assert result_items == items
    assert total == 2
    repository.list.assert_awaited_once_with(offset=10, limit=20)


async def test_update_knowledge_base_and_clear_description() -> None:
    service, session, repository = make_service()
    existing = make_knowledge_base()
    repository.get_by_id.return_value = existing
    repository.get_by_name.return_value = None

    result = await service.update(
        existing.id,
        KnowledgeBaseUpdate(name="Updated Notes", description=None),
    )

    repository.update.assert_awaited_once_with(
        existing,
        {"name": "Updated Notes", "description": None},
    )
    session.commit.assert_awaited_once()
    assert result is existing


async def test_update_rejects_existing_name() -> None:
    service, session, repository = make_service()
    existing = make_knowledge_base("Original")
    repository.get_by_id.return_value = existing
    repository.get_by_name.return_value = make_knowledge_base("Existing")

    with pytest.raises(KnowledgeBaseNameConflictError):
        await service.update(existing.id, KnowledgeBaseUpdate(name="Existing"))

    session.commit.assert_not_awaited()


async def test_delete_existing_knowledge_base() -> None:
    service, session, repository = make_service()
    existing = make_knowledge_base()
    repository.get_by_id.return_value = existing

    await service.delete(existing.id)

    repository.delete.assert_awaited_once_with(existing)
    session.commit.assert_awaited_once()


async def test_delete_missing_knowledge_base() -> None:
    service, session, repository = make_service()
    repository.get_by_id.return_value = None

    with pytest.raises(KnowledgeBaseNotFoundError):
        await service.delete(uuid4())

    session.commit.assert_not_awaited()


async def test_database_error_rolls_back() -> None:
    service, session, repository = make_service()
    repository.get_by_name.return_value = None
    repository.create.side_effect = OperationalError("insert", {}, Exception("connection"))

    with pytest.raises(OperationalError):
        await service.create(KnowledgeBaseCreate(name="Backend Notes"))

    session.rollback.assert_awaited_once()
