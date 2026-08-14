from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.indexing import VectorPoint
from app.models.knowledge_entry import KnowledgeEntry
from app.repositories.knowledge_entry_indexing import KnowledgeIndexSnapshot
from app.services.knowledge_entry_indexing import KnowledgeEntryIndexingService


class FakeProvider:
    model_name = "fake-embedding"
    dimension = 3

    def __init__(self) -> None:
        self.inputs: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.inputs = texts
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, _text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


class FakeGateway:
    def __init__(self) -> None:
        self.points: list[VectorPoint] = []
        self.deleted_generations: list[UUID] = []

    async def ensure_collection(self) -> None:
        pass

    async def upsert(self, points: list[VectorPoint]) -> None:
        self.points = points

    async def count_generation(self, _generation: UUID) -> int:
        return len(self.points)

    async def delete_generation(self, generation: UUID) -> None:
        self.deleted_generations.append(generation)

    async def delete_knowledge_entry(self, _entry_id: UUID) -> None:
        pass


class FakeRepository:
    def __init__(self, entry: KnowledgeEntry) -> None:
        self.entry = entry

    async def lock_entry(self, entry_id: UUID) -> KnowledgeEntry | None:
        return self.entry if self.entry.id == entry_id else None

    @staticmethod
    def is_processing_stale(
        _entry: KnowledgeEntry, *, now: datetime, stale_after_seconds: int
    ) -> bool:
        del now, stale_after_seconds
        return False

    @staticmethod
    def is_current_attempt(entry: KnowledgeEntry, generation: UUID) -> bool:
        return entry.index_status == "processing" and entry.index_attempt_generation == generation

    @staticmethod
    def snapshot_active(entry: KnowledgeEntry) -> KnowledgeIndexSnapshot | None:
        if entry.active_index_generation is None:
            return None
        return KnowledgeIndexSnapshot(
            entry.active_index_generation,
            entry.indexed_at,
            entry.indexed_source_updated_at,
            entry.indexed_chunk_count,
            entry.embedding_model,
            entry.embedding_dimension,
        )

    async def mark_processing(self, entry: KnowledgeEntry, generation: UUID, now: datetime) -> None:
        entry.index_status = "processing"
        entry.index_attempt_generation = generation
        entry.index_started_at = now

    async def mark_succeeded(self, entry: KnowledgeEntry, **values: object) -> None:
        entry.index_status = "succeeded"
        entry.active_index_generation = cast(UUID, values["generation"])
        entry.index_attempt_generation = None
        entry.indexed_source_updated_at = cast(datetime, values["source_updated_at"])
        entry.indexed_chunk_count = cast(int, values["chunk_count"])
        entry.embedding_model = cast(str, values["model_name"])
        entry.embedding_dimension = cast(int, values["dimension"])

    async def mark_not_indexed(self, entry: KnowledgeEntry) -> None:
        entry.index_status = "not_indexed"
        entry.active_index_generation = None
        entry.index_attempt_generation = None

    async def mark_failed(self, entry: KnowledgeEntry, **_values: object) -> None:
        entry.index_status = "failed"
        entry.index_attempt_generation = None


def make_entry(*, status: str = "verified") -> KnowledgeEntry:
    now = datetime.now(UTC)
    return KnowledgeEntry(
        id=uuid4(),
        knowledge_base_id=uuid4(),
        question="事务为什么失败？",
        background="并发请求同时写入。",
        root_cause="事务边界分裂。",
        solution="把写操作放进同一个事务。",
        failed_attempts=["只增加重试"],
        validation_status=status,
        tags=["postgres", "transaction"],
        question_snapshot="事务为什么失败？",
        answer_snapshot="历史回答不应进入索引。",
        sources_snapshot=[],
        index_status="pending",
        indexed_chunk_count=0,
        created_at=now,
        updated_at=now,
    )


def make_service(entry: KnowledgeEntry) -> tuple[KnowledgeEntryIndexingService, FakeGateway]:
    session = AsyncMock(spec=AsyncSession)
    gateway = FakeGateway()
    service = KnowledgeEntryIndexingService(
        cast(AsyncSession, session),
        Settings(embedding_dimension=3),
        FakeProvider(),
        cast(object, gateway),
        repository=cast(object, FakeRepository(entry)),
    )
    return service, gateway


async def test_verified_entry_is_chunked_and_indexed_without_answer_snapshot() -> None:
    entry = make_entry()
    service, gateway = make_service(entry)

    assert await service.sync_entry(entry.id)

    assert entry.index_status == "succeeded"
    assert entry.active_index_generation is not None
    assert entry.indexed_chunk_count == len(gateway.points)
    assert gateway.points
    assert all(point.payload["source_type"] == "knowledge_entry" for point in gateway.points)
    assert all(point.payload["knowledge_entry_id"] == str(entry.id) for point in gateway.points)
    indexed_text = "\n".join(service.provider.inputs)
    assert entry.solution in indexed_text
    assert entry.answer_snapshot not in indexed_text


async def test_non_verified_entry_removes_active_generation_without_reindexing() -> None:
    entry = make_entry(status="outdated")
    previous = uuid4()
    entry.active_index_generation = previous
    entry.indexed_at = entry.updated_at
    entry.indexed_source_updated_at = entry.updated_at
    entry.indexed_chunk_count = 2
    entry.index_status = "pending"
    service, gateway = make_service(entry)

    assert await service.sync_entry(entry.id)

    assert entry.index_status == "not_indexed"
    assert entry.active_index_generation is None
    assert gateway.deleted_generations == [previous]
