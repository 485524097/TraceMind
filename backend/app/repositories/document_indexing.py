from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentChunk, DocumentVersion


@dataclass(frozen=True)
class IndexingVersionRecord:
    document: Document
    version: DocumentVersion


@dataclass(frozen=True)
class ActiveGeneration:
    document_id: UUID
    version_id: UUID
    generation: UUID


@dataclass(frozen=True)
class IndexSnapshot:
    generation: UUID
    indexed_at: datetime | None
    chunk_count: int
    embedding_model: str | None
    embedding_dimension: int | None


class DocumentIndexingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_version(self, version_id: UUID) -> IndexingVersionRecord | None:
        statement = (
            select(Document, DocumentVersion)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .where(DocumentVersion.id == version_id)
            .with_for_update(of=DocumentVersion)
            .execution_options(populate_existing=True)
        )
        row = (await self.session.execute(statement)).one_or_none()
        return IndexingVersionRecord(*row) if row is not None else None

    async def get_scoped_version(
        self, knowledge_base_id: UUID, document_id: UUID, version_id: UUID
    ) -> DocumentVersion | None:
        result = await self.session.execute(
            select(DocumentVersion)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                Document.knowledge_base_id == knowledge_base_id,
                Document.id == document_id,
                DocumentVersion.id == version_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_chunks(self, version_id: UUID) -> list[DocumentChunk]:
        result = await self.session.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_version_id == version_id)
            .order_by(DocumentChunk.chunk_index)
        )
        return list(result.scalars().all())

    async def list_active_generations(
        self, knowledge_base_id: UUID, *, document_id: UUID | None
    ) -> list[ActiveGeneration]:
        latest = (
            select(
                DocumentVersion.document_id.label("document_id"),
                func.max(DocumentVersion.version_number).label("version_number"),
            )
            .group_by(DocumentVersion.document_id)
            .subquery()
        )
        statement = (
            select(
                DocumentVersion.document_id,
                DocumentVersion.id,
                DocumentVersion.active_index_generation,
            )
            .join(Document, Document.id == DocumentVersion.document_id)
            .join(
                latest,
                (latest.c.document_id == DocumentVersion.document_id)
                & (latest.c.version_number == DocumentVersion.version_number),
            )
            .where(
                Document.knowledge_base_id == knowledge_base_id,
                DocumentVersion.index_status.in_(("succeeded", "processing")),
                DocumentVersion.active_index_generation.is_not(None),
                DocumentVersion.indexed_at.is_not(None),
                DocumentVersion.parsed_at.is_not(None),
                DocumentVersion.indexed_at >= DocumentVersion.parsed_at,
            )
        )
        if document_id is not None:
            statement = statement.where(DocumentVersion.document_id == document_id)
        rows = (await self.session.execute(statement)).all()
        return [
            ActiveGeneration(document, version, generation)
            for document, version, generation in rows
            if generation is not None
        ]

    @staticmethod
    def is_processing_stale(
        version: DocumentVersion, *, now: datetime, stale_after_seconds: int
    ) -> bool:
        if version.index_status != "processing" or version.index_started_at is None:
            return version.index_status == "processing"
        return version.index_started_at <= now - timedelta(seconds=stale_after_seconds)

    @staticmethod
    def is_current_attempt(version: DocumentVersion, attempt_generation: UUID) -> bool:
        return (
            version.index_status == "processing"
            and version.index_attempt_generation == attempt_generation
        )

    @staticmethod
    def has_usable_active_index(version: DocumentVersion) -> bool:
        if (
            version.active_index_generation is None
            or version.indexed_at is None
            or version.parsed_at is None
            or version.index_status not in {"succeeded", "processing"}
        ):
            return False

        def as_utc(value: datetime) -> datetime:
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC)
            return value.astimezone(UTC)

        return as_utc(version.indexed_at) >= as_utc(version.parsed_at)

    @classmethod
    def snapshot_active(cls, version: DocumentVersion) -> IndexSnapshot | None:
        generation = version.active_index_generation
        if generation is None or not cls.has_usable_active_index(version):
            return None
        return IndexSnapshot(
            generation=generation,
            indexed_at=version.indexed_at,
            chunk_count=version.indexed_chunk_count,
            embedding_model=version.embedding_model,
            embedding_dimension=version.embedding_dimension,
        )

    async def mark_processing(
        self, version: DocumentVersion, attempt_generation: UUID, now: datetime
    ) -> None:
        version.index_status = "processing"
        version.index_attempt_generation = attempt_generation
        version.index_started_at = now
        version.last_index_attempt_at = now
        version.index_error_code = None
        version.index_error_message = None
        await self.session.flush()

    async def mark_succeeded(
        self,
        version: DocumentVersion,
        *,
        generation: UUID,
        chunk_count: int,
        model_name: str,
        dimension: int,
        indexed_at: datetime,
    ) -> None:
        version.index_status = "succeeded"
        version.active_index_generation = generation
        version.index_attempt_generation = None
        version.indexed_at = indexed_at
        version.indexed_chunk_count = chunk_count
        version.embedding_model = model_name
        version.embedding_dimension = dimension
        version.index_error_code = None
        version.index_error_message = None
        await self.session.flush()

    async def mark_failed(
        self,
        version: DocumentVersion,
        *,
        code: str,
        message: str,
        previous: IndexSnapshot | None,
    ) -> None:
        if previous is None:
            version.index_status = "failed"
            version.active_index_generation = None
            version.indexed_at = None
            version.indexed_chunk_count = 0
            version.embedding_model = None
            version.embedding_dimension = None
        else:
            version.index_status = "succeeded"
            version.active_index_generation = previous.generation
            version.indexed_at = previous.indexed_at
            version.indexed_chunk_count = previous.chunk_count
            version.embedding_model = previous.embedding_model
            version.embedding_dimension = previous.embedding_dimension
        version.index_attempt_generation = None
        version.index_error_code = code
        version.index_error_message = message[:500]
        await self.session.flush()

    async def mark_pending_after_parse(self, version: DocumentVersion) -> None:
        version.index_status = "pending"
        version.index_attempt_generation = None
        version.index_error_code = None
        version.index_error_message = None
        await self.session.flush()
