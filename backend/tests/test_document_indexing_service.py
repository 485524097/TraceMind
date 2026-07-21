from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.embedding import EmbeddingError
from app.indexing import VectorIndexError, VectorPoint, VectorSearchHit
from app.models.document import Document, DocumentChunk, DocumentVersion
from app.repositories.document_indexing import (
    ActiveGeneration,
    DocumentIndexingRepository,
    IndexingVersionRecord,
    IndexSnapshot,
)
from app.services.document_index_dispatcher import DocumentIndexingDispatcher
from app.services.document_indexing import DocumentIndexingService, deterministic_point_id
from app.services.exceptions import DocumentVersionNotFoundError, SemanticSearchUnavailableError


class FakeProvider:
    model_name = "fake-embedding"
    dimension = 3

    def __init__(
        self,
        *,
        error: Exception | None = None,
        on_embed: Callable[[], None] | None = None,
    ) -> None:
        self.error = error
        self.on_embed = on_embed

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.on_embed is not None:
            self.on_embed()
        if self.error is not None:
            raise self.error
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, _text: str) -> list[float]:
        if self.error is not None:
            raise self.error
        return [1.0, 0.0, 0.0]


class FakeGateway:
    def __init__(self, *, upsert_error: Exception | None = None) -> None:
        self.upsert_error = upsert_error
        self.points: list[VectorPoint] = []
        self.deleted_generations: list[UUID] = []
        self.search_calls: list[dict[str, object]] = []
        self.hits: list[VectorSearchHit] = []

    async def ensure_collection(self) -> None:
        return None

    async def upsert(self, points: list[VectorPoint]) -> None:
        self.points = points
        if self.upsert_error is not None:
            raise self.upsert_error

    async def count_generation(self, _generation: UUID) -> int:
        return len(self.points)

    async def delete_generation(self, generation: UUID) -> None:
        self.deleted_generations.append(generation)

    async def search(self, vector: list[float], **kwargs: object) -> list[VectorSearchHit]:
        self.search_calls.append({"vector": vector, **kwargs})
        return self.hits


class FakeDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, bool]] = []

    async def enqueue(self, version_id: UUID, *, force: bool = False) -> None:
        self.calls.append((version_id, force))


class FakeRepository:
    def __init__(
        self, document: Document, version: DocumentVersion, chunks: list[DocumentChunk]
    ) -> None:
        self.record = IndexingVersionRecord(document, version)
        self.chunks = chunks
        self.active: list[ActiveGeneration] = []

    async def lock_version(self, version_id: UUID) -> IndexingVersionRecord | None:
        return self.record if version_id == self.record.version.id else None

    async def get_scoped_version(
        self, knowledge_base_id: UUID, document_id: UUID, version_id: UUID
    ) -> DocumentVersion | None:
        if (
            knowledge_base_id == self.record.document.knowledge_base_id
            and document_id == self.record.document.id
            and version_id == self.record.version.id
        ):
            return self.record.version
        return None

    async def list_chunks(self, _version_id: UUID) -> list[DocumentChunk]:
        return self.chunks

    async def list_active_generations(
        self, _knowledge_base_id: UUID, *, document_id: UUID | None
    ) -> list[ActiveGeneration]:
        if document_id is None:
            return self.active
        return [item for item in self.active if item.document_id == document_id]

    is_processing_stale = staticmethod(DocumentIndexingRepository.is_processing_stale)
    is_current_attempt = staticmethod(DocumentIndexingRepository.is_current_attempt)
    has_usable_active_index = staticmethod(DocumentIndexingRepository.has_usable_active_index)
    snapshot_active = staticmethod(DocumentIndexingRepository.snapshot_active)

    async def mark_processing(
        self, version: DocumentVersion, attempt_generation: UUID, now: datetime
    ) -> None:
        version.index_status = "processing"
        version.index_attempt_generation = attempt_generation
        version.index_started_at = now
        version.last_index_attempt_at = now

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
        version.indexed_chunk_count = chunk_count
        version.embedding_model = model_name
        version.embedding_dimension = dimension
        version.indexed_at = indexed_at
        version.index_error_code = None
        version.index_error_message = None

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
            version.indexed_chunk_count = 0
        else:
            version.index_status = "succeeded"
            version.active_index_generation = previous.generation
            version.indexed_at = previous.indexed_at
            version.indexed_chunk_count = previous.chunk_count
            version.embedding_model = previous.embedding_model
            version.embedding_dimension = previous.embedding_dimension
        version.index_attempt_generation = None
        version.index_error_code = code
        version.index_error_message = message


