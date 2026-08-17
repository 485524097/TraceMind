from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.knowledge_base_rebuild import (
    KnowledgeBaseRebuildItem,
    KnowledgeBaseRebuildOperation,
)
from app.repositories.knowledge_base_rebuild import (
    KnowledgeBaseRebuildRepository,
    RebuildCounts,
)
from app.services.knowledge_base_rebuild import (
    KnowledgeBaseRebuildExecutor,
    KnowledgeBaseRebuildService,
)
from app.services.knowledge_base_rebuild_dispatcher import KnowledgeBaseRebuildDispatcher


def operation(knowledge_base_id: UUID, *, status: str = "queued") -> KnowledgeBaseRebuildOperation:
    now = datetime.now(UTC)
    return KnowledgeBaseRebuildOperation(
        id=uuid4(),
        knowledge_base_id=knowledge_base_id,
        status=status,
        run_generation=uuid4(),
        created_at=now,
        updated_at=now,
    )


class FakeDispatcher:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[UUID, UUID]] = []

    async def enqueue(self, operation_id: UUID, run_generation: UUID) -> None:
        self.calls.append((operation_id, run_generation))
        if self.error is not None:
            raise self.error


class FakeControlRepository:
    def __init__(
        self, knowledge_base_id: UUID, current: KnowledgeBaseRebuildOperation | None = None
    ):
        self.knowledge_base_id = knowledge_base_id
        self.current = current
        self.queue_failed = False

    async def knowledge_base_exists(self, knowledge_base_id: UUID) -> bool:
        return knowledge_base_id == self.knowledge_base_id

    async def get_active_operation(
        self, knowledge_base_id: UUID
    ) -> KnowledgeBaseRebuildOperation | None:
        if (
            self.current is not None
            and self.current.knowledge_base_id == knowledge_base_id
            and self.current.status in {"queued", "running"}
        ):
            return self.current
        return None

    async def get_latest_operation(
        self, knowledge_base_id: UUID
    ) -> KnowledgeBaseRebuildOperation | None:
        return self.current if knowledge_base_id == self.knowledge_base_id else None

    async def create_operation(self, knowledge_base_id: UUID) -> KnowledgeBaseRebuildOperation:
        self.current = operation(knowledge_base_id)
        return self.current

    async def counts(self, _operation_id: UUID) -> RebuildCounts:
        return RebuildCounts(document_versions_total=3, documents_total=1)

    async def mark_queue_failed(
        self, operation_id: UUID, run_generation: UUID, message: str
    ) -> None:
        assert self.current is not None
        assert (operation_id, run_generation) == (
            self.current.id,
            self.current.run_generation,
        )
        self.current.status = "failed"
        self.current.error_code = "queue_unavailable"
        self.current.error_message = message
        self.queue_failed = True

    async def prepare_retry(
        self, operation_id: UUID, *, stale_after_seconds: int
    ) -> tuple[KnowledgeBaseRebuildOperation, bool]:
        assert self.current is not None and self.current.id == operation_id
        assert stale_after_seconds > 0
        if self.current.status not in {"failed", "partially_failed"}:
            return self.current, False
        self.current.status = "queued"
        self.current.run_generation = uuid4()
        self.current.error_code = None
        self.current.error_message = None
        return self.current, True


