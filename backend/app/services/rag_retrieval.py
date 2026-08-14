import asyncio
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from typing import Any, Protocol
from uuid import UUID

from app.core.config import Settings
from app.embedding import EmbeddingError, EmbeddingProvider, validate_embeddings
from app.indexing import QdrantGateway, VectorIndexError
from app.repositories.knowledge_entry_indexing import KnowledgeEntryIndexingRepository
from app.services.document_indexing import (
    DocumentIndexingService,
    PreparedHybridSearch,
    SemanticSearchResult,
)
from app.services.exceptions import HybridSearchUnavailableError
from app.services.retrieval_query import PreparedRetrievalQuery


@dataclass(frozen=True)
class KnowledgeSearchResult:
    score: float
    content: str
    knowledge_base_id: UUID
    knowledge_entry_id: UUID
    chunk_id: UUID
    index_generation: UUID
    knowledge_question: str
    knowledge_updated_at: datetime
    chunk_index: int
    content_hash: str
    chunk_type: str
    section_title: str | None
    ranking_mode: str | None = None
    retrieval_score: float | None = None
    rerank_score: float | None = None
    retrieval_rank: int | None = None

    @property
    def source_type(self) -> str:
        return "knowledge_entry"

    @property
    def retrieval_id(self) -> UUID:
        return self.chunk_id

    @property
    def title(self) -> str:
        return self.knowledge_question


RetrievalSearchResult = SemanticSearchResult | KnowledgeSearchResult


@dataclass(frozen=True)
class RagHybridRetrievalResult:
    items: list[RetrievalSearchResult]
    embedding_latency_ms: int
    qdrant_latency_ms: int
    fusion_latency_ms: int
    dense_candidate_count: int
    sparse_candidate_count: int


class RagRetrievalServiceProtocol(Protocol):
    async def prepare_retrieval_query(
        self, knowledge_base_id: UUID, query: str, *, document_id: UUID | None
    ) -> PreparedRetrievalQuery: ...

    async def hybrid_search(
        self,
        knowledge_base_id: UUID,
        *,
        query: str,
        limit: int,
        language: str | None,
        document_id: UUID | None,
        prepared_query: PreparedRetrievalQuery | None = None,
    ) -> list[RetrievalSearchResult]: ...

    async def prepare_hybrid_search(
        self,
        knowledge_base_id: UUID,
        *,
        query: str,
        limit: int,
        language: str | None,
        document_id: UUID | None,
        prepared_query: PreparedRetrievalQuery | None = None,
    ) -> PreparedHybridSearch: ...

    async def execute_hybrid_search(
        self, prepared: PreparedHybridSearch
    ) -> RagHybridRetrievalResult: ...


