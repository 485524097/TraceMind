from contextlib import AbstractAsyncContextManager
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.tasks.documents import _parse_document_version


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


async def test_task_uses_fresh_database_session_and_disposes_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parse = AsyncMock(return_value=True)

    class FakeService:
        def __init__(self, session: AsyncSession, *_args: object, **_kwargs: object) -> None:
            assert session is FakeDatabase.instances[-1].session

        parse_version = parse

    FakeDatabase.instances.clear()
    monkeypatch.setattr("app.tasks.documents.Database", FakeDatabase)
    monkeypatch.setattr("app.tasks.documents.DocumentParsingService", FakeService)

    version_id = uuid4()
    assert await _parse_document_version(version_id, force=True)

    assert len(FakeDatabase.instances) == 1
    assert FakeDatabase.instances[0].closed
    parse.assert_awaited_once_with(version_id, force=True)


async def test_task_disposes_engine_when_service_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeService:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def parse_version(self, *_args: object, **_kwargs: object) -> bool:
            raise RuntimeError("failure")

    FakeDatabase.instances.clear()
    monkeypatch.setattr("app.tasks.documents.Database", FakeDatabase)
    monkeypatch.setattr("app.tasks.documents.DocumentParsingService", FakeService)

    with pytest.raises(RuntimeError):
        await _parse_document_version(uuid4(), force=False)
    assert FakeDatabase.instances[0].closed
