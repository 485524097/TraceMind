import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.sse import ServerSentEvent

from app.api.routes.rag import PreparedRagStream, RagGraph, stream_rag_answer
from app.rag.graph import RagRuntimeContext, RagState
from app.schemas.rag import RagStreamRequest
from app.services.conversation import (
    ConversationExchange,
    ConversationService,
    ConversationTurn,
)
from app.services.exceptions import HybridSearchUnavailableError


@dataclass
class FakeGraph:
    events: list[dict[str, object]]
    error: Exception | None = None
    calls: list[tuple[RagState, RagRuntimeContext, str]] = field(default_factory=list)

    async def astream(
        self,
        graph_input: RagState,
        *,
        context: RagRuntimeContext,
        stream_mode: str,
    ) -> AsyncIterator[dict[str, object]]:
        self.calls.append((graph_input, context, stream_mode))
        if self.error is not None:
            raise self.error
        for event in self.events:
            yield event


@dataclass
class BlockingGraph:
    async def astream(
        self,
        graph_input: RagState,
        *,
        context: RagRuntimeContext,
        stream_mode: str,
    ) -> AsyncIterator[dict[str, object]]:
        yield {"type": "sources", "source_count": 0, "sources": []}
        await asyncio.Event().wait()
        yield {"type": "token", "text": "unreachable"}


class DisconnectRequest:
    def __init__(self, disconnect_on_call: int | None = None) -> None:
        self.calls = 0
        self.disconnect_on_call = disconnect_on_call

    async def is_disconnected(self) -> bool:
        self.calls += 1
        return self.disconnect_on_call == self.calls


def prepared_stream(
    events: list[dict[str, object]],
    *,
    error: Exception | None = None,
) -> tuple[PreparedRagStream, AsyncMock, ConversationExchange, FakeGraph]:
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
    graph = FakeGraph(events, error)
    stream = PreparedRagStream(
        graph=cast(RagGraph, graph),
        runtime_context=cast(RagRuntimeContext, object()),
        knowledge_base_id=knowledge_base_id,
        body=RagStreamRequest(query="问题", conversation_id=exchange.conversation_id),
        trace_id=trace_id,
        conversation_history=(ConversationTurn("历史问题", "历史回答"),),
        conversation_service=persistence,
        exchange=exchange,
    )
    return stream, persistence, exchange, graph


async def consume(
    stream: PreparedRagStream,
    request: DisconnectRequest | None = None,
) -> list[ServerSentEvent]:
    return [
        event
        async for event in stream_rag_answer(
            request or DisconnectRequest(),  # type: ignore[arg-type]
            stream,
        )
    ]


def metadata_without_latency(metadata: dict[str, Any]) -> dict[str, Any]:
    result = dict(metadata)
    assert isinstance(result.pop("response_total_latency_ms"), int)
    return result


async def test_completed_answer_uses_custom_graph_and_persists_sources_and_tokens() -> None:
    source = {
        "source_id": "S1",
        "content": "生成时正文",
        "document_name": "doc.md",
    }
    done = {
        "type": "done",
        "terminal_status": "completed",
        "route_mode": "rag",
        "retrieval_mode": "hybrid_reranker",
        "reranker_fallback": False,
        "grounded": True,
        "valid_citation_count": 1,
        "invalid_citation_count": 0,
        "query_rewrite_mode": "rewritten",
        "query_rewrite_latency_ms": 7,
        "path_scope_mode": "none",
        "scoped_relative_path": None,
    }
    stream, persistence, exchange, graph = prepared_stream(
        [
            {"type": "sources", "source_count": 1, "sources": [source]},
            {"type": "token", "text": "安全"},
            {"type": "token", "text": "回答 [S1]"},
            done,
        ]
    )

    events = await consume(stream)

    assert graph.calls == [
        (
            {
                "trace_id": stream.trace_id,
                "knowledge_base_id": stream.knowledge_base_id,
                "query": "问题",
                "language": None,
                "document_id": None,
                "conversation_history": (ConversationTurn("历史问题", "历史回答"),),
            },
            stream.runtime_context,
            "custom",
        )
    ]
    kwargs = persistence.finish_exchange.await_args.kwargs
    assert kwargs["status"] == "completed"
    assert kwargs["content"] == "安全回答 [S1]"
    assert kwargs["sources"] == [source]
    assert metadata_without_latency(kwargs["generation_metadata"]) == {
        "trace_id": str(stream.trace_id),
        "history_turn_count": 1,
        **{key: value for key, value in done.items() if key != "type"},
    }
    assert persistence.finish_exchange.await_args.args == (exchange,)
    source["content"] = "后来改变"
    assert kwargs["sources"][0]["content"] == "生成时正文"

    assert [event.event for event in events] == ["sources", "token", "token", "done"]
    assert all(event.data["trace_id"] == str(stream.trace_id) for event in events)
    assert all(event.data["conversation_id"] == str(exchange.conversation_id) for event in events)
    assert all(event.data["message_id"] == str(exchange.assistant_message_id) for event in events)
    assert events[-1].data["terminal_status"] == "completed"
    assert isinstance(events[-1].data["conversation_persistence_latency_ms"], int)


