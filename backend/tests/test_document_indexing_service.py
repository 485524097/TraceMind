from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.embedding import EmbeddingError
from app.indexing import (
    PayloadPoint,
    PayloadScrollResult,
    VectorIndexError,
    VectorPoint,
    VectorSearchHit,
)
from app.models.document import Document, DocumentChunk, DocumentVersion
from app.repositories.document_indexing import (
    ActiveGeneration,
    DocumentIndexingRepository,
    IndexingVersionRecord,
    IndexSnapshot,
)
from app.services.document_index_dispatcher import DocumentIndexingDispatcher
from app.services.document_indexing import (
    DocumentIndexingService,
    build_document_embedding_text,
    build_sparse_document_text,
    deterministic_point_id,
)
from app.services.exceptions import DocumentVersionNotFoundError, SemanticSearchUnavailableError
from app.services.retrieval_query import PreparedRetrievalQuery


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
        self.document_inputs: list[str] = []
        self.query_inputs: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_inputs = texts
        if self.on_embed is not None:
            self.on_embed()
        if self.error is not None:
            raise self.error
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        self.query_inputs.append(text)
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
        self.scroll_results: list[PayloadScrollResult] = []
        self.scroll_calls: list[dict[str, object]] = []
        self.ensure_calls = 0

    async def ensure_collection(self) -> None:
        self.ensure_calls += 1

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

    async def hybrid_search(
        self, vector: list[float], query: str, **kwargs: object
    ) -> list[VectorSearchHit]:
        self.search_calls.append({"vector": vector, "query": query, "hybrid": True, **kwargs})
        return self.hits

    async def scroll_symbol_matches(self, **kwargs: object) -> PayloadScrollResult:
        self.scroll_calls.append(kwargs)
        return self.scroll_results.pop(0) if self.scroll_results else PayloadScrollResult([])


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
        self.active_results: list[list[ActiveGeneration]] = []
        self.active_calls: list[tuple[UUID, UUID | None]] = []

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
        self, knowledge_base_id: UUID, *, document_id: UUID | None
    ) -> list[ActiveGeneration]:
        self.active_calls.append((knowledge_base_id, document_id))
        active = self.active_results.pop(0) if self.active_results else self.active
        if document_id is None:
            return active
        return [item for item in active if item.document_id == document_id]

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
        relative_path="sample.md",
        normalized_path="sample.md",
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
    provider = FakeProvider()
    service, session, repository, gateway, document, version = make_service(provider=provider)

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
    assert gateway.points[0].payload["content"] == repository.chunks[0].content
    assert gateway.points[0].payload["content_hash"] == repository.chunks[0].content_hash
    assert gateway.points[0].payload["relative_path"] == "sample.md"
    assert gateway.points[0].dense_vector == [1.0, 0.0, 0.0]
    assert gateway.points[0].sparse_text == "sample.md\nArchitecture\nTraceMind service"
    assert provider.document_inputs == [
        "Document: sample.md\n"
        "Section: Architecture\n"
        "Type: paragraph\n"
        "Language: python\n"
        "Content:\n"
        "TraceMind service"
    ]
    assert session.commit.await_count == 2


def test_path_is_added_to_dense_and_sparse_text_only_for_directory_documents() -> None:
    document, version, chunks = make_version()
    record = IndexingVersionRecord(document, version)
    assert "Path:" not in build_document_embedding_text(record, chunks[0])
    assert "Path:" not in build_sparse_document_text(record, chunks[0])

    document.relative_path = "backend/sample.md"
    assert "Path: backend/sample.md" in build_document_embedding_text(record, chunks[0])
    assert "Path: backend/sample.md" in build_sparse_document_text(record, chunks[0])


def test_document_embedding_text_omits_missing_optional_context_without_mutation() -> None:
    document, version, chunks = make_version()
    chunk = chunks[0]
    chunk.section_title = None
    chunk.language = None
    original_content = chunk.content

    text = build_document_embedding_text(IndexingVersionRecord(document, version), chunk)

    assert text == "Document: sample.md\nType: paragraph\nContent:\nTraceMind service"
    assert text.count(original_content) == 1
    assert "Section:" not in text
    assert "Language:" not in text
    assert chunk.content == original_content


