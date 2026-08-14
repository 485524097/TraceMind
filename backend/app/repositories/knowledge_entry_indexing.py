from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models.knowledge_entry import KnowledgeEntry


@dataclass(frozen=True)
class ActiveKnowledgeGeneration:
    entry_id: UUID
    generation: UUID


@dataclass(frozen=True)
class KnowledgeIndexSnapshot:
    generation: UUID
    indexed_at: datetime | None
    source_updated_at: datetime | None
    chunk_count: int
    embedding_model: str | None
    embedding_dimension: int | None


class KnowledgeEntryIndexingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_entry(self, entry_id: UUID) -> KnowledgeEntry | None:
        result = await self.session.execute(
            select(KnowledgeEntry)
            .where(KnowledgeEntry.id == entry_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def list_active_generations(
        self, knowledge_base_id: UUID
    ) -> list[ActiveKnowledgeGeneration]:
        result = await self.session.execute(
            select(KnowledgeEntry.id, KnowledgeEntry.active_index_generation).where(
                KnowledgeEntry.knowledge_base_id == knowledge_base_id,
                KnowledgeEntry.validation_status == "verified",
                KnowledgeEntry.index_status.in_(("succeeded", "processing")),
                KnowledgeEntry.active_index_generation.is_not(None),
                KnowledgeEntry.indexed_at.is_not(None),
                KnowledgeEntry.indexed_source_updated_at == KnowledgeEntry.updated_at,
            )
        )
        return [
            ActiveKnowledgeGeneration(entry_id, generation)
            for entry_id, generation in result
            if generation is not None
        ]

    @staticmethod
    def is_processing_stale(
        entry: KnowledgeEntry, *, now: datetime, stale_after_seconds: int
    ) -> bool:
        if entry.index_status != "processing" or entry.index_started_at is None:
            return entry.index_status == "processing"
        return entry.index_started_at <= now - timedelta(seconds=stale_after_seconds)

    @staticmethod
    def is_current_attempt(entry: KnowledgeEntry, generation: UUID) -> bool:
        return entry.index_status == "processing" and entry.index_attempt_generation == generation

    @staticmethod
    def snapshot_active(entry: KnowledgeEntry) -> KnowledgeIndexSnapshot | None:
        if entry.active_index_generation is None or entry.indexed_at is None:
            return None
        return KnowledgeIndexSnapshot(
            entry.active_index_generation,
            entry.indexed_at,
            entry.indexed_source_updated_at,
            entry.indexed_chunk_count,
            entry.embedding_model,
            entry.embedding_dimension,
        )

    async def mark_pending(self, entry: KnowledgeEntry) -> None:
        entry.index_status = "pending"
        entry.index_attempt_generation = None
        entry.index_started_at = None
        entry.index_error_code = None
        entry.index_error_message = None
        self._preserve_updated_at(entry)
        await self.session.flush()

    async def mark_processing(self, entry: KnowledgeEntry, generation: UUID, now: datetime) -> None:
        entry.index_status = "processing"
        entry.index_attempt_generation = generation
        entry.index_started_at = now
        entry.last_index_attempt_at = now
        entry.index_error_code = None
        entry.index_error_message = None
        self._preserve_updated_at(entry)
        await self.session.flush()

    async def mark_succeeded(
        self,
        entry: KnowledgeEntry,
        *,
        generation: UUID,
        source_updated_at: datetime,
        chunk_count: int,
        model_name: str,
        dimension: int,
        indexed_at: datetime,
    ) -> None:
        entry.index_status = "succeeded"
        entry.active_index_generation = generation
        entry.index_attempt_generation = None
        entry.index_started_at = None
        entry.indexed_at = indexed_at
        entry.indexed_source_updated_at = source_updated_at
        entry.indexed_chunk_count = chunk_count
        entry.embedding_model = model_name
        entry.embedding_dimension = dimension
        entry.index_error_code = None
        entry.index_error_message = None
        self._preserve_updated_at(entry)
        await self.session.flush()

    async def mark_not_indexed(self, entry: KnowledgeEntry) -> None:
        entry.index_status = "not_indexed"
        entry.active_index_generation = None
        entry.index_attempt_generation = None
        entry.index_started_at = None
        entry.indexed_at = None
        entry.indexed_source_updated_at = None
        entry.indexed_chunk_count = 0
        entry.embedding_model = None
        entry.embedding_dimension = None
        entry.index_error_code = None
        entry.index_error_message = None
        self._preserve_updated_at(entry)
        await self.session.flush()

    async def mark_failed(
        self,
        entry: KnowledgeEntry,
        *,
        code: str,
        message: str,
        previous: KnowledgeIndexSnapshot | None,
    ) -> None:
        can_restore = (
            previous is not None
            and previous.source_updated_at == entry.updated_at
            and entry.validation_status == "verified"
        )
        if can_restore and previous is not None:
            entry.index_status = "succeeded"
            entry.active_index_generation = previous.generation
            entry.indexed_at = previous.indexed_at
            entry.indexed_source_updated_at = previous.source_updated_at
            entry.indexed_chunk_count = previous.chunk_count
            entry.embedding_model = previous.embedding_model
            entry.embedding_dimension = previous.embedding_dimension
        else:
            entry.index_status = "failed"
            entry.active_index_generation = None
            entry.indexed_at = None
            entry.indexed_source_updated_at = None
            entry.indexed_chunk_count = 0
            entry.embedding_model = None
            entry.embedding_dimension = None
        entry.index_attempt_generation = None
        entry.index_started_at = None
        entry.index_error_code = code
        entry.index_error_message = message[:500]
        self._preserve_updated_at(entry)
        await self.session.flush()

    async def mark_queue_failed(self, entry: KnowledgeEntry) -> None:
        entry.index_status = "failed"
        entry.index_attempt_generation = None
        entry.index_started_at = None
        entry.index_error_code = "queue_unavailable"
        entry.index_error_message = "Knowledge entry indexing queue is unavailable"
        self._preserve_updated_at(entry)
        await self.session.flush()

    @staticmethod
    def _preserve_updated_at(entry: KnowledgeEntry) -> None:
        # Index state is derived metadata and must not make maintained knowledge look edited.
        if "updated_at" in entry.__dict__:
            flag_modified(entry, "updated_at")
