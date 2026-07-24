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
from app.services.document_indexing import DocumentIndexingService

logger = logging.getLogger(__name__)
NO_ANSWER_MESSAGE = "知识库中未找到足够相关的信息。"
LLM_ERROR_MESSAGE = "回答生成服务暂时不可用，请稍后重试。"


@dataclass(frozen=True)
class PreparedRag:
    trace_id: UUID
    knowledge_base_id: UUID
    context: RagContext
    messages: list[LLMMessage] | None
    retrieval_latency_ms: int
    started_at: float


class RagService:
    def __init__(
        self,
        indexing_service: DocumentIndexingService,
        provider: LLMProvider,
        settings: Settings,
    ) -> None:
        self.indexing_service = indexing_service
        self.provider = provider
        self.settings = settings

    async def prepare(
        self,
        knowledge_base_id: UUID,
        *,
        query: str,
        language: str | None,
        document_id: UUID | None,
    ) -> PreparedRag:
        started_at = perf_counter()
        trace_id = uuid4()
        results = await self.indexing_service.search(
            knowledge_base_id,
            query=query,
            limit=self.settings.rag_retrieval_limit,
            language=language,
            document_id=document_id,
        )
        retrieval_latency_ms = round((perf_counter() - started_at) * 1_000)
        context = build_rag_context(results, self.settings.rag_max_context_chars)
        logger.info(
            "RAG retrieval trace_id=%s knowledge_base_id=%s query_length=%s "
            "retrieval_count=%s retrieval_latency_ms=%s",
            trace_id,
            knowledge_base_id,
            len(query),
            len(context.sources),
            retrieval_latency_ms,
        )
        messages = build_rag_messages(query, context) if context.sources else None
        return PreparedRag(
            trace_id,
            knowledge_base_id,
            context,
            messages,
            retrieval_latency_ms,
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
            yield "done", self._done(prepared, "no_answer", False, 0, 0, 0)
            return

        guard = StreamingCitationGuard({source.source_id for source in prepared.context.sources})
        llm_started_at = perf_counter()
        finish_reason = "stop"
        try:
            stream = await self.provider.stream(prepared.messages)
            async with aclosing(stream):
                async for delta in stream:
                    finish_reason = delta.finish_reason or finish_reason
                    safe_text = guard.push(delta.text)
                    if safe_text:
                        yield "token", {"trace_id": trace_id, "text": safe_text}
            tail = guard.finish()
            if tail:
                yield "token", {"trace_id": trace_id, "text": tail}
        except asyncio.CancelledError:
            raise
        except LLMProviderError:
            yield (
                "error",
                {
                    "trace_id": trace_id,
                    "code": "llm_unavailable",
                    "message": LLM_ERROR_MESSAGE,
                },
            )
            return

        llm_latency_ms = round((perf_counter() - llm_started_at) * 1_000)
        yield (
            "done",
            self._done(
                prepared,
                finish_reason,
                guard.grounded,
                guard.valid_citation_count,
                guard.invalid_citation_count,
                llm_latency_ms,
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
    ) -> dict[str, object]:
        return {
            "trace_id": str(prepared.trace_id),
            "finish_reason": finish_reason,
            "grounded": grounded,
            "valid_citation_count": valid_count,
            "invalid_citation_count": invalid_count,
            "retrieval_latency_ms": prepared.retrieval_latency_ms,
            "llm_latency_ms": llm_latency_ms,
            "total_latency_ms": round((perf_counter() - prepared.started_at) * 1_000),
        }
