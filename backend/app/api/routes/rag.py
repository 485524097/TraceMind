import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes.conversations import ConversationServiceDependency
from app.api.routes.indexing import (
    IndexingServiceDependency,
)
from app.llm import LLMProvider
from app.schemas.rag import RagStreamRequest
from app.services.conversation import ConversationExchange, ConversationService
from app.services.document_reranking import DocumentRerankingService
from app.services.exceptions import ConversationNotFoundError
from app.services.rag import (
    PreparedRag,
    RagRetrievalUnavailableError,
    RagService,
    build_rag_scope_metadata,
)

router = APIRouter(prefix="/knowledge-bases/{knowledge_base_id}/rag", tags=["rag"])
logger = logging.getLogger(__name__)


def get_rag_service(
    request: Request,
    indexing_service: IndexingServiceDependency,
) -> RagService:
    provider = request.app.state.llm_provider
    if provider is None:
        raise HTTPException(status_code=503, detail="RAG answer generation is not configured")
    return RagService(
        indexing_service,
        cast(LLMProvider, provider),
        request.app.state.settings,
        (
            DocumentRerankingService(request.app.state.reranker_provider)
            if request.app.state.reranker_provider is not None
            else None
        ),
    )


RagServiceDependency = Annotated[RagService, Depends(get_rag_service)]


@dataclass(frozen=True)
class PreparedRagStream:
    rag_service: RagService
    prepared: PreparedRag
    conversation_service: ConversationService | None = None
    exchange: ConversationExchange | None = None


async def prepare_rag_stream(
    knowledge_base_id: UUID,
    body: RagStreamRequest,
    service: RagServiceDependency,
    conversation_service: ConversationServiceDependency,
) -> PreparedRagStream:
    exchange: ConversationExchange | None = None
    trace_id = uuid4() if body.conversation_id is not None else None
    if body.conversation_id is not None and trace_id is not None:
        try:
            exchange = await conversation_service.begin_exchange(
                knowledge_base_id,
                body.conversation_id,
                query=body.query,
                trace_id=trace_id,
                history_max_turns=service.settings.query_rewrite_history_max_turns,
                history_max_chars=service.settings.query_rewrite_history_max_chars,
            )
        except ConversationNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Conversation not found") from exc
        except SQLAlchemyError as exc:
            logger.exception("Conversation message could not be persisted")
            raise HTTPException(
                status_code=500, detail="The conversation operation could not be completed"
            ) from exc
    try:
        if exchange is not None and trace_id is not None:
            prepared = await service.prepare(
                knowledge_base_id,
                query=body.query,
                language=body.language,
                document_id=body.document_id,
                trace_id=trace_id,
                conversation_id=exchange.conversation_id,
                conversation_history=exchange.history,
            )
        else:
            prepared = await service.prepare(
                knowledge_base_id,
                query=body.query,
                language=body.language,
                document_id=body.document_id,
            )
    except RagRetrievalUnavailableError as exc:
        if exchange is not None:
            await conversation_service.finish_exchange(
                exchange,
                status="failed",
                content="回答生成服务暂时不可用，请稍后重试。",
                sources=None,
                generation_metadata={
                    "error_code": "retrieval_unavailable",
                    **exc.scope_metadata,
                },
            )
        raise HTTPException(status_code=503, detail="Hybrid search is unavailable") from exc
    return PreparedRagStream(
        service,
        prepared,
        conversation_service if exchange is not None else None,
        exchange,
    )


PreparedStreamDependency = Annotated[PreparedRagStream, Depends(prepare_rag_stream)]