def test_sparse_document_text_preserves_exact_identifiers_and_optional_section() -> None:
    document, version, chunks = make_version()
    chunk = chunks[0]
    chunk.content = "spring.cloud.nacos.discovery.server-addr DiscoveryClient SELECT_FOR_UPDATE"
    record = IndexingVersionRecord(document, version)

    text = build_sparse_document_text(record, chunk)

    assert text == (
        "sample.md\nArchitecture\n"
        "spring.cloud.nacos.discovery.server-addr DiscoveryClient SELECT_FOR_UPDATE"
    )
    chunk.section_title = None
    assert build_sparse_document_text(record, chunk) == f"sample.md\n{chunk.content}"


def test_symbol_context_is_stable_in_dense_and_sparse_text() -> None:
    document, version, chunks = make_version()
    document.name = "UserService.java"
    document.relative_path = "src/main/java/demo/UserService.java"
    chunk = chunks[0]
    chunk.content = "return username;"
    chunk.symbol_kind = "method"
    chunk.symbol_name = "source"
    chunk.symbol_qualified_name = "demo.UserService.source"
    chunk.symbol_signature = "public String source(String username)"
    record = IndexingVersionRecord(document, version)

    dense = build_document_embedding_text(record, chunk)
    sparse = build_sparse_document_text(record, chunk)

    expected = (
        "Path: src/main/java/demo/UserService.java\n"
        "Symbol: demo.UserService.source\n"
        "Signature: public String source(String username)\n"
        "Kind: method"
    )
    assert expected in dense
    assert expected in sparse
    assert dense == build_document_embedding_text(record, chunk)
    assert sparse == build_sparse_document_text(record, chunk)
    assert chunk.content == "return username;"


async def test_index_point_and_search_result_round_trip_symbol_metadata() -> None:
    service, _, repository, gateway, document, version = make_service()
    chunk = repository.chunks[0]
    chunk.symbol_kind = "method"
    chunk.symbol_name = "来源"
    chunk.symbol_qualified_name = "示例.服务.来源"
    chunk.symbol_signature = "String 来源(String 名称)"
    chunk.symbol_lookup_keys = ["v1:method:示例.服务#来源(String)"]

    assert await service.index_version(version.id)
    payload = gateway.points[0].payload
    assert payload["symbol_kind"] == "method"
    assert payload["symbol_name"] == "来源"
    assert payload["symbol_qualified_name"] == "示例.服务.来源"
    assert payload["symbol_signature"] == "String 来源(String 名称)"
    assert payload["symbol_lookup_keys"] == ["v1:method:示例.服务#来源(String)"]

    generation = version.active_index_generation
    assert generation is not None
    repository.active = [ActiveGeneration(document.id, version.id, generation)]
    gateway.hits = [VectorSearchHit(0.9, payload)]
    result = (
        await service.search(
            document.knowledge_base_id,
            query="来源",
            limit=5,
            language=None,
            document_id=None,
        )
    )[0]
    assert result.symbol_qualified_name == "示例.服务.来源"
    assert result.symbol_signature == "String 来源(String 名称)"


async def test_old_or_invalid_qdrant_symbol_payload_is_safe_none() -> None:
    service, _, repository, gateway, document, version = make_service()
    generation = uuid4()
    repository.active = [ActiveGeneration(document.id, version.id, generation)]
    service_payload = DocumentIndexingService._point(
        repository.record, repository.chunks[0], generation, [1.0, 0.0, 0.0]
    ).payload
    for key in (
        "symbol_kind",
        "symbol_name",
        "symbol_qualified_name",
        "symbol_signature",
    ):
        service_payload.pop(key, None)
    service_payload["symbol_name"] = {"invalid": True}
    gateway.hits = [VectorSearchHit(0.9, service_payload)]

    result = (
        await service.search(
            document.knowledge_base_id,
            query="legacy",
            limit=5,
            language=None,
            document_id=None,
        )
    )[0]
    assert result.symbol_kind is None
    assert result.symbol_name is None
    assert result.symbol_qualified_name is None
    assert result.symbol_signature is None


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
    assert results[0].relative_path == document.name
    call = gateway.search_calls[0]
    assert call["knowledge_base_id"] == document.knowledge_base_id
    assert call["generations"] == [generation]
    assert call["language"] == "python"
    assert call["document_id"] == document.id
    assert call["score_threshold"] == 0.50
    assert call["excluded_chunk_types"] == ("heading",)
    assert call["symbol_lookup_key"] is None
    assert gateway.ensure_calls == 1
    assert gateway.scroll_calls == []