class RagRetrievalService:
    def __init__(
        self,
        document_service: DocumentIndexingService,
        settings: Settings,
        provider: EmbeddingProvider,
        gateway: QdrantGateway,
        knowledge_repository: KnowledgeEntryIndexingRepository,
    ) -> None:
        self.document_service = document_service
        self.settings = settings
        self.provider = provider
        self.gateway = gateway
        self.knowledge_repository = knowledge_repository

    async def prepare_retrieval_query(
        self, knowledge_base_id: UUID, query: str, *, document_id: UUID | None
    ) -> PreparedRetrievalQuery:
        return await self.document_service.prepare_retrieval_query(
            knowledge_base_id, query, document_id=document_id
        )

    async def hybrid_search(
        self,
        knowledge_base_id: UUID,
        *,
        query: str,
        limit: int,
        language: str | None,
        document_id: UUID | None,
        prepared_query: PreparedRetrievalQuery | None = None,
    ) -> list[RetrievalSearchResult]:
        prepared = await self.prepare_hybrid_search(
            knowledge_base_id,
            query=query,
            limit=limit,
            language=language,
            document_id=document_id,
            prepared_query=prepared_query,
        )
        return (await self.execute_hybrid_search(prepared)).items

    async def prepare_hybrid_search(
        self,
        knowledge_base_id: UUID,
        *,
        query: str,
        limit: int,
        language: str | None,
        document_id: UUID | None,
        prepared_query: PreparedRetrievalQuery | None = None,
    ) -> PreparedHybridSearch:
        prepared = prepared_query or await self.prepare_retrieval_query(
            knowledge_base_id, query, document_id=document_id
        )
        document_generations = await self.document_service.list_active_generations(
            knowledge_base_id, document_id=prepared.scoped_document_id
        )
        generations = [item.generation for item in document_generations]
        if prepared.scoped_document_id is None and language is None:
            knowledge_generations = await self.knowledge_repository.list_active_generations(
                knowledge_base_id
            )
            generations.extend(item.generation for item in knowledge_generations)
        if not generations:
            return PreparedHybridSearch(knowledge_base_id, prepared, language, limit, (), None, 0)
        try:
            started_at = perf_counter()
            vector = await asyncio.to_thread(self.provider.embed_query, prepared.semantic_query)
            validate_embeddings([vector], dimension=self.provider.dimension)
            return PreparedHybridSearch(
                knowledge_base_id,
                prepared,
                language,
                limit,
                tuple(generations),
                vector,
                round((perf_counter() - started_at) * 1_000),
            )
        except EmbeddingError as exc:
            raise HybridSearchUnavailableError("Hybrid search is unavailable") from exc

    async def execute_hybrid_search(
        self, prepared: PreparedHybridSearch
    ) -> RagHybridRetrievalResult:
        if prepared.vector is None or not prepared.generations:
            return RagHybridRetrievalResult([], prepared.embedding_latency_ms, 0, 0, 0, 0)
        try:
            await self.gateway.ensure_collection()
            batch = await self.gateway.hybrid_search_with_diagnostics(
                prepared.vector,
                prepared.prepared_query.semantic_query,
                knowledge_base_id=prepared.knowledge_base_id,
                generations=list(prepared.generations),
                limit=prepared.limit,
                language=prepared.language,
                document_id=prepared.prepared_query.scoped_document_id,
                dense_score_threshold=self.settings.semantic_search_score_threshold,
                excluded_chunk_types=("heading",),
            )
            items = [
                self._result(
                    hit.score,
                    hit.payload,
                    retrieval_rank=rank,
                )
                for rank, hit in enumerate(batch.hits, start=1)
            ]
            return RagHybridRetrievalResult(
                items,
                prepared.embedding_latency_ms,
                batch.qdrant_latency_ms,
                batch.fusion_latency_ms,
                batch.dense_candidate_count,
                batch.sparse_candidate_count,
            )
        except VectorIndexError as exc:
            raise HybridSearchUnavailableError("Hybrid search is unavailable") from exc

    def _result(
        self, score: float, payload: dict[str, Any], *, retrieval_rank: int
    ) -> RetrievalSearchResult:
        if payload.get("source_type") != "knowledge_entry":
            return self.document_service.search_result(
                score,
                payload,
                ranking_mode="hybrid",
                retrieval_score=score,
                retrieval_rank=retrieval_rank,
            )
        try:
            return KnowledgeSearchResult(
                score=score,
                content=str(payload["content"]),
                knowledge_base_id=UUID(str(payload["knowledge_base_id"])),
                knowledge_entry_id=UUID(str(payload["knowledge_entry_id"])),
                chunk_id=UUID(str(payload["chunk_id"])),
                index_generation=UUID(str(payload["index_generation"])),
                knowledge_question=str(payload["knowledge_question"]),
                knowledge_updated_at=datetime.fromisoformat(str(payload["knowledge_updated_at"])),
                chunk_index=int(payload["chunk_index"]),
                content_hash=str(payload["content_hash"]),
                chunk_type="knowledge_entry",
                section_title=(
                    str(payload["section_title"]) if payload.get("section_title") else None
                ),
                ranking_mode="hybrid",
                retrieval_score=score,
                retrieval_rank=retrieval_rank,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise HybridSearchUnavailableError("Hybrid search payload is invalid") from exc
