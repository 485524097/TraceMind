import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.embedding import EmbeddingError, EmbeddingProvider, validate_embeddings
from app.indexing import QdrantGateway, VectorIndexError, VectorPoint
from app.models.document import DocumentChunk, DocumentVersion
from app.repositories.document_indexing import (
    DocumentIndexingRepository,
    IndexingVersionRecord,
    IndexSnapshot,
)
from app.services.document_index_dispatcher import DocumentIndexingDispatcher
from app.services.exceptions import (
    DocumentIndexingQueueError,
    DocumentNotReadyForIndexError,
    DocumentVersionNotFoundError,
    HybridSearchUnavailableError,
    SemanticSearchUnavailableError,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IndexRequestResult:
    queued: bool
    version: DocumentVersion


@dataclass(frozen=True)
class IndexClaim:
    record: IndexingVersionRecord
    attempt_generation: UUID
    previous: IndexSnapshot | None
    cleanup_generations: frozenset[UUID]


@dataclass(frozen=True)
class SemanticSearchResult:
    score: float
    content: str
    knowledge_base_id: UUID
    document_id: UUID
    document_version_id: UUID
    chunk_id: UUID
    index_generation: UUID
    document_name: str
    version_number: int
    chunk_index: int
    content_hash: str
    chunk_type: str
    language: str | None
    section_title: str | None
    page_number: int | None
    start_line: int | None
    end_line: int | None
    ranking_mode: str | None = None
    retrieval_score: float | None = None
    rerank_score: float | None = None
    retrieval_rank: int | None = None


def deterministic_point_id(version_id: UUID, generation: UUID, chunk_index: int) -> UUID:
    return uuid5(NAMESPACE_URL, f"{version_id}:{generation}:{chunk_index}")


def build_document_embedding_text(record: IndexingVersionRecord, chunk: DocumentChunk) -> str:
    lines = [f"Document: {record.document.name}"]
    if chunk.section_title:
        lines.append(f"Section: {chunk.section_title}")
    lines.append(f"Type: {chunk.chunk_type}")
    if chunk.language:
        lines.append(f"Language: {chunk.language}")
    lines.extend(("Content:", chunk.content))
    return "\n".join(lines)


def build_sparse_document_text(record: IndexingVersionRecord, chunk: DocumentChunk) -> str:
    parts = [record.document.name]
    if chunk.section_title:
        parts.append(chunk.section_title)
    parts.append(chunk.content)
    return "\n".join(parts)


class DocumentIndexingService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        provider: EmbeddingProvider,
        gateway: QdrantGateway,
        *,
        dispatcher: DocumentIndexingDispatcher | None = None,
        repository: DocumentIndexingRepository | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.provider = provider
        self.gateway = gateway
        self.dispatcher = dispatcher
        self.repository = repository or DocumentIndexingRepository(session)

    async def request_index(
        self,
        knowledge_base_id: UUID,
        document_id: UUID,
        version_id: UUID,
        *,
        force: bool,
    ) -> IndexRequestResult:
        version = await self._require_scoped_version(knowledge_base_id, document_id, version_id)
        if version.parse_status != "succeeded" or version.chunk_count <= 0:
            raise DocumentNotReadyForIndexError("Document version has no parsed chunks")
        dispatch_force = force
        if version.index_status == "processing":
            if not self.repository.is_processing_stale(
                version,
                now=datetime.now(UTC),
                stale_after_seconds=self.settings.document_index_stale_after_seconds,
            ):
                return IndexRequestResult(False, version)
            dispatch_force = True
        if version.index_status == "succeeded" and not force:
            return IndexRequestResult(False, version)
        if self.dispatcher is None:
            raise DocumentIndexingQueueError("Document indexing queue is unavailable")
        await self.dispatcher.enqueue(version.id, force=dispatch_force)
        return IndexRequestResult(True, version)

    async def get_status(
        self, knowledge_base_id: UUID, document_id: UUID, version_id: UUID
    ) -> DocumentVersion:
        return await self._require_scoped_version(knowledge_base_id, document_id, version_id)

    async def index_version(self, version_id: UUID, *, force: bool = False) -> bool:
        claim = await self._claim(version_id, force=force)
        if claim is None:
            return False
        generation = claim.attempt_generation
        try:
            chunks = await self.repository.list_chunks(version_id)
            if not chunks:
                raise DocumentNotReadyForIndexError("Document version has no parsed chunks")
            await self.gateway.ensure_collection()
            vectors = await asyncio.to_thread(
                self.provider.embed_documents,
                [build_document_embedding_text(claim.record, chunk) for chunk in chunks],
            )
            validate_embeddings(vectors, dimension=self.provider.dimension)
            if len(vectors) != len(chunks):
                raise EmbeddingError("Embedding provider returned an invalid vector count")
            points = [
                self._point(claim.record, chunk, generation, vector)
                for chunk, vector in zip(chunks, vectors, strict=True)
            ]
            await self.gateway.upsert(points)
            if await self.gateway.count_generation(generation) != len(chunks):
                raise VectorIndexError("Qdrant point count did not match the parsed chunks")
        except EmbeddingError:
            await self._fail_and_cleanup(
                version_id,
                generation,
                claim.previous,
                claim.cleanup_generations,
                "embedding_error",
                "Document embeddings could not be generated",
            )
            return False
        except DocumentNotReadyForIndexError:
            await self._fail_and_cleanup(
                version_id,
                generation,
                claim.previous,
                claim.cleanup_generations,
                "chunks_unavailable",
                "Document has no parsed chunks to index",
            )
            return False
        except VectorIndexError:
            await self._fail_and_cleanup(
                version_id,
                generation,
                claim.previous,
                claim.cleanup_generations,
                "vector_index_error",
                "Document vectors could not be stored",
            )
            return False
        except Exception as exc:
            logger.error(
                "Unexpected document indexing failure for version %s (%s)",
                version_id,
                type(exc).__name__,
            )
            await self._fail_and_cleanup(
                version_id,
                generation,
                claim.previous,
                claim.cleanup_generations,
                "internal_index_error",
                "Document could not be indexed",
            )
            return False

        try:
            record = await self.repository.lock_version(version_id)
            if record is None:
                await self.session.rollback()
                await self._cleanup_generation(generation)
                return False
            if not self.repository.is_current_attempt(record.version, generation):
                await self.session.rollback()
                logger.info("Document indexing attempt no longer owns version %s", version_id)
                await self._cleanup_generation(generation)
                return False
            await self.repository.mark_succeeded(
                record.version,
                generation=generation,
                chunk_count=len(chunks),
                model_name=self.provider.model_name,
                dimension=self.provider.dimension,
                indexed_at=datetime.now(UTC),
            )
            await self.session.commit()
        except Exception as exc:
            await self.session.rollback()
            logger.error(
                "Document index finalization failed for version %s (%s)",
                version_id,
                type(exc).__name__,
            )
            owns_attempt, safe_to_delete = await self._record_failure(
                version_id,
                generation,
                claim.previous,
                "index_finalize_error",
                "Document index could not be finalized",
            )
            if safe_to_delete:
                await self._cleanup_generation(generation)
            if owns_attempt:
                await self._cleanup_generations(claim.cleanup_generations)
            return False

        cleanup_generations = set(claim.cleanup_generations)
        if claim.previous is not None:
            cleanup_generations.add(claim.previous.generation)
        cleanup_generations.discard(generation)
        await self._cleanup_generations(cleanup_generations)
        return True

    async def search(
        self,
        knowledge_base_id: UUID,
        *,
        query: str,
        limit: int,
        language: str | None,
        document_id: UUID | None,
    ) -> list[SemanticSearchResult]:
        generations = await self.repository.list_active_generations(
            knowledge_base_id, document_id=document_id
        )
        if not generations:
            return []
        try:
            await self.gateway.ensure_collection()
            vector = await asyncio.to_thread(self.provider.embed_query, query)
            validate_embeddings([vector], dimension=self.provider.dimension)
            hits = await self.gateway.search(
                vector,
                knowledge_base_id=knowledge_base_id,
                generations=[item.generation for item in generations],
                limit=limit,
                language=language,
                document_id=document_id,
                score_threshold=self.settings.semantic_search_score_threshold,
                excluded_chunk_types=("heading",),
            )
            return [
                self._search_result(hit.score, hit.payload, ranking_mode="dense") for hit in hits
            ]
        except (EmbeddingError, VectorIndexError) as exc:
            raise SemanticSearchUnavailableError("Semantic search is unavailable") from exc

    async def hybrid_search(
        self,
        knowledge_base_id: UUID,
        *,
        query: str,
        limit: int,
        language: str | None,
        document_id: UUID | None,
    ) -> list[SemanticSearchResult]:
        generations = await self.repository.list_active_generations(
            knowledge_base_id, document_id=document_id
        )
        if not generations:
            return []
        try:
            await self.gateway.ensure_collection()
            vector = await asyncio.to_thread(self.provider.embed_query, query)
            validate_embeddings([vector], dimension=self.provider.dimension)
            hits = await self.gateway.hybrid_search(
                vector,
                query,
                knowledge_base_id=knowledge_base_id,
                generations=[item.generation for item in generations],
                limit=limit,
                language=language,
                document_id=document_id,
                dense_score_threshold=self.settings.semantic_search_score_threshold,
                excluded_chunk_types=("heading",),
            )
            return [
                self._search_result(
                    hit.score,
                    hit.payload,
                    ranking_mode="hybrid",
                    retrieval_score=hit.score,
                    retrieval_rank=rank,
                )
                for rank, hit in enumerate(hits, start=1)
            ]
        except (EmbeddingError, VectorIndexError) as exc:
            raise HybridSearchUnavailableError("Hybrid search is unavailable") from exc

    async def _claim(self, version_id: UUID, *, force: bool) -> IndexClaim | None:
        record = await self.repository.lock_version(version_id)
        if record is None:
            raise DocumentVersionNotFoundError("Document version was not found")
        version = record.version
        if version.parse_status != "succeeded" or version.chunk_count <= 0:
            await self.session.rollback()
            raise DocumentNotReadyForIndexError("Document version has no parsed chunks")
        now = datetime.now(UTC)
        if version.index_status == "processing" and not self.repository.is_processing_stale(
            version,
            now=now,
            stale_after_seconds=self.settings.document_index_stale_after_seconds,
        ):
            await self.session.rollback()
            return None
        if version.index_status == "succeeded" and not force:
            await self.session.rollback()
            return None
        stale_attempt = version.index_attempt_generation
        previous = self.repository.snapshot_active(version)
        cleanup_generations: set[UUID] = set()
        if stale_attempt is not None:
            cleanup_generations.add(stale_attempt)
        if version.active_index_generation is not None and previous is None:
            cleanup_generations.add(version.active_index_generation)
        attempt_generation = uuid4()
        cleanup_generations.discard(attempt_generation)
        await self.repository.mark_processing(version, attempt_generation, now)
        await self.session.commit()
        return IndexClaim(
            record,
            attempt_generation,
            previous,
            frozenset(cleanup_generations),
        )

    async def _fail_and_cleanup(
        self,
        version_id: UUID,
        generation: UUID,
        previous: IndexSnapshot | None,
        cleanup_generations: frozenset[UUID],
        code: str,
        message: str,
    ) -> None:
        owns_attempt, safe_to_delete = await self._record_failure(
            version_id, generation, previous, code, message
        )
        if safe_to_delete:
            await self._cleanup_generation(generation)
        if owns_attempt:
            await self._cleanup_generations(cleanup_generations)

    async def _record_failure(
        self,
        version_id: UUID,
        generation: UUID,
        previous: IndexSnapshot | None,
        code: str,
        message: str,
    ) -> tuple[bool, bool]:
        await self.session.rollback()
        record = await self.repository.lock_version(version_id)
        if record is None:
            await self.session.rollback()
            return False, True
        if not self.repository.is_current_attempt(record.version, generation):
            active = record.version.active_index_generation == generation
            await self.session.rollback()
            return False, not active
        await self.repository.mark_failed(
            record.version,
            code=code,
            message=message,
            previous=previous,
        )
        await self.session.commit()
        return True, True

    async def _cleanup_generations(self, generations: set[UUID] | frozenset[UUID]) -> None:
        for generation in generations:
            await self._cleanup_generation(generation)

    async def _cleanup_generation(self, generation: UUID) -> None:
        try:
            await self.gateway.delete_generation(generation)
        except VectorIndexError:
            logger.warning("A stale Qdrant index generation requires later cleanup")

    async def _require_scoped_version(
        self, knowledge_base_id: UUID, document_id: UUID, version_id: UUID
    ) -> DocumentVersion:
        version = await self.repository.get_scoped_version(
            knowledge_base_id, document_id, version_id
        )
        if version is None:
            raise DocumentVersionNotFoundError("Document version was not found")
        return version

    @staticmethod
    def _point(
        record: IndexingVersionRecord,
        chunk: DocumentChunk,
        generation: UUID,
        vector: list[float],
    ) -> VectorPoint:
        document = record.document
        version = record.version
        return VectorPoint(
            id=deterministic_point_id(version.id, generation, chunk.chunk_index),
            dense_vector=vector,
            sparse_text=build_sparse_document_text(record, chunk),
            payload={
                "knowledge_base_id": str(document.knowledge_base_id),
                "document_id": str(document.id),
                "document_version_id": str(version.id),
                "chunk_id": str(chunk.id),
                "index_generation": str(generation),
                "document_name": document.name,
                "version_number": version.version_number,
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                "content_hash": chunk.content_hash,
                "chunk_type": chunk.chunk_type,
                "language": chunk.language,
                "section_title": chunk.section_title,
                "page_number": chunk.page_number,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
            },
        )

    @staticmethod
    def _search_result(
        score: float,
        payload: dict[str, Any],
        *,
        ranking_mode: str,
        retrieval_score: float | None = None,
        retrieval_rank: int | None = None,
    ) -> SemanticSearchResult:
        try:
            return SemanticSearchResult(
                score=score,
                content=str(payload["content"]),
                knowledge_base_id=UUID(str(payload["knowledge_base_id"])),
                document_id=UUID(str(payload["document_id"])),
                document_version_id=UUID(str(payload["document_version_id"])),
                chunk_id=UUID(str(payload["chunk_id"])),
                index_generation=UUID(str(payload["index_generation"])),
                document_name=str(payload["document_name"]),
                version_number=int(payload["version_number"]),
                chunk_index=int(payload["chunk_index"]),
                content_hash=str(payload["content_hash"]),
                chunk_type=str(payload["chunk_type"]),
                language=str(payload["language"]) if payload.get("language") else None,
                section_title=(
                    str(payload["section_title"]) if payload.get("section_title") else None
                ),
                page_number=int(payload["page_number"]) if payload.get("page_number") else None,
                start_line=int(payload["start_line"]) if payload.get("start_line") else None,
                end_line=int(payload["end_line"]) if payload.get("end_line") else None,
                ranking_mode=ranking_mode,
                retrieval_score=retrieval_score,
                retrieval_rank=retrieval_rank,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticSearchUnavailableError("Semantic search payload is invalid") from exc