def make_version() -> tuple[Document, DocumentVersion, list[DocumentChunk]]:
    document = Document(
        id=uuid4(),
        knowledge_base_id=uuid4(),
        name="sample.md",
        normalized_name="sample.md",
        source_type="upload",
    )
    version = DocumentVersion(
        id=uuid4(),
        document_id=document.id,
        version_number=1,
        content_hash="a" * 64,
        file_size=7,
        mime_type="text/markdown",
        extension=".md",
        storage_path="safe/content.md",
        parse_status="succeeded",
        chunk_count=1,
        parsed_at=datetime.now(UTC),
        index_status="pending",
        indexed_chunk_count=0,
    )
    chunk = DocumentChunk(
        id=uuid4(),
        document_version_id=version.id,
        chunk_index=0,
        content="TraceMind service",
        content_hash="b" * 64,
        char_count=17,
        page_number=None,
        start_line=2,
        end_line=2,
        section_title="Architecture",
        chunk_type="paragraph",
        language="python",
    )
    return document, version, [chunk]


def make_service(
    *,
    provider: FakeProvider | None = None,
    gateway: FakeGateway | None = None,
    dispatcher: FakeDispatcher | None = None,
) -> tuple[
    DocumentIndexingService,
    AsyncMock,
    FakeRepository,
    FakeGateway,
    Document,
    DocumentVersion,
]:
    document, version, chunks = make_version()
    session = AsyncMock(spec=AsyncSession)
    repository = FakeRepository(document, version, chunks)
    fake_gateway = gateway or FakeGateway()
    service = DocumentIndexingService(
        cast(AsyncSession, session),
        Settings(embedding_dimension=3),
        provider or FakeProvider(),
        cast(object, fake_gateway),
        dispatcher=(cast(DocumentIndexingDispatcher, dispatcher) if dispatcher else None),
        repository=cast(DocumentIndexingRepository, repository),
    )
    return service, session, repository, fake_gateway, document, version


async def test_successful_index_writes_traceable_point_and_activates_generation() -> None:
    service, session, _, gateway, document, version = make_service()

    assert await service.index_version(version.id)

    generation = version.active_index_generation
    assert generation is not None
    assert version.index_status == "succeeded"
    assert version.index_attempt_generation is None
    assert version.indexed_chunk_count == 1
    assert version.embedding_model == "fake-embedding"
    assert gateway.points[0].id == deterministic_point_id(version.id, generation, 0)
    assert gateway.points[0].payload["knowledge_base_id"] == str(document.knowledge_base_id)
    assert gateway.points[0].payload["section_title"] == "Architecture"
    assert session.commit.await_count == 2


async def test_force_reindex_replaces_generation_and_cleans_previous() -> None:
    observed_claim: list[tuple[str, UUID | None, UUID | None]] = []
    service, _, _, gateway, _, version = make_service(
        provider=FakeProvider(
            on_embed=lambda: observed_claim.append(
                (
                    version.index_status,
                    version.active_index_generation,
                    version.index_attempt_generation,
                )
            )
        )
    )
    previous = uuid4()
    version.index_status = "succeeded"
    version.active_index_generation = previous
    version.indexed_at = version.parsed_at
    version.indexed_chunk_count = 1
    version.embedding_model = "old"
    version.embedding_dimension = 3

    assert await service.index_version(version.id, force=True)
    assert len(observed_claim) == 1
    status, active_during_claim, attempt_during_claim = observed_claim[0]
    assert status == "processing"
    assert active_during_claim == previous
    assert attempt_during_claim is not None
    assert version.active_index_generation != previous
    assert version.index_attempt_generation is None
    assert gateway.deleted_generations == [previous]
    assert not await service.index_version(version.id)


async def test_partial_batch_failure_preserves_usable_active_generation() -> None:
    service, _, _, gateway, _, version = make_service(
        gateway=FakeGateway(upsert_error=VectorIndexError("second batch failed"))
    )
    active = uuid4()
    version.index_status = "succeeded"
    version.active_index_generation = active
    version.indexed_at = version.parsed_at
    version.indexed_chunk_count = 1
    version.embedding_model = "old"
    version.embedding_dimension = 3

    assert not await service.index_version(version.id, force=True)
    assert version.index_status == "succeeded"
    assert version.active_index_generation == active
    assert version.index_attempt_generation is None
    assert version.index_error_code == "vector_index_error"
    assert active not in gateway.deleted_generations
    assert len(gateway.deleted_generations) == 1


