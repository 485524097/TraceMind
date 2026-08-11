import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass, replace
from time import perf_counter
from uuid import UUID, uuid4

from app.core.config import Settings
from app.llm import LLMMessage, LLMProvider, LLMProviderError
from app.rag import RagContext, StreamingCitationGuard, build_rag_context, build_rag_messages
from app.reranker import RerankerError, RerankerUnavailableError
from app.services.conversation import ConversationTurn
from app.services.document_indexing import (
    DocumentIndexingService,
    HybridRetrievalResult,
    PreparedHybridSearch,
    SemanticSearchResult,
)
from app.services.document_reranking import DocumentRerankingService
from app.services.exceptions import HybridSearchUnavailableError, SemanticSearchUnavailableError
from app.services.query_rewrite import (
    HistoryAwareQueryRewriteService,
    QueryRewriteResult,
)
from app.services.query_router import RouteMode, route_query
from app.services.retrieval_query import PreparedRetrievalQuery

logger = logging.getLogger(__name__)
NO_ANSWER_MESSAGE = "知识库中未找到足够相关的信息。"
LLM_ERROR_MESSAGE = "回答生成服务暂时不可用，请稍后重试。"
DIRECT_SYSTEM_PROMPT = """你是 TraceMind，一个本地优先的个人工程知识助手。
当前消息是简单社交表达，不需要检索知识库。请用简洁、自然的中文回应。
不要声称已经检索资料，不要虚构来源，也不要添加 Citation。"""


@dataclass(frozen=True)
class PreparedRag:
    trace_id: UUID
    knowledge_base_id: UUID
    original_query: str
    retrieval_query: str
    conversation_history: tuple[ConversationTurn, ...]
    context: RagContext
    messages: list[LLMMessage] | None
    retrieval_latency_ms: int
    retrieval_mode: str
    rerank_latency_ms: int
    reranker_fallback: bool
    query_rewrite_mode: str
    query_rewrite_latency_ms: int
    query_rewrite_fallback_reason: str | None
    path_scope_mode: str
    scoped_relative_path: str | None
    embedding_latency_ms: int
    qdrant_latency_ms: int
    fusion_latency_ms: int
    dense_candidate_count: int
    sparse_candidate_count: int
    started_at: float
    route_mode: RouteMode = "rag"
    routing_latency_ms: int = 0


@dataclass(frozen=True)
class RetrievalPreparation:
    scoped_query: PreparedRetrievalQuery
    rewrite: QueryRewriteResult
    history: tuple[ConversationTurn, ...]


@dataclass(frozen=True)
class RerankOutcome:
    results: list[SemanticSearchResult]
    retrieval_mode: str
    latency_ms: int
    fallback: bool
    fallback_reason: str | None


class RagRetrievalUnavailableError(HybridSearchUnavailableError):
    def __init__(self, scope_metadata: dict[str, object]) -> None:
        super().__init__("Hybrid search is unavailable")
        self.scope_metadata = scope_metadata


