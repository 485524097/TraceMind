import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass
from time import perf_counter
from uuid import UUID, uuid4

from app.core.config import Settings
from app.llm import LLMMessage, LLMProvider, LLMProviderError
from app.rag import RagContext, StreamingCitationGuard, build_rag_context, build_rag_messages
from app.reranker import RerankerError, RerankerUnavailableError
from app.services.conversation import ConversationTurn
from app.services.document_indexing import DocumentIndexingService
from app.services.document_reranking import DocumentRerankingService
from app.services.query_rewrite import (
    HistoryAwareQueryRewriteService,
    QueryRewriteResult,
)
from app.services.retrieval_query import PreparedRetrievalQuery

logger = logging.getLogger(__name__)
NO_ANSWER_MESSAGE = "知识库中未找到足够相关的信息。"
LLM_ERROR_MESSAGE = "回答生成服务暂时不可用，请稍后重试。"


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
    started_at: float


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
        scoped_query = await self.indexing_service.prepare_retrieval_query(
            knowledge_base_id,
            query,
            document_id=document_id,
            resolve_symbol_scope=False,
        )
        rewrite = QueryRewriteResult(scoped_query.semantic_query, "not_applicable")
        history = conversation_history or ()
        if conversation_history is not None:
            rewrite = await self.query_rewrite_service.rewrite(scoped_query.semantic_query, history)
        retrieval_query = rewrite.query
        logger.info(
            "RAG query rewrite trace_id=%s conversation_id=%s query_rewrite_mode=%s "
            "history_turn_count=%s original_query_length=%s retrieval_query_length=%s "
            "query_rewrite_latency_ms=%s fallback_reason=%s",
            trace_id,
            conversation_id,
            rewrite.mode,
            len(history),
            len(query),
            len(retrieval_query),
            rewrite.latency_ms,
            rewrite.fallback_reason,
        )
        logger.info(
            "RAG path scope trace_id=%s conversation_id=%s path_scope_mode=%s "
            "scoped_path_length=%s",
            trace_id,
            conversation_id,
            scoped_query.path_scope_mode,
            len(scoped_query.explicit_relative_path or ""),
        )
        retrieval_scope = PreparedRetrievalQuery(
            original_query=query,
            semantic_query=retrieval_query,
            scoped_document_id=scoped_query.scoped_document_id,
            path_scope_mode=scoped_query.path_scope_mode,
            explicit_relative_path=scoped_query.explicit_relative_path,
        )
        candidates = await self.indexing_service.hybrid_search(
            knowledge_base_id,
            query=retrieval_query,
            limit=self.settings.rag_rerank_candidate_limit,
            language=language,
            document_id=document_id,
            prepared_query=retrieval_scope,
        )
        results = candidates[: self.settings.rag_retrieval_limit]
        retrieval_mode = "hybrid"
        rerank_latency_ms = 0
        reranker_fallback = False
        fallback_reason: str | None = None
        if candidates and self.settings.reranker_enabled:
            rerank_started_at = perf_counter()
            try:
                if self.reranking_service is None:
                    raise RerankerUnavailableError(reason="unavailable")
                results = await self.reranking_service.rerank(
                    retrieval_query,
                    candidates,
                    limit=min(self.settings.rag_retrieval_limit, len(candidates)),
                )
                retrieval_mode = "hybrid_reranker"
            except RerankerUnavailableError as exc:
                results = candidates[: self.settings.rag_retrieval_limit]
                retrieval_mode = "hybrid_fallback"
                reranker_fallback = True
                fallback_reason = exc.reason
            except RerankerError:
                results = candidates[: self.settings.rag_retrieval_limit]
                retrieval_mode = "hybrid_fallback"
                reranker_fallback = True
                fallback_reason = "internal_error"
            rerank_latency_ms = round((perf_counter() - rerank_started_at) * 1_000)
        retrieval_latency_ms = round((perf_counter() - started_at) * 1_000)
        context = build_rag_context(results, self.settings.rag_max_context_chars)
        logger.info(
            "RAG retrieval trace_id=%s knowledge_base_id=%s retrieval_mode=%s query_length=%s "
            "candidate_count=%s final_count=%s retrieval_latency_ms=%s rerank_latency_ms=%s "
            "reranker_fallback=%s fallback_reason=%s",
            trace_id,
            knowledge_base_id,
            retrieval_mode,
            len(query),
            len(candidates),
            len(context.sources),
            retrieval_latency_ms,
            rerank_latency_ms,
            reranker_fallback,
            fallback_reason,
        )
        answer_history = history if rewrite.mode in {"rewritten", "fallback"} else ()
        messages = (
            build_rag_messages(
                query,
                context,
                answer_history,
                scoped_relative_path=scoped_query.explicit_relative_path,
            )
            if context.sources
            else None
        )
        return PreparedRag(
            trace_id,
            knowledge_base_id,
            query,
            retrieval_query,
            history,
            context,
            messages,
            retrieval_latency_ms,
            retrieval_mode,
            rerank_latency_ms,
            reranker_fallback,
            rewrite.mode,
            rewrite.latency_ms,
            rewrite.fallback_reason,
            scoped_query.path_scope_mode,
            scoped_query.explicit_relative_path,
            started_at,
        )

    async def stream_answer(
        self, prepared: PreparedRag
    ) -> AsyncGenerator[tuple[str, dict[str, object]]]:
        trace_id = str(prepared.trace_id)
        sources = [source.model_dump(mode="json") for source in prepared.context.sources]
        yield (
            "retrieval",
            {
                "trace_id": trace_id,
                "source_count": len(sources),
                "sources": sources,
            },
        )
        if prepared.messages is None:
            yield "no_answer", {"trace_id": trace_id, "message": NO_ANSWER_MESSAGE}
            yield "done", self._done(prepared, "no_answer", False, 0, 0, 0, 0)
            return

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
                            llm_first_token_latency_ms = max(
                                1, round((perf_counter() - llm_started_at) * 1_000)
                            )
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
                    llm_first_token_latency_ms = max(
                        1, round((perf_counter() - llm_started_at) * 1_000)
                    )
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
            yield (
                "error",
                {
                    "trace_id": trace_id,
                    "code": "llm_unavailable",
                    "message": LLM_ERROR_MESSAGE,
                    "llm_first_token_latency_ms": llm_first_token_latency_ms,
                },
            )
            return

        llm_latency_ms = max(
            llm_first_token_latency_ms,
            round((perf_counter() - llm_started_at) * 1_000),
        )
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
        logger.info(
            "RAG completed trace_id=%s knowledge_base_id=%s llm_model=%s "
            "finish_reason=%s grounded=%s valid_citation_count=%s "
            "invalid_citation_count=%s llm_latency_ms=%s total_latency_ms=%s",
            prepared.trace_id,
            prepared.knowledge_base_id,
            self.settings.llm_model,
            finish_reason,
            guard.grounded,
            guard.valid_citation_count,
            guard.invalid_citation_count,
            llm_latency_ms,
            round((perf_counter() - prepared.started_at) * 1_000),
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
            "trace_id": str(prepared.trace_id),
            "finish_reason": finish_reason,
            "grounded": grounded,
            "valid_citation_count": valid_count,
            "invalid_citation_count": invalid_count,
            "retrieval_latency_ms": prepared.retrieval_latency_ms,
            "retrieval_mode": prepared.retrieval_mode,
            "rerank_latency_ms": prepared.rerank_latency_ms,
            "reranker_fallback": prepared.reranker_fallback,
            "query_rewrite_mode": prepared.query_rewrite_mode,
            "query_rewrite_latency_ms": prepared.query_rewrite_latency_ms,
            "history_turn_count": len(prepared.conversation_history),
            "retrieval_query": prepared.retrieval_query,
            "path_scope_mode": prepared.path_scope_mode,
            "scoped_relative_path": prepared.scoped_relative_path,
            "source_count": len(prepared.context.sources),
            "llm_first_token_latency_ms": llm_first_token_latency_ms,
            "llm_latency_ms": llm_latency_ms,
            "total_latency_ms": round((perf_counter() - prepared.started_at) * 1_000),
        }
