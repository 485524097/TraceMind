import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.api.routes.rag import PreparedRagStream, prepare_rag_stream, stream_rag_answer
from app.core.config import Settings
from app.schemas.conversation import ConversationMessageResponse
from app.schemas.rag import RagStreamRequest
from app.services.conversation import (
    ConversationExchange,
    ConversationService,
    ConversationTurn,
)
from app.services.rag import RagRetrievalUnavailableError, RagService

SAFE_SCOPE_METADATA = {
    "symbol_scope_mode": "none",
    "symbol_scope_reason": None,
    "scoped_symbol_kind": None,
    "scoped_symbol_qualified_name": None,
    "scoped_symbol_signature": None,
}


@dataclass
class FakePrepared:
    trace_id: UUID
    knowledge_base_id: UUID
    query_rewrite_mode: str = "rewritten"
    query_rewrite_latency_ms: int = 7
    conversation_history: tuple[ConversationTurn, ...] = (ConversationTurn("历史问题", "历史回答"),)
    retrieval_query: str = "独立检索问题"
    path_scope_mode: str = "none"
    scoped_relative_path: str | None = None
    symbol_scope_mode: str = "none"
    symbol_scope_reason: str | None = None
    scoped_symbol_kind: str | None = None
    scoped_symbol_qualified_name: str | None = None
    scoped_symbol_signature: str | None = None


@dataclass
class FakeRagService:
    events: list[tuple[str, dict[str, object]]]

    async def stream_answer(
        self, prepared: object
    ) -> AsyncGenerator[tuple[str, dict[str, object]]]:
        for item in self.events:
            yield item


class DisconnectRequest:
    def __init__(self, disconnect_on_call: int | None = None) -> None:
        self.calls = 0
        self.disconnect_on_call = disconnect_on_call

    async def is_disconnected(self) -> bool:
        self.calls += 1
        return self.disconnect_on_call == self.calls


@dataclass
class BlockingRagService:
    async def stream_answer(
        self, prepared: object
    ) -> AsyncGenerator[tuple[str, dict[str, object]]]:
        yield "retrieval", {"trace_id": "trace", "sources": []}
        await asyncio.Event().wait()
        yield "token", {"trace_id": "trace", "text": "unreachable"}


def prepared_stream(
    events: list[tuple[str, dict[str, object]]],
) -> tuple[PreparedRagStream, AsyncMock, ConversationExchange]:
    knowledge_base_id = uuid4()
    trace_id = uuid4()
    exchange = ConversationExchange(
        knowledge_base_id,
        uuid4(),
        uuid4(),
        uuid4(),
        trace_id,
    )
    persistence = AsyncMock(spec=ConversationService)
    stream = PreparedRagStream(
        FakeRagService(events),  # type: ignore[arg-type]
        FakePrepared(trace_id, knowledge_base_id),  # type: ignore[arg-type]
        persistence,
        exchange,
    )
    return stream, persistence, exchange


async def consume(stream: PreparedRagStream, request: DisconnectRequest | None = None) -> None:
    async for _ in stream_rag_answer(request or DisconnectRequest(), stream):  # type: ignore[arg-type]
        pass