class RagService:
    def __init__(
        self,
        indexing_service: DocumentIndexingService,
        provider: LLMProvider,
        settings: Settings,
        reranking_service: DocumentRerankingService | None = None,
        query_rewrite_service: HistoryAwareQueryRewriteService | None = None,
    ) -> None:
        self.indexing_service = indexing_service
        self.provider = provider
        self.settings = settings
        self.reranking_service = reranking_service
        self.query_rewrite_service = query_rewrite_service or HistoryAwareQueryRewriteService(
            provider, settings
        )

    async def prepare(
        self,
        knowledge_base_id: UUID,
        *,
        query: str,
        language: str | None,
        document_id: UUID | None,
        trace_id: UUID | None = None,
        conversation_id: UUID | None = None,
        conversation_history: tuple[ConversationTurn, ...] | None = None,
    ) -> PreparedRag:
        started_at = perf_counter()
        trace_id = trace_id or uuid4()
        preparation = await self._resolve_and_rewrite(
            knowledge_base_id,
            query=query,
            document_id=document_id,
            conversation_history=conversation_history,
        )
        retrieval_scope = replace(
            preparation.scoped_query,
            semantic_query=preparation.rewrite.query,
        )
        try:
            candidates = await self.indexing_service.hybrid_search(
                knowledge_base_id,
                query=preparation.rewrite.query,
                limit=self.settings.rag_rerank_candidate_limit,
                language=language,
                document_id=document_id,
                prepared_query=retrieval_scope,
            )
        except HybridSearchUnavailableError as exc:
            raise RagRetrievalUnavailableError(
                _scope_metadata_from_query(preparation.scoped_query)
            ) from exc
        retrieval = HybridRetrievalResult(candidates, 0, 0, 0, len(candidates), 0)
        reranked = await self._rerank(preparation.rewrite.query, retrieval.items)
        prepared = self._assemble(
            trace_id,
            knowledge_base_id,
            query,
            preparation,
            retrieval,
            reranked,
            started_at,
        )
        self._log_prepared(prepared, conversation_id)
        return prepared

    async def stream_query(
        self,
        knowledge_base_id: UUID,
        *,
        query: str,
        language: str | None,
        document_id: UUID | None,
        trace_id: UUID | None = None,
        conversation_id: UUID | None = None,
        conversation_history: tuple[ConversationTurn, ...] | None = None,
    ) -> AsyncGenerator[tuple[str, dict[str, object]]]:
        started_at = perf_counter()
        trace_id = trace_id or uuid4()

        yield "pipeline", self._pipeline(trace_id, "analyzing", "started")
        analyzing_started_at = perf_counter()
        yield (
            "pipeline",
            self._pipeline(
                trace_id,
                "analyzing",
                "completed",
                elapsed_ms=self._elapsed(analyzing_started_at),
            ),
        )

        yield "pipeline", self._pipeline(trace_id, "routing", "started")
        routing_started_at = perf_counter()
        route_mode = route_query(query)
        routing_latency_ms = self._elapsed(routing_started_at)
        yield (
            "pipeline",
            self._pipeline(
                trace_id,
                "routing",
                "completed",
                elapsed_ms=routing_latency_ms,
                route_mode=route_mode,
            ),
        )

        if route_mode == "direct":
            async for event in self._stream_direct(
                trace_id,
                knowledge_base_id,
                query,
                started_at,
                routing_latency_ms,
            ):
                yield event
            return

        yield "pipeline", self._pipeline(trace_id, "query_rewrite", "started")
        try:
            preparation = await self._resolve_and_rewrite(
                knowledge_base_id,
                query=query,
                document_id=document_id,
                conversation_history=conversation_history,
            )
        except RagRetrievalUnavailableError as exc:
            yield "pipeline", self._pipeline(trace_id, "query_rewrite", "failed")
            yield (
                "error",
                {
                    **exc.scope_metadata,
                    "trace_id": str(trace_id),
                    "route_mode": "rag",
                    "routing_latency_ms": routing_latency_ms,
                    "code": "retrieval_unavailable",
                    "message": LLM_ERROR_MESSAGE,
                },
            )
            return

        rewrite_status = {
            "fallback": "fallback",
            "not_applicable": "skipped",
            "skipped": "skipped",
            "rewritten": "completed",
        }[preparation.rewrite.mode]
        yield (
            "pipeline",
            self._pipeline(
                trace_id,
                "query_rewrite",
                rewrite_status,
                elapsed_ms=preparation.rewrite.latency_ms,
                fallback_reason=preparation.rewrite.fallback_reason,
            ),
        )

        yield "pipeline", self._pipeline(trace_id, "query_embedding", "started")
        try:
            hybrid = await self._prepare_hybrid(
                knowledge_base_id,
                preparation,
                language=language,
                document_id=document_id,
            )
        except HybridSearchUnavailableError:
            yield "pipeline", self._pipeline(trace_id, "query_embedding", "failed")
            yield (
                "error",
                {
                    **_scope_metadata_from_query(preparation.scoped_query),
                    "trace_id": str(trace_id),
                    "route_mode": "rag",
                    "routing_latency_ms": routing_latency_ms,
                    "code": "retrieval_unavailable",
                    "message": LLM_ERROR_MESSAGE,
                },
            )
            return
        embedding_status = "completed" if hybrid.vector is not None else "skipped"
        yield (
            "pipeline",
            self._pipeline(
                trace_id,
                "query_embedding",
                embedding_status,
                elapsed_ms=hybrid.embedding_latency_ms,
            ),
        )

        if hybrid.vector is None:
            retrieval = HybridRetrievalResult([], 0, 0, 0, 0, 0)
            yield "pipeline", self._pipeline(trace_id, "hybrid_retrieval", "skipped")
        else:
            yield "pipeline", self._pipeline(trace_id, "hybrid_retrieval", "started")
            try:
                retrieval = await self._execute_hybrid(hybrid)
            except HybridSearchUnavailableError:
                yield "pipeline", self._pipeline(trace_id, "hybrid_retrieval", "failed")
                yield (
                    "error",
                    {
                        **_scope_metadata_from_query(preparation.scoped_query),
                        "trace_id": str(trace_id),
                        "route_mode": "rag",
                        "routing_latency_ms": routing_latency_ms,
                        "code": "retrieval_unavailable",
                        "message": LLM_ERROR_MESSAGE,
                    },
                )
                return
            yield (
                "pipeline",
                self._pipeline(
                    trace_id,
                    "hybrid_retrieval",
                    "completed",
                    elapsed_ms=retrieval.qdrant_latency_ms + retrieval.fusion_latency_ms,
                    candidate_count=len(retrieval.items),
                ),
            )

        yield (
            "pipeline",
            self._pipeline(
                trace_id,
                "candidates",
                "completed",
                candidate_count=len(retrieval.items),
            ),
        )

        if retrieval.items and self.settings.reranker_enabled:
            yield "pipeline", self._pipeline(trace_id, "reranking", "started")
        reranked = await self._rerank(preparation.rewrite.query, retrieval.items)
        if not retrieval.items or not self.settings.reranker_enabled:
            rerank_status = "skipped"
        elif reranked.fallback:
            rerank_status = "fallback"
        else:
            rerank_status = "completed"
        yield (
            "pipeline",
            self._pipeline(
                trace_id,
                "reranking",
                rerank_status,
                elapsed_ms=reranked.latency_ms,
                candidate_count=len(reranked.results),
                fallback_reason=reranked.fallback_reason,
            ),
        )

        prepared = self._assemble(
            trace_id,
            knowledge_base_id,
            query,
            preparation,
            retrieval,
            reranked,
            started_at,
            routing_latency_ms=routing_latency_ms,
        )
        self._log_prepared(prepared, conversation_id)
        async for event in self.stream_answer(
            prepared, include_retrieval=True, include_pipeline=True
        ):
            yield event

    async def _resolve_and_rewrite(
        self,
        knowledge_base_id: UUID,
        *,
        query: str,
        document_id: UUID | None,
        conversation_history: tuple[ConversationTurn, ...] | None,
    ) -> RetrievalPreparation:
        try:
            scoped_query = await self.indexing_service.prepare_retrieval_query(
                knowledge_base_id,
                query,
                document_id=document_id,
            )
        except SemanticSearchUnavailableError as exc:
            raise RagRetrievalUnavailableError(
                exc.scope_metadata or _empty_scope_metadata()
            ) from exc
        history = conversation_history or ()
        rewrite = QueryRewriteResult(scoped_query.semantic_query, "not_applicable")
        if conversation_history is not None:
            rewrite = await self.query_rewrite_service.rewrite(scoped_query.semantic_query, history)
        return RetrievalPreparation(scoped_query, rewrite, history)

    async def _prepare_hybrid(
        self,
        knowledge_base_id: UUID,
        preparation: RetrievalPreparation,
        *,
        language: str | None,
        document_id: UUID | None,
    ) -> PreparedHybridSearch:
        retrieval_scope = replace(
            preparation.scoped_query,
            semantic_query=preparation.rewrite.query,
        )
        return await self.indexing_service.prepare_hybrid_search(
            knowledge_base_id,
            query=preparation.rewrite.query,
            limit=self.settings.rag_rerank_candidate_limit,
            language=language,
            document_id=document_id,
            prepared_query=retrieval_scope,
        )

    async def _execute_hybrid(
        self,
        prepared: PreparedHybridSearch,
    ) -> HybridRetrievalResult:
        try:
            return await self.indexing_service.execute_hybrid_search(prepared)
        except HybridSearchUnavailableError as exc:
            raise RagRetrievalUnavailableError(
                _scope_metadata_from_query(prepared.prepared_query)
            ) from exc

    async def _rerank(self, query: str, candidates: list[SemanticSearchResult]) -> RerankOutcome:
        results = candidates[: self.settings.rag_retrieval_limit]
        if not candidates or not self.settings.reranker_enabled:
            return RerankOutcome(results, "hybrid", 0, False, None)

        started_at = perf_counter()
        try:
            if self.reranking_service is None:
                raise RerankerUnavailableError(reason="unavailable")
            results = await self.reranking_service.rerank(
                query,
                candidates,
                limit=min(self.settings.rag_retrieval_limit, len(candidates)),
            )
            return RerankOutcome(
                results,
                "hybrid_reranker",
                self._elapsed(started_at),
                False,
                None,
            )
        except RerankerUnavailableError as exc:
            return RerankOutcome(
                results,
                "hybrid_fallback",
                self._elapsed(started_at),
                True,
                exc.reason,
            )
        except RerankerError:
            return RerankOutcome(
                results,
                "hybrid_fallback",
                self._elapsed(started_at),
                True,
                "internal_error",
            )

    def _assemble(
        self,
        trace_id: UUID,
        knowledge_base_id: UUID,
        original_query: str,
        preparation: RetrievalPreparation,
        retrieval: HybridRetrievalResult,
        reranked: RerankOutcome,
        started_at: float,
        *,
        routing_latency_ms: int = 0,
    ) -> PreparedRag:
        context = build_rag_context(reranked.results, self.settings.rag_max_context_chars)
        answer_history = (
            preparation.history if preparation.rewrite.mode in {"rewritten", "fallback"} else ()
        )
        messages = (
            build_rag_messages(
                original_query,
                context,
                answer_history,
                scoped_relative_path=preparation.scoped_query.explicit_relative_path,
            )
            if context.sources
            else None
        )
        retrieval_latency_ms = (
            preparation.rewrite.latency_ms
            + retrieval.embedding_latency_ms
            + retrieval.qdrant_latency_ms
            + retrieval.fusion_latency_ms
            + reranked.latency_ms
        )
        return PreparedRag(
            trace_id=trace_id,
            knowledge_base_id=knowledge_base_id,
            original_query=original_query,
            retrieval_query=preparation.rewrite.query,
            conversation_history=preparation.history,
            context=context,
            messages=messages,
            retrieval_latency_ms=retrieval_latency_ms,
            retrieval_mode=reranked.retrieval_mode,
            rerank_latency_ms=reranked.latency_ms,
            reranker_fallback=reranked.fallback,
            query_rewrite_mode=preparation.rewrite.mode,
            query_rewrite_latency_ms=preparation.rewrite.latency_ms,
            query_rewrite_fallback_reason=preparation.rewrite.fallback_reason,
            path_scope_mode=preparation.scoped_query.path_scope_mode,
            scoped_relative_path=preparation.scoped_query.explicit_relative_path,
            embedding_latency_ms=retrieval.embedding_latency_ms,
            qdrant_latency_ms=retrieval.qdrant_latency_ms,
            fusion_latency_ms=retrieval.fusion_latency_ms,
            dense_candidate_count=retrieval.dense_candidate_count,
            sparse_candidate_count=retrieval.sparse_candidate_count,
            started_at=started_at,
            routing_latency_ms=routing_latency_ms,
        )

    async def stream_answer(
        self,
        prepared: PreparedRag,
        *,
        include_retrieval: bool = True,
        include_pipeline: bool = False,
    ) -> AsyncGenerator[tuple[str, dict[str, object]]]:
        trace_id = str(prepared.trace_id)
        sources = [source.model_dump(mode="json") for source in prepared.context.sources]
        if include_retrieval:
            yield (
                "retrieval",
                {
                    **self._execution_metadata(prepared),
                    "source_count": len(sources),
                    "sources": sources,
                },
            )
        if prepared.messages is None:
            yield (
                "no_answer",
                {
                    **build_rag_scope_metadata(prepared),
                    "trace_id": trace_id,
                    "route_mode": "rag",
                    "message": NO_ANSWER_MESSAGE,
                },
            )
            if include_pipeline:
                yield "pipeline", self._pipeline(prepared.trace_id, "completed", "completed")
            yield "done", self._done(prepared, "no_answer", False, 0, 0, 0, 0)
            return

        if include_pipeline:
            yield "pipeline", self._pipeline(prepared.trace_id, "generating", "started")
        guard = StreamingCitationGuard({source.source_id for source in prepared.context.sources})
        llm_started_at = perf_counter()
        llm_first_token_latency_ms = 0
        finish_reason = "stop"
        try:
            stream = await self.provider.stream(prepared.messages)
            async with aclosing(stream):
                async for delta in stream:
                    finish_reason = delta.finish_reason or finish_reason
                    safe_text = guard.push(delta.text)
                    if safe_text:
                        if llm_first_token_latency_ms == 0:
                            llm_first_token_latency_ms = max(1, self._elapsed(llm_started_at))
                        yield (
                            "token",
                            {
                                "trace_id": trace_id,
                                "text": safe_text,
                                "llm_first_token_latency_ms": llm_first_token_latency_ms,
                            },
                        )
            tail = guard.finish()
            if tail:
                if llm_first_token_latency_ms == 0:
                    llm_first_token_latency_ms = max(1, self._elapsed(llm_started_at))
                yield (
                    "token",
                    {
                        "trace_id": trace_id,
                        "text": tail,
                        "llm_first_token_latency_ms": llm_first_token_latency_ms,
                    },
                )
        except asyncio.CancelledError:
            raise
        except LLMProviderError:
            if include_pipeline:
                yield "pipeline", self._pipeline(prepared.trace_id, "generating", "failed")
            yield (
                "error",
                {
                    **build_rag_scope_metadata(prepared),
                    "trace_id": trace_id,
                    "route_mode": "rag",
                    "code": "llm_unavailable",
                    "message": LLM_ERROR_MESSAGE,
                    "llm_first_token_latency_ms": llm_first_token_latency_ms,
                },
            )
            return

        llm_latency_ms = max(llm_first_token_latency_ms, self._elapsed(llm_started_at))
        if include_pipeline:
            yield (
                "pipeline",
                self._pipeline(
                    prepared.trace_id,
                    "generating",
                    "completed",
                    elapsed_ms=llm_latency_ms,
                ),
            )
            yield "pipeline", self._pipeline(prepared.trace_id, "completed", "completed")
        yield (
            "done",
            self._done(
                prepared,
                finish_reason,
                guard.grounded,
                guard.valid_citation_count,
                guard.invalid_citation_count,
                llm_latency_ms,
                llm_first_token_latency_ms,
            ),
        )

    async def _stream_direct(
        self,
        trace_id: UUID,
        knowledge_base_id: UUID,
        query: str,
        started_at: float,
        routing_latency_ms: int,
    ) -> AsyncGenerator[tuple[str, dict[str, object]]]:
        metadata = {
            "trace_id": str(trace_id),
            "route_mode": "direct",
            "routing_latency_ms": routing_latency_ms,
            "query_rewrite_latency_ms": 0,
            "embedding_latency_ms": 0,
            "qdrant_latency_ms": 0,
            "fusion_latency_ms": 0,
            "rerank_latency_ms": 0,
            "retrieval_latency_ms": 0,
            "dense_candidate_count": 0,
            "sparse_candidate_count": 0,
            "source_count": 0,
        }
        yield "retrieval", {**metadata, "sources": []}
        yield "pipeline", self._pipeline(trace_id, "generating", "started")
        llm_started_at = perf_counter()
        first_token_ms = 0
        finish_reason = "stop"
        try:
            stream = await self.provider.stream(
                [
                    LLMMessage(role="system", content=DIRECT_SYSTEM_PROMPT),
                    LLMMessage(role="user", content=query),
                ]
            )
            async with aclosing(stream):
                async for delta in stream:
                    finish_reason = delta.finish_reason or finish_reason
                    if not delta.text:
                        continue
                    if first_token_ms == 0:
                        first_token_ms = max(1, self._elapsed(llm_started_at))
                    yield (
                        "token",
                        {
                            "trace_id": str(trace_id),
                            "text": delta.text,
                            "llm_first_token_latency_ms": first_token_ms,
                        },
                    )
        except asyncio.CancelledError:
            raise
        except LLMProviderError:
            yield "pipeline", self._pipeline(trace_id, "generating", "failed")
            yield (
                "error",
                {
                    "trace_id": str(trace_id),
                    "route_mode": "direct",
                    "code": "llm_unavailable",
                    "message": LLM_ERROR_MESSAGE,
                    "llm_first_token_latency_ms": first_token_ms,
                },
            )
            return

        llm_latency_ms = max(first_token_ms, self._elapsed(llm_started_at))
        yield (
            "pipeline",
            self._pipeline(
                trace_id,
                "generating",
                "completed",
                elapsed_ms=llm_latency_ms,
            ),
        )
        yield "pipeline", self._pipeline(trace_id, "completed", "completed")
        yield (
            "done",
            {
                **metadata,
                "finish_reason": finish_reason,
                "grounded": False,
                "valid_citation_count": 0,
                "invalid_citation_count": 0,
                "llm_first_token_latency_ms": first_token_ms,
                "llm_latency_ms": llm_latency_ms,
                "total_latency_ms": self._elapsed(started_at),
            },
        )
        logger.info(
            "Direct answer completed trace_id=%s knowledge_base_id=%s llm_model=%s "
            "llm_latency_ms=%s total_latency_ms=%s",
            trace_id,
            knowledge_base_id,
            self.settings.llm_model,
            llm_latency_ms,
            self._elapsed(started_at),
        )

    @staticmethod
    def _done(
        prepared: PreparedRag,
        finish_reason: str,
        grounded: bool,
        valid_count: int,
        invalid_count: int,
        llm_latency_ms: int,
        llm_first_token_latency_ms: int,
    ) -> dict[str, object]:
        return {
            **RagService._execution_metadata(prepared),
            "finish_reason": finish_reason,
            "grounded": grounded,
            "valid_citation_count": valid_count,
            "invalid_citation_count": invalid_count,
            "source_count": len(prepared.context.sources),
            "llm_first_token_latency_ms": llm_first_token_latency_ms,
            "llm_latency_ms": llm_latency_ms,
            "total_latency_ms": RagService._elapsed(prepared.started_at),
        }

    @staticmethod
    def _execution_metadata(prepared: PreparedRag) -> dict[str, object]:
        return {
            **build_rag_scope_metadata(prepared),
            "trace_id": str(prepared.trace_id),
            "route_mode": prepared.route_mode,
            "routing_latency_ms": prepared.routing_latency_ms,
            "retrieval_latency_ms": prepared.retrieval_latency_ms,
            "retrieval_mode": prepared.retrieval_mode,
            "rerank_latency_ms": prepared.rerank_latency_ms,
            "reranker_fallback": prepared.reranker_fallback,
            "query_rewrite_mode": prepared.query_rewrite_mode,
            "query_rewrite_latency_ms": prepared.query_rewrite_latency_ms,
            "embedding_latency_ms": prepared.embedding_latency_ms,
            "qdrant_latency_ms": prepared.qdrant_latency_ms,
            "fusion_latency_ms": prepared.fusion_latency_ms,
            "dense_candidate_count": prepared.dense_candidate_count,
            "sparse_candidate_count": prepared.sparse_candidate_count,
            "history_turn_count": len(prepared.conversation_history),
        }

    @staticmethod
    def _pipeline(
        trace_id: UUID,
        phase: str,
        status: str,
        *,
        elapsed_ms: int | None = None,
        candidate_count: int | None = None,
        route_mode: RouteMode | None = None,
        fallback_reason: str | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "trace_id": str(trace_id),
            "phase": phase,
            "status": status,
        }
        if elapsed_ms is not None:
            payload["elapsed_ms"] = elapsed_ms
        if candidate_count is not None:
            payload["candidate_count"] = candidate_count
        if route_mode is not None:
            payload["route_mode"] = route_mode
        if fallback_reason is not None:
            payload["fallback_reason"] = fallback_reason
        return payload

    @staticmethod
    def _elapsed(started_at: float) -> int:
        return round((perf_counter() - started_at) * 1_000)

    def _log_prepared(self, prepared: PreparedRag, conversation_id: UUID | None) -> None:
        logger.info(
            "RAG retrieval trace_id=%s conversation_id=%s knowledge_base_id=%s "
            "retrieval_mode=%s source_count=%s rewrite_ms=%s embedding_ms=%s "
            "qdrant_ms=%s fusion_ms=%s rerank_ms=%s retrieval_ms=%s",
            prepared.trace_id,
            conversation_id,
            prepared.knowledge_base_id,
            prepared.retrieval_mode,
            len(prepared.context.sources),
            prepared.query_rewrite_latency_ms,
            prepared.embedding_latency_ms,
            prepared.qdrant_latency_ms,
            prepared.fusion_latency_ms,
            prepared.rerank_latency_ms,
            prepared.retrieval_latency_ms,
        )


def build_rag_scope_metadata(prepared: PreparedRag) -> dict[str, object]:
    return {
        "path_scope_mode": prepared.path_scope_mode,
        "scoped_relative_path": prepared.scoped_relative_path,
    }


def _scope_metadata_from_query(prepared: PreparedRetrievalQuery) -> dict[str, object]:
    return {
        "path_scope_mode": prepared.path_scope_mode,
        "scoped_relative_path": prepared.explicit_relative_path,
    }


def _empty_scope_metadata() -> dict[str, object]:
    return {
        "path_scope_mode": "none",
        "scoped_relative_path": None,
    }
