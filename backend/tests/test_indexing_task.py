from contextlib import AbstractAsyncContextManager
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.tasks.indexing import _index_document_version


class SessionContext(AbstractAsyncContextManager[AsyncSession]):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def __aenter__(self) -> AsyncSession:
        return self.session

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeDatabase:
    instances: list["FakeDatabase"] = []

    def __init__(self, _settings: object) -> None:
        self.session = cast(AsyncSession, AsyncMock(spec=AsyncSession))
        self.closed = False
        self.instances.append(self)

    def session_factory(self) -> SessionContext:
        return SessionContext(self.session)

    async def close(self) -> None:
        self.closed = True


class FakeQdrant:
    instances: list["FakeQdrant"] = []

    def __init__(self, _settings: object) -> None:
        self.client = object()
        self.closed = False
        self.instances.append(self)

    async def close(self) -> None:
        self.closed = True


async def test_index_task_uses_fresh_resources_and_closes_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = AsyncMock(return_value=True)

    class FakeService:
        def __init__(self, session: AsyncSession, *_args: object) -> None:
            assert session is FakeDatabase.instances[-1].session

        index_version = index

    FakeDatabase.instances.clear()
    FakeQdrant.instances.clear()
    monkeypatch.setattr("app.tasks.indexing.Database", FakeDatabase)
    monkeypatch.setattr("app.tasks.indexing.QdrantClient", FakeQdrant)
    monkeypatch.setattr("app.tasks.indexing.DocumentIndexingService", FakeService)

    version_id = uuid4()
    assert await _index_document_version(version_id, force=True)

    assert FakeDatabase.instances[0].closed
    assert FakeQdrant.instances[0].closed
    index.assert_awaited_once_with(version_id, force=True)