async def test_stale_processing_is_taken_over() -> None:
    service, _, _, gateway, _, version = make_service()
    active, stale_attempt = uuid4(), uuid4()
    version.index_status = "processing"
    version.index_started_at = datetime.now(UTC) - timedelta(hours=1)
    version.active_index_generation = active
    version.index_attempt_generation = stale_attempt
    version.indexed_at = version.parsed_at

    assert await service.index_version(version.id)
    assert version.index_status == "succeeded"
    assert version.active_index_generation not in {active, stale_attempt}
    assert version.index_attempt_generation is None
    assert set(gateway.deleted_generations) == {active, stale_attempt}


async def test_stale_takeover_failure_restores_active_and_cleans_attempts() -> None:
    service, _, _, gateway, _, version = make_service(
        gateway=FakeGateway(upsert_error=VectorIndexError("partial"))
    )
    active, stale_attempt = uuid4(), uuid4()
    version.index_status = "processing"
    version.active_index_generation = active
    version.index_attempt_generation = stale_attempt
    version.index_started_at = datetime.now(UTC) - timedelta(hours=1)
    version.indexed_at = version.parsed_at
    version.indexed_chunk_count = 1
    version.embedding_model = "old"
    version.embedding_dimension = 3

    assert not await service.index_version(version.id)
    assert version.index_status == "succeeded"
    assert version.active_index_generation == active
    assert version.index_attempt_generation is None
    assert active not in gateway.deleted_generations
    assert stale_attempt in gateway.deleted_generations
    assert len(gateway.deleted_generations) == 2


async def test_manual_request_distinguishes_fresh_and_stale_processing() -> None:
    dispatcher = FakeDispatcher()
    service, _, _, _, document, version = make_service(dispatcher=dispatcher)
    version.index_status = "processing"
    version.index_attempt_generation = uuid4()
    version.index_started_at = datetime.now(UTC)

    fresh = await service.request_index(
        document.knowledge_base_id, document.id, version.id, force=False
    )
    assert not fresh.queued
    assert dispatcher.calls == []

    version.index_started_at = datetime.now(UTC) - timedelta(hours=1)
    stale = await service.request_index(
        document.knowledge_base_id, document.id, version.id, force=False
    )
    assert stale.queued
    assert dispatcher.calls == [(version.id, True)]


async def test_manual_force_reindexes_succeeded_and_scope_is_preserved() -> None:
    dispatcher = FakeDispatcher()
    service, _, _, _, document, version = make_service(dispatcher=dispatcher)
    version.index_status = "succeeded"
    version.active_index_generation = uuid4()
    version.indexed_at = version.parsed_at

    skipped = await service.request_index(
        document.knowledge_base_id, document.id, version.id, force=False
    )
    queued = await service.request_index(
        document.knowledge_base_id, document.id, version.id, force=True
    )

    assert not skipped.queued
    assert queued.queued
    assert dispatcher.calls == [(version.id, True)]
    try:
        await service.get_status(uuid4(), document.id, version.id)
    except DocumentVersionNotFoundError:
        pass
    else:
        raise AssertionError("Cross-knowledge-base version lookup must fail")


async def test_old_worker_cannot_activate_after_new_generation_takes_ownership() -> None:
    newer_generation = uuid4()
    active = uuid4()
    service, _, _, gateway, _, version = make_service(
        provider=FakeProvider(
            on_embed=lambda: (
                setattr(version, "index_status", "processing"),
                setattr(version, "active_index_generation", active),
                setattr(version, "index_attempt_generation", newer_generation),
            )
        )
    )

    assert not await service.index_version(version.id)
    assert version.index_status == "processing"
    assert version.active_index_generation == active
    assert version.index_attempt_generation == newer_generation
    assert len(gateway.deleted_generations) == 1
    assert gateway.deleted_generations[0] not in {active, newer_generation}


async def test_old_worker_failure_only_cleans_its_own_generation() -> None:
    newer_attempt = uuid4()
    active = uuid4()
    service, _, _, gateway, _, version = make_service(
        provider=FakeProvider(
            error=EmbeddingError("failed"),
            on_embed=lambda: (
                setattr(version, "index_status", "processing"),
                setattr(version, "active_index_generation", active),
                setattr(version, "index_attempt_generation", newer_attempt),
            ),
        )
    )

    assert not await service.index_version(version.id)
    assert version.index_status == "processing"
    assert version.active_index_generation == active
    assert version.index_attempt_generation == newer_attempt
    assert len(gateway.deleted_generations) == 1
    assert gateway.deleted_generations[0] not in {active, newer_attempt}


