from contextlib import AbstractAsyncContextManager
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.knowledge_base_rebuild_dispatcher import (
    CeleryKnowledgeBaseRebuildDispatcher,
)
from app.tasks.rebuild import _rebuild_knowledge_base


async def test_dispatcher_passes_operation_and_database_run_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delay = Mock()
    monkeypatch.setattr("app.tasks.rebuild.rebuild_knowledge_base.delay", delay)
    operation_id, run_generation = uuid4(), uuid4()

    await CeleryKnowledgeBaseRebuildDispatcher().enqueue(operation_id, run_generation)

    delay.assert_called_once_with(str(operation_id), str(run_generation))


class SessionContext(AbstractAsyncContextManager[AsyncSession]):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def __aenter__(self) -> AsyncSession:
        return self.session

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeDatabase:
    instance: "FakeDatabase | None" = None

    def __init__(self, _settings: object) -> None:
        self.session = cast(AsyncSession, AsyncMock(spec=AsyncSession))
        self.closed = False
        FakeDatabase.instance = self

    def session_factory(self) -> SessionContext:
        return SessionContext(self.session)

    async def close(self) -> None:
        self.closed = True


class FakeQdrant:
    instance: "FakeQdrant | None" = None

    def __init__(self, _settings: object) -> None:
        self.client = object()
        self.closed = False
        FakeQdrant.instance = self

    async def close(self) -> None:
        self.closed = True


async def test_task_builds_formal_pipelines_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = AsyncMock(return_value=True)

    class FakePipeline:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    class FakeExecutor:
        def __init__(self, session: AsyncSession, *_args: object) -> None:
            assert FakeDatabase.instance is not None
            assert session is FakeDatabase.instance.session

        async def run(self, operation_id: object, run_generation: object) -> bool:
            return bool(await run(operation_id, run_generation))

    FakeDatabase.instance = None
    FakeQdrant.instance = None
    monkeypatch.setattr("app.tasks.rebuild.Database", FakeDatabase)
    monkeypatch.setattr("app.tasks.rebuild.QdrantClient", FakeQdrant)
    monkeypatch.setattr("app.tasks.rebuild.LocalFileStorage", FakePipeline)
    monkeypatch.setattr("app.tasks.rebuild.SentenceTransformerEmbeddingProvider", FakePipeline)
    monkeypatch.setattr("app.tasks.rebuild.build_qdrant_gateway", lambda *_args: object())
    monkeypatch.setattr("app.tasks.rebuild.DocumentParsingService", FakePipeline)
    monkeypatch.setattr("app.tasks.rebuild.DocumentIndexingService", FakePipeline)
    monkeypatch.setattr("app.tasks.rebuild.KnowledgeEntryIndexingService", FakePipeline)
    monkeypatch.setattr("app.tasks.rebuild.KnowledgeBaseRebuildExecutor", FakeExecutor)
    operation_id, run_generation = uuid4(), uuid4()

    assert await _rebuild_knowledge_base(operation_id, run_generation)

    run.assert_awaited_once_with(operation_id, run_generation)
    assert FakeDatabase.instance is not None and FakeDatabase.instance.closed
    assert FakeQdrant.instance is not None and FakeQdrant.instance.closed