async def test_search_returns_empty_when_gateway_has_no_results_above_threshold() -> None:
    service, _, repository, gateway, document, version = make_service()
    repository.active = [ActiveGeneration(document.id, version.id, uuid4())]
    gateway.hits = []

    results = await service.search(
        document.knowledge_base_id,
        query="unanswered question",
        limit=5,
        language=None,
        document_id=None,
    )

    assert results == []
    assert gateway.search_calls[0]["score_threshold"] == 0.50


async def test_exact_symbol_empty_dense_result_uses_direct_scroll() -> None:
    provider = FakeProvider()
    service, _, repository, gateway, document, version = make_service(provider=provider)
    generation = uuid4()
    repository.active = [ActiveGeneration(document.id, version.id, generation)]
    lookup_key = "v1:method:sample#run"
    payload = DocumentIndexingService._point(
        repository.record, repository.chunks[0], generation, [1.0, 0.0, 0.0]
    ).payload
    payload.update(
        symbol_lookup_keys=[lookup_key],
        symbol_kind="method",
        symbol_qualified_name="sample.run",
    )
    gateway.scroll_results = [PayloadScrollResult([PayloadPoint("point", payload)])]
    prepared = PreparedRetrievalQuery(
        original_query="sample#run",
        semantic_query="sample#run",
        scoped_document_id=None,
        symbol_scope_mode="exact",
        scoped_symbol_lookup_key=lookup_key,
        scoped_symbol_kind="method",
        scoped_symbol_qualified_name="sample.run",
        symbol_fallback_query="sample#run",
    )

    results = await service.search(
        document.knowledge_base_id,
        query=prepared.original_query,
        limit=5,
        language="java",
        document_id=None,
        prepared_query=prepared,
    )

    assert provider.query_inputs == ["sample#run"]
    assert gateway.search_calls[0]["symbol_lookup_key"] == lookup_key
    assert results[0].score == 1.0
    assert results[0].ranking_mode == "symbol_exact"
    assert results[0].retrieval_score is None
    assert results[0].retrieval_rank == 1


async def test_hybrid_search_uses_active_generations_and_rrf_gateway() -> None:
    service, _, repository, gateway, document, version = make_service()
    generation = uuid4()
    repository.active = [ActiveGeneration(document.id, version.id, generation)]

    results = await service.hybrid_search(
        document.knowledge_base_id,
        query="DiscoveryClient",
        limit=5,
        language="java",
        document_id=document.id,
    )

    assert results == []
    call = gateway.search_calls[0]
    assert call["hybrid"] is True
    assert call["query"] == "DiscoveryClient"
    assert call["generations"] == [generation]
    assert call["dense_score_threshold"] == 0.50
    assert call["symbol_lookup_key"] is None
    assert gateway.ensure_calls == 1
    assert gateway.scroll_calls == []


async def test_scoped_query_drives_dense_and_hybrid_embedding_and_filter() -> None:
    provider = FakeProvider()
    service, _, repository, gateway, document, version = make_service(provider=provider)
    generation = uuid4()
    repository.active = [ActiveGeneration(document.id, version.id, generation)]
    prepared = PreparedRetrievalQuery(
        original_query="src/main/java/demo/UserService.java 中 source 方法返回什么？",
        semantic_query="source 方法返回什么？",
        scoped_document_id=document.id,
        path_scope_mode="exact",
        explicit_relative_path="src/main/java/demo/UserService.java",
    )

    await service.search(
        document.knowledge_base_id,
        query=prepared.original_query,
        limit=5,
        language="java",
        document_id=None,
        prepared_query=prepared,
    )
    await service.hybrid_search(
        document.knowledge_base_id,
        query=prepared.original_query,
        limit=5,
        language="java",
        document_id=None,
        prepared_query=prepared,
    )

    assert provider.query_inputs == [prepared.semantic_query, prepared.semantic_query]
    assert repository.active_calls == [
        (document.knowledge_base_id, document.id),
        (document.knowledge_base_id, document.id),
    ]
    assert gateway.search_calls[0]["document_id"] == document.id
    assert gateway.search_calls[1]["document_id"] == document.id
    assert gateway.search_calls[1]["query"] == prepared.semantic_query
    assert gateway.search_calls[0]["symbol_lookup_key"] is None
    assert gateway.search_calls[1]["symbol_lookup_key"] is None
    assert gateway.ensure_calls == 2
    assert gateway.scroll_calls == []


