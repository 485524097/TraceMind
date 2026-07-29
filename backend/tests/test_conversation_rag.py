import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.api.routes.rag import PreparedRagStream, stream_rag_answer
from app.services.conversation import ConversationExchange, ConversationService


@dataclass
class FakePrepared:
    trace_id: UUID
    knowledge_base_id: UUID


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
    source = {"source_id": "S1", "content": "生成时正文", "document_name": "doc.md"}
    done = {
        "trace_id": "trace",
        "finish_reason": "stop",
        "retrieval_mode": retrieval_mode,
        "reranker_fallback": fallback,
        "grounded": True,
    }
    stream, persistence, exchange = prepared_stream(
        [
            ("retrieval", {"trace_id": "trace", "sources": [source]}),
            ("token", {"trace_id": "trace", "text": "安全"}),
            ("token", {"trace_id": "trace", "text": "回答 [S1]"}),
            ("done", done),
        ]
    )
    await consume(stream)

    persistence.finish_exchange.assert_awaited_once_with(
        exchange,
        status="completed",
        content="安全回答 [S1]",
        sources=[source],
        generation_metadata=done,
    )
    source["content"] = "后来改变"
    assert persistence.finish_exchange.await_args.kwargs["sources"][0]["content"] == "生成时正文"


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
        generation_metadata={"trace_id": "trace", "finish_reason": "no_answer"},
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
        "generation_metadata": {"error_code": "llm_unavailable"},
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
        generation_metadata={"cancelled": True},
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
        generation_metadata={"cancelled": True},
    )