@pytest.mark.parametrize(
    ("retrieval_mode", "fallback"),
    [("hybrid_reranker", False), ("hybrid_fallback", True)],
)
async def test_completed_answer_persists_guarded_content_sources_and_metadata(
    retrieval_mode: str, fallback: bool
) -> None:
    source = {
        "source_id": "S1",
        "content": "生成时正文",
        "document_name": "doc.md",
        "symbol_kind": "method",
        "symbol_name": "run",
        "symbol_qualified_name": "demo.Sample.run",
        "symbol_signature": "void run()",
        "ranking_mode": "symbol_exact",
    }
    done = {
        "trace_id": "trace",
        "finish_reason": "stop",
        "retrieval_mode": retrieval_mode,
        "reranker_fallback": fallback,
        "grounded": True,
        "query_rewrite_mode": "rewritten",
        "query_rewrite_latency_ms": 7,
        "history_turn_count": 1,
        "retrieval_query": "独立检索问题",
        "path_scope_mode": "none",
        "scoped_relative_path": None,
    }
    stream, persistence, exchange = prepared_stream(
        [
            ("retrieval", {"trace_id": "trace", "sources": [source]}),
            ("token", {"trace_id": "trace", "text": "安全"}),
            ("token", {"trace_id": "trace", "text": "回答 [S1]"}),
            ("done", done),
        ]
    )
    exact_scope = {
        "symbol_scope_mode": "exact",
        "symbol_scope_reason": None,
        "scoped_symbol_kind": "method",
        "scoped_symbol_qualified_name": "demo.Sample.run",
        "scoped_symbol_signature": "void run()",
    }
    for key, value in exact_scope.items():
        setattr(stream.prepared, key, value)
    await consume(stream)

    persistence.finish_exchange.assert_awaited_once_with(
        exchange,
        status="completed",
        content="安全回答 [S1]",
        sources=[source],
        generation_metadata={
            **exact_scope,
            "llm_first_token_latency_ms": 0,
            **done,
        },
    )
    source["content"] = "后来改变"
    assert persistence.finish_exchange.await_args.kwargs["sources"][0]["content"] == "生成时正文"
    assert persistence.finish_exchange.await_args.kwargs["sources"][0]["symbol_signature"] == (
        "void run()"
    )
    assert persistence.finish_exchange.await_args.kwargs["sources"][0]["ranking_mode"] == (
        "symbol_exact"
    )
    assert "lookup" not in str(persistence.finish_exchange.await_args.kwargs)


async def test_no_answer_is_persisted_as_terminal_message() -> None:
    stream, persistence, exchange = prepared_stream(
        [
            ("retrieval", {"trace_id": "trace", "sources": []}),
            ("no_answer", {"trace_id": "trace", "message": "没有足够信息"}),
            ("done", {"trace_id": "trace", "finish_reason": "no_answer"}),
        ]
    )
    await consume(stream)
    persistence.finish_exchange.assert_awaited_once_with(
        exchange,
        status="no_answer",
        content="没有足够信息",
        sources=[],
        generation_metadata={
            "query_rewrite_mode": "rewritten",
            "query_rewrite_latency_ms": 7,
            "history_turn_count": 1,
            "retrieval_query": "独立检索问题",
            "path_scope_mode": "none",
            "scoped_relative_path": None,
            **SAFE_SCOPE_METADATA,
            "llm_first_token_latency_ms": 0,
            "trace_id": "trace",
            "finish_reason": "no_answer",
        },
    )


async def test_llm_error_persists_only_safe_public_error() -> None:
    stream, persistence, exchange = prepared_stream(
        [
            ("retrieval", {"trace_id": "trace", "sources": []}),
            (
                "error",
                {
                    "trace_id": "trace",
                    "code": "llm_unavailable",
                    "message": "回答生成服务暂时不可用，请稍后重试。",
                },
            ),
        ]
    )
    await consume(stream)
    kwargs = persistence.finish_exchange.await_args.kwargs
    assert kwargs == {
        "status": "failed",
        "content": "回答生成服务暂时不可用，请稍后重试。",
        "sources": [],
        "generation_metadata": {
            "error_code": "llm_unavailable",
            "llm_first_token_latency_ms": 0,
            "query_rewrite_mode": "rewritten",
            "query_rewrite_latency_ms": 7,
            "history_turn_count": 1,
            "retrieval_query": "独立检索问题",
            "path_scope_mode": "none",
            "scoped_relative_path": None,
            **SAFE_SCOPE_METADATA,
        },
    }
    assert "upstream" not in str(kwargs).lower()
    assert persistence.finish_exchange.await_args.args == (exchange,)