async def test_no_answer_is_persisted_as_terminal_message() -> None:
    stream, persistence, exchange, _ = prepared_stream(
        [
            {"type": "no_answer", "message": "没有足够信息"},
            {
                "type": "done",
                "terminal_status": "no_answer",
                "route_mode": "rag",
                "grounded": False,
                "valid_citation_count": 0,
                "invalid_citation_count": 0,
            },
        ]
    )

    events = await consume(stream)

    kwargs = persistence.finish_exchange.await_args.kwargs
    assert kwargs["status"] == "no_answer"
    assert kwargs["content"] == "没有足够信息"
    assert kwargs["sources"] is None
    assert metadata_without_latency(kwargs["generation_metadata"])["terminal_status"] == "no_answer"
    assert persistence.finish_exchange.await_args.args == (exchange,)
    assert [event.event for event in events] == ["no_answer", "done"]


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (HybridSearchUnavailableError("private retrieval detail"), "retrieval_unavailable"),
        (RuntimeError("private model detail"), "generation_failed"),
    ],
)
async def test_graph_error_emits_safe_event_and_persists_failed(
    error: Exception,
    code: str,
) -> None:
    stream, persistence, exchange, _ = prepared_stream([], error=error)

    events = await consume(stream)

    assert len(events) == 1
    assert events[0].event == "error"
    assert events[0].data == {
        "code": code,
        "message": "回答生成服务暂时不可用，请稍后重试。",
        "trace_id": str(stream.trace_id),
        "conversation_id": str(exchange.conversation_id),
        "message_id": str(exchange.assistant_message_id),
    }
    assert "private" not in str(events[0].data)
    kwargs = persistence.finish_exchange.await_args.kwargs
    assert kwargs["status"] == "failed"
    assert kwargs["content"] == "回答生成服务暂时不可用，请稍后重试。"
    assert metadata_without_latency(kwargs["generation_metadata"])["error_code"] == code


async def test_disconnect_stops_graph_and_persists_partial_answer() -> None:
    source = {"source_id": "S1", "content": "source"}
    stream, persistence, exchange, _ = prepared_stream(
        [
            {"type": "sources", "source_count": 1, "sources": [source]},
            {"type": "token", "text": "部分回答"},
            {"type": "token", "text": "不应到达"},
        ]
    )

    events = await consume(stream, DisconnectRequest(disconnect_on_call=2))

    assert [event.event for event in events] == ["sources"]
    kwargs = persistence.finish_exchange.await_args.kwargs
    assert kwargs["status"] == "cancelled"
    assert kwargs["content"] == "部分回答"
    assert kwargs["sources"] == [source]
    metadata = metadata_without_latency(kwargs["generation_metadata"])
    assert metadata["cancelled"] is True
    assert persistence.finish_exchange.await_args.args == (exchange,)


async def test_task_cancellation_shields_terminal_persistence_and_propagates() -> None:
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
        graph=cast(RagGraph, BlockingGraph()),
        runtime_context=cast(RagRuntimeContext, object()),
        knowledge_base_id=knowledge_base_id,
        body=RagStreamRequest(query="问题", conversation_id=exchange.conversation_id),
        trace_id=trace_id,
        conversation_service=persistence,
        exchange=exchange,
    )
    response = stream_rag_answer(DisconnectRequest(), stream)  # type: ignore[arg-type]
    first = await anext(response)
    assert first.event == "sources"
    task = asyncio.create_task(anext(response))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    kwargs = persistence.finish_exchange.await_args.kwargs
    assert kwargs["status"] == "cancelled"
    assert kwargs["content"] == ""
    assert kwargs["sources"] == []
    assert metadata_without_latency(kwargs["generation_metadata"])["cancelled"] is True
