import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.embedding import EmbeddingError, EmbeddingProvider, validate_embeddings
from app.indexing import QdrantGateway, VectorIndexError, VectorPoint
from app.parsing.base import ParsedBlock
from app.parsing.chunker import ChunkDraft, DeterministicChunker
from app.repositories.knowledge_entry_indexing import (
    KnowledgeEntryIndexingRepository,
    KnowledgeIndexSnapshot,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KnowledgeIndexSource:
    id: UUID
    knowledge_base_id: UUID
    question: str
    background: str | None
    root_cause: str | None
    solution: str
    failed_attempts: tuple[str, ...]
    tags: tuple[str, ...]
    updated_at: datetime


@dataclass(frozen=True)
class KnowledgeIndexClaim:
    source: KnowledgeIndexSource
    attempt_generation: UUID
    previous: KnowledgeIndexSnapshot | None
    cleanup_generations: frozenset[UUID]
    mode: Literal["index", "remove"]


def build_knowledge_blocks(source: KnowledgeIndexSource) -> list[ParsedBlock]:
    values: list[tuple[str, str | None]] = [
        ("Question", source.question),
        ("Background", source.background),
        ("Root Cause", source.root_cause),
        ("Solution", source.solution),
        (
            "Failed Attempts",
            "\n".join(f"- {attempt}" for attempt in source.failed_attempts)
            if source.failed_attempts
            else None,
        ),
        ("Tags", ", ".join(source.tags) if source.tags else None),
    ]
    return [
        ParsedBlock(text=value, block_type="paragraph", section_title=section)
        for section, value in values
        if value and value.strip()
    ]


def build_knowledge_embedding_text(source: KnowledgeIndexSource, chunk: ChunkDraft) -> str:
    parts = ["Source: verified knowledge entry", f"Question: {source.question}"]
    if source.tags:
        parts.append(f"Tags: {', '.join(source.tags)}")
    if chunk.section_title:
        parts.append(f"Section: {chunk.section_title}")
    parts.extend(("Content:", chunk.content))
    return "\n".join(parts)


def build_knowledge_sparse_text(source: KnowledgeIndexSource, chunk: ChunkDraft) -> str:
    parts = [source.question, *source.tags]
    if chunk.section_title:
        parts.append(chunk.section_title)
    parts.append(chunk.content)
    return "\n".join(parts)


class KnowledgeEntryIndexingService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        provider: EmbeddingProvider,
        gateway: QdrantGateway,
        repository: KnowledgeEntryIndexingRepository | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.provider = provider
        self.gateway = gateway
        self.repository = repository or KnowledgeEntryIndexingRepository(session)
        self.chunker = DeterministicChunker(
            max_chars=settings.document_chunk_max_chars,
            overlap_chars=settings.document_chunk_overlap_chars,
        )

    async def sync_entry(self, entry_id: UUID, *, force: bool = False) -> bool:
        claim = await self._claim(entry_id, force=force)
        if claim is None:
            return False
        if claim.mode == "remove":
            await self._finalize_removal(entry_id, claim)
            return True

        generation = claim.attempt_generation
        chunks = self.chunker.chunk(build_knowledge_blocks(claim.source))
        try:
            await self.gateway.ensure_collection()
            vectors = await asyncio.to_thread(
                self.provider.embed_documents,
                [build_knowledge_embedding_text(claim.source, chunk) for chunk in chunks],
            )
            validate_embeddings(vectors, dimension=self.provider.dimension)
            if len(vectors) != len(chunks):
                raise EmbeddingError("Embedding provider returned an invalid vector count")
            await self.gateway.upsert(
                [
                    self._point(claim.source, chunk, generation, vector)
                    for chunk, vector in zip(chunks, vectors, strict=True)
                ]
            )
            if await self.gateway.count_generation(generation) != len(chunks):
                raise VectorIndexError("Knowledge entry point count did not match its chunks")
        except EmbeddingError:
            await self._fail_and_cleanup(
                entry_id, claim, "embedding_error", "Knowledge embeddings could not be generated"
            )
            return False
        except VectorIndexError:
            await self._fail_and_cleanup(
                entry_id, claim, "vector_index_error", "Knowledge vectors could not be stored"
            )
            return False
        except Exception as exc:
            logger.error(
                "Unexpected knowledge indexing failure for entry %s (%s)",
                entry_id,
                type(exc).__name__,
            )
            await self._fail_and_cleanup(
                entry_id, claim, "internal_index_error", "Knowledge entry could not be indexed"
            )
            return False

        entry = await self.repository.lock_entry(entry_id)
        if (
            entry is None
            or not self.repository.is_current_attempt(entry, generation)
            or entry.validation_status != "verified"
            or entry.updated_at != claim.source.updated_at
        ):
            await self.session.rollback()
            await self._cleanup_generation(generation)
            return False
        await self.repository.mark_succeeded(
            entry,
            generation=generation,
            source_updated_at=claim.source.updated_at,
            chunk_count=len(chunks),
            model_name=self.provider.model_name,
            dimension=self.provider.dimension,
            indexed_at=datetime.now(UTC),
        )
        await self.session.commit()
        cleanup = set(claim.cleanup_generations)
        if claim.previous is not None:
            cleanup.add(claim.previous.generation)
        cleanup.discard(generation)
        await self._cleanup_generations(cleanup)
        return True

    async def delete_entry(self, entry_id: UUID) -> bool:
        try:
            await self.gateway.ensure_collection()
            await self.gateway.delete_knowledge_entry(entry_id)
            return True
        except VectorIndexError:
            logger.warning("Deleted knowledge entry has orphaned Qdrant points requiring cleanup")
            return False

    async def _claim(self, entry_id: UUID, *, force: bool) -> KnowledgeIndexClaim | None:
        entry = await self.repository.lock_entry(entry_id)
        if entry is None:
            await self.session.rollback()
            return None
        now = datetime.now(UTC)
        if entry.index_status == "processing" and not self.repository.is_processing_stale(
            entry,
            now=now,
            stale_after_seconds=self.settings.document_index_stale_after_seconds,
        ):
            await self.session.rollback()
            return None
        previous = self.repository.snapshot_active(entry)
        if (
            entry.validation_status == "verified"
            and entry.index_status == "succeeded"
            and entry.indexed_source_updated_at == entry.updated_at
            and not force
        ):
            await self.session.rollback()
            return None
        if (
            entry.validation_status != "verified"
            and entry.active_index_generation is None
            and entry.index_attempt_generation is None
            and entry.index_status == "not_indexed"
        ):
            await self.session.rollback()
            return None

        cleanup: set[UUID] = set()
        if entry.index_attempt_generation is not None:
            cleanup.add(entry.index_attempt_generation)
        generation = uuid4()
        source = KnowledgeIndexSource(
            entry.id,
            entry.knowledge_base_id,
            entry.question,
            entry.background,
            entry.root_cause,
            entry.solution,
            tuple(entry.failed_attempts),
            tuple(entry.tags),
            entry.updated_at,
        )
        await self.repository.mark_processing(entry, generation, now)
        await self.session.commit()
        return KnowledgeIndexClaim(
            source,
            generation,
            previous,
            frozenset(cleanup),
            "index" if entry.validation_status == "verified" else "remove",
        )

    async def _finalize_removal(self, entry_id: UUID, claim: KnowledgeIndexClaim) -> None:
        entry = await self.repository.lock_entry(entry_id)
        if entry is None:
            await self.session.rollback()
            await self.delete_entry(entry_id)
            return
        if not self.repository.is_current_attempt(entry, claim.attempt_generation):
            await self.session.rollback()
            return
        await self.repository.mark_not_indexed(entry)
        await self.session.commit()
        cleanup = set(claim.cleanup_generations)
        if claim.previous is not None:
            cleanup.add(claim.previous.generation)
        await self._cleanup_generations(cleanup)

    async def _fail_and_cleanup(
        self, entry_id: UUID, claim: KnowledgeIndexClaim, code: str, message: str
    ) -> None:
        await self.session.rollback()
        entry = await self.repository.lock_entry(entry_id)
        if entry is None:
            await self.session.rollback()
            await self._cleanup_generation(claim.attempt_generation)
            return
        if not self.repository.is_current_attempt(entry, claim.attempt_generation):
            await self.session.rollback()
            await self._cleanup_generation(claim.attempt_generation)
            return
        await self.repository.mark_failed(
            entry, code=code, message=message, previous=claim.previous
        )
        await self.session.commit()
        await self._cleanup_generation(claim.attempt_generation)
        await self._cleanup_generations(claim.cleanup_generations)

    async def _cleanup_generations(self, generations: set[UUID] | frozenset[UUID]) -> None:
        for generation in generations:
            await self._cleanup_generation(generation)

    async def _cleanup_generation(self, generation: UUID) -> None:
        try:
            await self.gateway.delete_generation(generation)
        except VectorIndexError:
            logger.warning("A stale knowledge index generation requires later cleanup")

    @staticmethod
    def _point(
        source: KnowledgeIndexSource,
        chunk: ChunkDraft,
        generation: UUID,
        vector: list[float],
    ) -> VectorPoint:
        point_id = uuid5(
            NAMESPACE_URL,
            f"knowledge-entry:{source.id}:{generation}:{chunk.chunk_index}",
        )
        payload: dict[str, Any] = {
            "source_type": "knowledge_entry",
            "knowledge_base_id": str(source.knowledge_base_id),
            "knowledge_entry_id": str(source.id),
            "index_generation": str(generation),
            "knowledge_question": source.question,
            "knowledge_updated_at": source.updated_at.isoformat(),
            "validation_status": "verified",
            "chunk_id": str(point_id),
            "chunk_index": chunk.chunk_index,
            "content": chunk.content,
            "content_hash": chunk.content_hash,
            "chunk_type": "knowledge_entry",
            "section_title": chunk.section_title,
            "language": None,
            "page_number": None,
            "start_line": None,
            "end_line": None,
        }
        return VectorPoint(
            id=point_id,
            dense_vector=vector,
            sparse_text=build_knowledge_sparse_text(source, chunk),
            payload=payload,
        )