class FakeExecutorRepository:
    def __init__(self, items: list[KnowledgeBaseRebuildItem]) -> None:
        self.items = items
        self.parse_succeeded: set[UUID] = set()
        self.document_index_succeeded_ids: set[UUID] = set()
        self.knowledge_index_succeeded: set[UUID] = set()
        self.historical_versions: set[UUID] = set()
        self.status = "queued"

    async def claim_operation(self, _operation_id: UUID, _generation: UUID) -> bool:
        if self.status != "queued":
            return False
        self.status = "running"
        return True

    async def list_retryable_items(self, _operation_id: UUID) -> list[KnowledgeBaseRebuildItem]:
        priority = {
            "document_parse": 1,
            "document_index": 2,
            "knowledge_entry_index": 3,
        }
        return sorted(
            [item for item in self.items if item.status in {"pending", "failed"}],
            key=lambda item: priority[item.work_type],
        )

    async def mark_item_running(
        self, item_id: UUID, _operation_id: UUID, _generation: UUID
    ) -> bool:
        item = self._item(item_id)
        item.status = "running"
        item.attempt_count += 1
        return True

    async def mark_item_succeeded(
        self, item_id: UUID, _operation_id: UUID, _generation: UUID
    ) -> bool:
        self._item(item_id).status = "succeeded"
        return True

    async def mark_item_failed(
        self,
        item_id: UUID,
        _operation_id: UUID,
        _generation: UUID,
        *,
        error_code: str,
        error_message: str,
    ) -> bool:
        item = self._item(item_id)
        item.status = "failed"
        item.error_code = error_code
        item.error_message = error_message
        return True

    async def finalize_operation(self, _operation_id: UUID, _generation: UUID) -> bool:
        failed = any(item.status == "failed" for item in self.items)
        succeeded = any(item.status == "succeeded" for item in self.items)
        self.status = (
            "partially_failed" if failed and succeeded else "failed" if failed else "succeeded"
        )
        return self.status == "succeeded"

    async def document_parse_succeeded(self, version_id: UUID) -> bool:
        return version_id in self.parse_succeeded

    async def document_version_is_latest(self, version_id: UUID) -> bool:
        return version_id not in self.historical_versions

    async def document_index_succeeded(self, version_id: UUID) -> bool:
        return version_id in self.document_index_succeeded_ids

    async def knowledge_entry_index_succeeded(self, entry_id: UUID) -> bool:
        return entry_id in self.knowledge_index_succeeded

    def prepare_retry(self) -> None:
        self.status = "queued"
        for item in self.items:
            if item.status == "failed":
                item.status = "pending"

    def _item(self, item_id: UUID) -> KnowledgeBaseRebuildItem:
        return next(item for item in self.items if item.id == item_id)


class FakeParser:
    def __init__(self, repository: FakeExecutorRepository, fail_once: set[UUID] | None = None):
        self.repository = repository
        self.fail_once = fail_once or set()
        self.calls: list[tuple[UUID, bool]] = []

    async def parse_version(
        self,
        version_id: UUID,
        *,
        force: bool = False,
        enqueue_index: bool = True,
    ) -> bool:
        self.calls.append((version_id, enqueue_index))
        if version_id in self.fail_once:
            self.fail_once.remove(version_id)
            return False
        self.repository.parse_succeeded.add(version_id)
        return True


class FakeDocumentIndexer:
    def __init__(self, repository: FakeExecutorRepository) -> None:
        self.repository = repository
        self.calls: list[UUID] = []

    async def index_version(self, version_id: UUID, *, force: bool = False) -> bool:
        self.calls.append(version_id)
        self.repository.document_index_succeeded_ids.add(version_id)
        return True


class FakeKnowledgeIndexer:
    def __init__(self, repository: FakeExecutorRepository) -> None:
        self.repository = repository
        self.calls: list[UUID] = []

    async def sync_entry(self, entry_id: UUID, *, force: bool = False) -> bool:
        self.calls.append(entry_id)
        self.repository.knowledge_index_succeeded.add(entry_id)
        return True


def item(operation_id: UUID, work_type: str, target_id: UUID) -> KnowledgeBaseRebuildItem:
    return KnowledgeBaseRebuildItem(
        id=uuid4(),
        operation_id=operation_id,
        work_type=work_type,
        target_id=target_id,
        status="pending",
        attempt_count=0,
    )


async def test_start_persists_failed_status_when_queue_is_unavailable() -> None:
    knowledge_base_id = uuid4()
    repository = FakeControlRepository(knowledge_base_id)
    dispatcher = FakeDispatcher(RuntimeError("redis unavailable"))
    session = AsyncMock(spec=AsyncSession)
    service = KnowledgeBaseRebuildService(
        cast(AsyncSession, session),
        Settings(),
        cast(KnowledgeBaseRebuildDispatcher, dispatcher),
        cast(KnowledgeBaseRebuildRepository, repository),
    )

    response = await service.start(knowledge_base_id)

    assert response.status == "failed"
    assert response.error_code == "queue_unavailable"
    assert response.document_versions_total == 3
    assert repository.queue_failed
    session.commit.assert_awaited_once()