@router.post("/stream", response_class=EventSourceResponse)
async def stream_rag_answer(
    request: Request,
    prepared_stream: PreparedStreamDependency,
) -> AsyncGenerator[ServerSentEvent]:
    service = prepared_stream.rag_service
    prepared = prepared_stream.prepared
    conversation_service = prepared_stream.conversation_service
    exchange = prepared_stream.exchange
    stream = service.stream_answer(prepared)
    answer_parts: list[str] = []
    sources: list[dict[str, Any]] | None = None
    no_answer_content: str | None = None
    llm_first_token_latency_ms = 0
    terminal_saved = exchange is None

    def execution_metadata() -> dict[str, object]:
        return {
            **build_rag_scope_metadata(prepared),
            "query_rewrite_mode": prepared.query_rewrite_mode,
            "query_rewrite_latency_ms": prepared.query_rewrite_latency_ms,
            "history_turn_count": len(prepared.conversation_history),
            "retrieval_query": prepared.retrieval_query,
            "llm_first_token_latency_ms": llm_first_token_latency_ms,
        }

    async def finish(
        status: str,
        content: str,
        metadata: dict[str, Any] | None,
    ) -> None:
        nonlocal terminal_saved
        if terminal_saved or conversation_service is None or exchange is None:
            return
        await conversation_service.finish_exchange(
            exchange,
            status=status,
            content=content,
            sources=sources,
            generation_metadata=metadata,
        )
        terminal_saved = True

    try:
        async with aclosing(stream):
            async for event, data in stream:
                if event == "retrieval":
                    raw_sources = data.get("sources")
                    if isinstance(raw_sources, list):
                        sources = [dict(item) for item in raw_sources if isinstance(item, dict)]
                elif event == "token":
                    text = data.get("text")
                    if isinstance(text, str):
                        answer_parts.append(text)
                    raw_first_token_latency = data.get("llm_first_token_latency_ms")
                    if isinstance(raw_first_token_latency, int):
                        llm_first_token_latency_ms = raw_first_token_latency
                elif event == "no_answer":
                    message = data.get("message")
                    if isinstance(message, str):
                        no_answer_content = message
                elif event == "error":
                    raw_first_token_latency = data.get("llm_first_token_latency_ms")
                    if isinstance(raw_first_token_latency, int):
                        llm_first_token_latency_ms = raw_first_token_latency
                    await finish(
                        "failed",
                        str(data.get("message") or "回答生成服务暂时不可用，请稍后重试。"),
                        {
                            "error_code": str(data.get("code") or "generation_failed"),
                            **execution_metadata(),
                        },
                    )
                elif event == "done":
                    status = (
                        "no_answer" if data.get("finish_reason") == "no_answer" else "completed"
                    )
                    content = no_answer_content or "".join(answer_parts)
                    await finish(status, content, {**execution_metadata(), **dict(data)})
                if await request.is_disconnected():
                    await finish(
                        "no_answer" if no_answer_content is not None else "cancelled",
                        no_answer_content or "".join(answer_parts),
                        {"cancelled": no_answer_content is None, **execution_metadata()},
                    )
                    logger.info(
                        "RAG disconnected trace_id=%s knowledge_base_id=%s disconnected=true",
                        prepared.trace_id,
                        prepared.knowledge_base_id,
                    )
                    break
                if exchange is not None:
                    data = {
                        **data,
                        "conversation_id": str(exchange.conversation_id),
                        "message_id": str(exchange.assistant_message_id),
                    }
                yield ServerSentEvent(event=event, data=data)
    except asyncio.CancelledError:
        try:
            await finish(
                "no_answer" if no_answer_content is not None else "cancelled",
                no_answer_content or "".join(answer_parts),
                {"cancelled": no_answer_content is None, **execution_metadata()},
            )
        except SQLAlchemyError:
            logger.exception("Cancelled conversation response could not be persisted")
        raise
    except Exception:
        try:
            await finish(
                "failed",
                "回答生成服务暂时不可用，请稍后重试。",
                {"error_code": "generation_failed", **execution_metadata()},
            )
        except SQLAlchemyError:
            logger.exception("Failed conversation response could not be persisted")
        raise
    finally:
        if not terminal_saved:
            try:
                await finish(
                    "no_answer" if no_answer_content is not None else "cancelled",
                    no_answer_content or "".join(answer_parts),
                    {"cancelled": no_answer_content is None, **execution_metadata()},
                )
            except SQLAlchemyError:
                logger.exception("Conversation response finalization failed")