async def test_partial_qdrant_failure_marks_failed_and_cleans_generation() -> None:
    service, _, _, gateway, _, version = make_service(
        gateway=FakeGateway(upsert_error=VectorIndexError("partial"))
    )

    assert not await service.index_version(version.id)
    assert version.index_status == "failed"
    assert version.index_error_code == "vector_index_error"
    assert len(gateway.deleted_generations) == 1


async def test_failed_index_after_reparse_does_not_restore_obsolete_generation() -> None:
    service, _, _, gateway, _, version = make_service(
        gateway=FakeGateway(upsert_error=VectorIndexError("partial"))
    )
    obsolete = uuid4()
    version.index_status = "pending"
    version.active_index_generation = obsolete
    version.indexed_at = version.parsed_at - timedelta(seconds=1)
    version.indexed_chunk_count = 1
    version.embedding_model = "old"
    version.embedding_dimension = 3

    assert not await service.index_version(version.id)
    assert version.index_status == "failed"
    assert version.active_index_generation is None
    assert obsolete in gateway.deleted_generations


async def test_embedding_failure_is_safe_and_does_not_expose_provider_error() -> None:
    service, _, _, _, _, version = make_service(
        provider=FakeProvider(error=EmbeddingError("C:\\private\\model"))
    )

    assert not await service.index_version(version.id)
    assert version.index_error_code == "embedding_error"
    assert "private" not in (version.index_error_message or "")


async def test_db_finalization_failure_records_failure_and_removes_points() -> None:
    service, session, _, gateway, _, version = make_service()
    commits = 0

    async def commit() -> None:
        nonlocal commits
        commits += 1
        if commits == 2:
            version.index_status = "processing"
            version.active_index_generation = None
            version.index_attempt_generation = UUID(
                str(gateway.points[0].payload["index_generation"])
            )
            raise RuntimeError("database unavailable")

    session.commit.side_effect = commit

    assert not await service.index_version(version.id)
    assert version.index_status == "failed"
    assert version.index_error_code == "index_finalize_error"
    assert len(gateway.deleted_generations) == 1


async def test_search_uses_database_generations_and_filters() -> None:
    service, _, repository, gateway, document, version = make_service()
    generation = uuid4()
    repository.active = [ActiveGeneration(document.id, version.id, generation)]
    gateway.hits = [
        VectorSearchHit(
            0.9,
            {
                "knowledge_base_id": str(document.knowledge_base_id),
                "document_id": str(document.id),
                "document_version_id": str(version.id),
                "chunk_id": str(repository.chunks[0].id),
                "index_generation": str(generation),
                "document_name": document.name,
                "version_number": 1,
                "chunk_index": 0,
                "content": "TraceMind service",
                "content_hash": "b" * 64,
                "chunk_type": "paragraph",
                "language": "python",
                "section_title": "Architecture",
                "page_number": None,
                "start_line": 2,
                "end_line": 2,
            },
        )
    ]

    results = await service.search(
        document.knowledge_base_id,
        query="service layer",
        limit=5,
        language="python",
        document_id=document.id,
    )

    assert results[0].document_id == document.id
    call = gateway.search_calls[0]
    assert call["knowledge_base_id"] == document.knowledge_base_id
    assert call["generations"] == [generation]
    assert call["language"] == "python"
    assert call["document_id"] == document.id


async def test_search_returns_empty_without_database_active_generation() -> None:
    service, _, _, gateway, document, _ = make_service()

    assert (
        await service.search(
            document.knowledge_base_id,
            query="deleted document",
            limit=10,
            language=None,
            document_id=None,
        )
        == []
    )
    assert gateway.search_calls == []


async def test_qdrant_unavailable_is_converted_to_controlled_search_error() -> None:
    class UnavailableGateway(FakeGateway):
        async def ensure_collection(self) -> None:
            raise VectorIndexError("private endpoint")

    service, _, repository, _, document, version = make_service(gateway=UnavailableGateway())
    repository.active = [ActiveGeneration(document.id, version.id, uuid4())]

    try:
        await service.search(
            document.knowledge_base_id,
            query="service",
            limit=10,
            language=None,
            document_id=None,
        )
    except SemanticSearchUnavailableError as exc:
        assert "private" not in str(exc)
    else:
        raise AssertionError("Qdrant failure must use the controlled search exception")