async def test_retry_requeues_same_operation_without_creating_source_entities() -> None:
    knowledge_base_id = uuid4()
    current = operation(knowledge_base_id, status="partially_failed")
    repository = FakeControlRepository(knowledge_base_id, current)
    dispatcher = FakeDispatcher()
    session = AsyncMock(spec=AsyncSession)
    service = KnowledgeBaseRebuildService(
        cast(AsyncSession, session),
        Settings(),
        cast(KnowledgeBaseRebuildDispatcher, dispatcher),
        cast(KnowledgeBaseRebuildRepository, repository),
    )

    response = await service.retry(knowledge_base_id)

    assert response.operation_id == current.id
    assert response.status == "queued"
    assert dispatcher.calls == [(current.id, current.run_generation)]


async def test_executor_parses_all_versions_but_indexes_only_latest_and_verified_knowledge() -> (
    None
):
    operation_id, generation = uuid4(), uuid4()
    v1, v2, v3, other_v1, verified_entry = (uuid4() for _ in range(5))
    items = [
        item(operation_id, "document_parse", version_id) for version_id in (v1, v2, v3, other_v1)
    ]
    items.extend(
        [
            item(operation_id, "document_index", v3),
            item(operation_id, "document_index", other_v1),
            item(operation_id, "knowledge_entry_index", verified_entry),
        ]
    )
    repository = FakeExecutorRepository(items)
    parser = FakeParser(repository)
    document_indexer = FakeDocumentIndexer(repository)
    knowledge_indexer = FakeKnowledgeIndexer(repository)
    executor = KnowledgeBaseRebuildExecutor(
        cast(AsyncSession, AsyncMock(spec=AsyncSession)),
        parser,
        document_indexer,
        knowledge_indexer,
        cast(KnowledgeBaseRebuildRepository, repository),
    )

    assert await executor.run(operation_id, generation)

    assert parser.calls == [(v1, False), (v2, False), (v3, False), (other_v1, False)]
    assert document_indexer.calls == [v3, other_v1]
    assert knowledge_indexer.calls == [verified_entry]
    assert repository.status == "succeeded"


async def test_retry_only_reprocesses_failed_work_and_reaches_succeeded() -> None:
    operation_id, generation = uuid4(), uuid4()
    v1, latest = uuid4(), uuid4()
    items = [
        item(operation_id, "document_parse", v1),
        item(operation_id, "document_parse", latest),
        item(operation_id, "document_index", latest),
    ]
    repository = FakeExecutorRepository(items)
    parser = FakeParser(repository, fail_once={latest})
    document_indexer = FakeDocumentIndexer(repository)
    executor = KnowledgeBaseRebuildExecutor(
        cast(AsyncSession, AsyncMock(spec=AsyncSession)),
        parser,
        document_indexer,
        FakeKnowledgeIndexer(repository),
        cast(KnowledgeBaseRebuildRepository, repository),
    )

    assert not await executor.run(operation_id, generation)
    assert repository.status == "partially_failed"
    assert parser.calls == [(v1, False), (latest, False)]
    assert document_indexer.calls == []

    repository.prepare_retry()
    assert await executor.run(operation_id, uuid4())

    assert parser.calls == [(v1, False), (latest, False), (latest, False)]
    assert document_indexer.calls == [latest]
    assert items[0].attempt_count == 1
    assert items[1].attempt_count == 2
    assert repository.status == "succeeded"


async def test_rebuild_treats_frozen_version_that_became_historical_as_safe_skip() -> None:
    operation_id, generation, frozen_version = uuid4(), uuid4(), uuid4()
    items = [item(operation_id, "document_index", frozen_version)]
    repository = FakeExecutorRepository(items)
    repository.historical_versions.add(frozen_version)
    document_indexer = FakeDocumentIndexer(repository)
    executor = KnowledgeBaseRebuildExecutor(
        cast(AsyncSession, AsyncMock(spec=AsyncSession)),
        FakeParser(repository),
        document_indexer,
        FakeKnowledgeIndexer(repository),
        cast(KnowledgeBaseRebuildRepository, repository),
    )

    assert await executor.run(operation_id, generation)
    assert items[0].status == "succeeded"
    assert document_indexer.calls == []
