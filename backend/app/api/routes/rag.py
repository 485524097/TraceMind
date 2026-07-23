import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import aclosing
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.api.routes.indexing import (
    IndexingServiceDependency,
)
from app.llm import LLMProvider
from app.schemas.rag import RagStreamRequest
from app.services.exceptions import SemanticSearchUnavailableError
from app.services.rag import PreparedRag, RagService

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
    )


RagServiceDependency = Annotated[RagService, Depends(get_rag_service)]


async def prepare_rag_stream(
    knowledge_base_id: UUID,
    body: RagStreamRequest,
    service: RagServiceDependency,
) -> tuple[RagService, PreparedRag]:
    try:
        prepared = await service.prepare(
            knowledge_base_id,
            query=body.query,
            language=body.language,
            document_id=body.document_id,
        )
    except SemanticSearchUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Semantic search is unavailable") from exc
    return service, prepared


PreparedStreamDependency = Annotated[tuple[RagService, PreparedRag], Depends(prepare_rag_stream)]


@router.post("/stream", response_class=EventSourceResponse)
async def stream_rag_answer(
    request: Request,
    prepared_stream: PreparedStreamDependency,
) -> AsyncGenerator[ServerSentEvent]:
    service, prepared = prepared_stream
    stream = service.stream_answer(prepared)
    try:
        async with aclosing(stream):
            async for event, data in stream:
                if await request.is_disconnected():
                    logger.info(
                        "RAG disconnected trace_id=%s knowledge_base_id=%s disconnected=true",
                        prepared.trace_id,
                        prepared.knowledge_base_id,
                    )
                    break
                yield ServerSentEvent(event=event, data=data)
    except asyncio.CancelledError:
        raise