async def test_disconnect_persists_cancelled_without_pending_message() -> None:
    stream, persistence, exchange = prepared_stream(
        [
            ("retrieval", {"trace_id": "trace", "sources": []}),
            ("token", {"trace_id": "trace", "text": "部分回答"}),
            ("token", {"trace_id": "trace", "text": "不应到达"}),
        ]
    )
    await consume(stream, DisconnectRequest(disconnect_on_call=2))
    persistence.finish_exchange.assert_awaited_once_with(
        exchange,
        status="cancelled",
        content="部分回答",
        sources=[],
        generation_metadata={
            "cancelled": True,
            "query_rewrite_mode": "rewritten",
            "query_rewrite_latency_ms": 7,
            "history_turn_count": 1,
            "retrieval_query": "独立检索问题",
            "path_scope_mode": "none",
            "scoped_relative_path": None,
            **SAFE_SCOPE_METADATA,
            "llm_first_token_latency_ms": 0,
        },
    )


async def test_task_cancellation_persists_cancelled_status() -> None:
    knowledge_base_id = uuid4()
    trace_id = uuid4()
    exchange = ConversationExchange(
        knowledge_base_id,
        uuid4(),
        uuid4(),
        uuid4(),
        trace_id,
    )
    persistence = AsyncMock(spec=ConversationService)
    stream = PreparedRagStream(
        BlockingRagService(),  # type: ignore[arg-type]
        FakePrepared(trace_id, knowledge_base_id),  # type: ignore[arg-type]
        persistence,
        exchange,
    )
    response = stream_rag_answer(DisconnectRequest(), stream)  # type: ignore[arg-type]
    await anext(response)
    task = asyncio.create_task(anext(response))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    persistence.finish_exchange.assert_awaited_once_with(
        exchange,
        status="cancelled",
        content="",
        sources=[],
        generation_metadata={
            "cancelled": True,
            "query_rewrite_mode": "rewritten",
            "query_rewrite_latency_ms": 7,
            "history_turn_count": 1,
            "retrieval_query": "独立检索问题",
            "path_scope_mode": "none",
            "scoped_relative_path": None,
            **SAFE_SCOPE_METADATA,
            "llm_first_token_latency_ms": 0,
        },
    )


async def test_retrieval_error_persists_failed_with_safe_scope_metadata() -> None:
    knowledge_base_id, conversation_id, trace_id = uuid4(), uuid4(), uuid4()
    exchange = ConversationExchange(
        knowledge_base_id,
        conversation_id,
        uuid4(),
        uuid4(),
        trace_id,
    )
    scope = {
        "path_scope_mode": "exact",
        "scoped_relative_path": "src/UserService.java",
        "symbol_scope_mode": "exact",
        "symbol_scope_reason": None,
        "scoped_symbol_kind": "method",
        "scoped_symbol_qualified_name": "demo.UserService.source",
        "scoped_symbol_signature": "String source(String username)",
    }
    service = AsyncMock(spec=RagService)
    service.settings = Settings(_env_file=None)
    service.prepare.side_effect = RagRetrievalUnavailableError(scope)
    conversation = AsyncMock(spec=ConversationService)
    conversation.begin_exchange.return_value = exchange

    with pytest.raises(HTTPException) as caught:
        await prepare_rag_stream(
            knowledge_base_id,
            RagStreamRequest(query="UserService#source", conversation_id=conversation_id),
            service,
            conversation,
        )

    assert caught.value.status_code == 503
    conversation.finish_exchange.assert_awaited_once_with(
        exchange,
        status="failed",
        content="回答生成服务暂时不可用，请稍后重试。",
        sources=None,
        generation_metadata={"error_code": "retrieval_unavailable", **scope},
    )
    assert "lookup" not in str(conversation.finish_exchange.await_args.kwargs)


def test_legacy_generation_metadata_defaults_symbol_scope_to_none() -> None:
    response = ConversationMessageResponse.model_validate(
        {
            "id": uuid4(),
            "conversation_id": uuid4(),
            "role": "assistant",
            "status": "completed",
            "content": "legacy",
            "trace_id": uuid4(),
            "sources": None,
            "generation_metadata": {"retrieval_mode": "hybrid"},
            "created_at": datetime.now(UTC),
        }
    )

    assert response.generation_metadata is not None
    assert response.generation_metadata["symbol_scope_mode"] == "none"
    assert response.generation_metadata["scoped_symbol_qualified_name"] is None