async def test_path_only_prepare_adds_no_symbol_qdrant_request() -> None:
    service, _, repository, gateway, document, version = make_service()
    generation = uuid4()
    repository.active = [ActiveGeneration(document.id, version.id, generation)]
    path_prepared = PreparedRetrievalQuery(
        original_query="src/main/java/demo/UserService.java 中 source 方法返回什么？",
        semantic_query="source 方法返回什么？",
        scoped_document_id=document.id,
        path_scope_mode="exact",
        explicit_relative_path="src/main/java/demo/UserService.java",
    )
    service.path_resolver.prepare = AsyncMock(return_value=path_prepared)

    prepared = await service.prepare_retrieval_query(
        document.knowledge_base_id,
        path_prepared.original_query,
        document_id=None,
        language="java",
    )
    assert prepared == path_prepared
    assert gateway.ensure_calls == 0
    assert gateway.scroll_calls == []

    await service.search(
        document.knowledge_base_id,
        query=prepared.original_query,
        limit=5,
        language="java",
        document_id=None,
        prepared_query=prepared,
    )
    await service.hybrid_search(
        document.knowledge_base_id,
        query=prepared.original_query,
        limit=5,
        language="java",
        document_id=None,
        prepared_query=prepared,
    )

    assert gateway.ensure_calls == 2
    assert gateway.scroll_calls == []
    assert all(call["symbol_lookup_key"] is None for call in gateway.search_calls)


async def test_direct_symbol_retry_occurs_after_invalid_scoped_points() -> None:
    service, _, repository, gateway, document, version = make_service()
    first_generation, refreshed_generation = uuid4(), uuid4()
    initial = [ActiveGeneration(document.id, version.id, first_generation)]
    refreshed = [ActiveGeneration(document.id, version.id, refreshed_generation)]
    repository.active_results = [initial, refreshed]
    lookup_key = "v1:method:sample#run"
    invalid_payload = DocumentIndexingService._point(
        repository.record, repository.chunks[0], first_generation, [1.0, 0.0, 0.0]
    ).payload
    invalid_payload.update(
        symbol_kind="method",
        symbol_qualified_name="other.run",
    )
    valid_payload = dict(invalid_payload)
    valid_payload.update(
        index_generation=str(refreshed_generation),
        symbol_qualified_name="sample.run",
    )
    gateway.scroll_results = [
        PayloadScrollResult([PayloadPoint("invalid", invalid_payload)]),
        PayloadScrollResult([PayloadPoint("valid", valid_payload)]),
    ]
    prepared = PreparedRetrievalQuery(
        original_query="sample#run",
        semantic_query="sample#run",
        scoped_document_id=None,
        symbol_scope_mode="exact",
        scoped_symbol_lookup_key=lookup_key,
        scoped_symbol_kind="method",
        scoped_symbol_qualified_name="sample.run",
    )

    results = await service.search(
        document.knowledge_base_id,
        query=prepared.original_query,
        limit=5,
        language="java",
        document_id=None,
        prepared_query=prepared,
    )

    assert [result.symbol_qualified_name for result in results] == ["sample.run"]
    assert len(gateway.scroll_calls) == 2
    assert all(call["symbol_lookup_key"] == lookup_key for call in gateway.scroll_calls)
    assert all(call["document_id"] is None for call in gateway.scroll_calls)
    assert gateway.scroll_calls[0]["generations"] == [first_generation]
    assert gateway.scroll_calls[1]["generations"] == [refreshed_generation]


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
