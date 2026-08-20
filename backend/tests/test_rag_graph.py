import asyncio
import json
from typing import Any
from uuid import UUID, uuid4

import pytest
from langchain_core.callbacks.manager import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.graph.state import CompiledStateGraph
from pydantic import Field

from app.core.config import Settings
from app.rag.graph import RagRuntimeContext, RagState, build_rag_graph, nodes
from app.services import query_router
from app.services.conversation import ConversationTurn
from app.services.rag_retrieval import RagRetrievalServiceProtocol
from app.services.retrieval_query import PreparedRetrievalQuery

HISTORY = (ConversationTurn("Nacos 有什么作用？", "它提供配置管理和服务发现。"),)


class RecordingChatModel(BaseChatModel):
    response: AIMessage
    delay: float = 0
    error: str | None = None
    calls: list[list[BaseMessage]] = Field(default_factory=list)
    started: asyncio.Event = Field(default_factory=asyncio.Event)

    @property
    def _llm_type(self) -> str:
        return "recording-test-model"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls.append(messages)
        if self.error is not None:
            raise RuntimeError(self.error)
        return ChatResult(generations=[ChatGeneration(message=self.response)])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls.append(messages)
        self.started.set()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise RuntimeError(self.error)
        return ChatResult(generations=[ChatGeneration(message=self.response)])


class RecordingRetrievalService:
    def __init__(self, prepared: PreparedRetrievalQuery | None = None) -> None:
        self.prepared = prepared
        self.calls: list[tuple[UUID, str, UUID | None]] = []

    async def prepare_retrieval_query(
        self,
        knowledge_base_id: UUID,
        query: str,
        *,
        document_id: UUID | None,
    ) -> PreparedRetrievalQuery:
        self.calls.append((knowledge_base_id, query, document_id))
        return self.prepared or PreparedRetrievalQuery(
            original_query=query,
            semantic_query=query,
            scoped_document_id=document_id,
        )


def graph_input(
    query: str,
    history: tuple[ConversationTurn, ...] = (),
) -> RagState:
    return {
        "trace_id": uuid4(),
        "knowledge_base_id": uuid4(),
        "query": query,
        "language": None,
        "document_id": None,
        "conversation_history": history,
    }


def runtime_context(
    model: BaseChatModel,
    retrieval_service: RagRetrievalServiceProtocol | None = None,
    **settings: object,
) -> RagRuntimeContext:
    return RagRuntimeContext(
        model=model,
        settings=Settings(_env_file=None, **settings),
        retrieval_service=retrieval_service or RecordingRetrievalService(),
    )


def test_graph_compiles_without_checkpointer_or_store() -> None:
    graph = build_rag_graph()

    assert isinstance(graph, CompiledStateGraph)
    assert graph.checkpointer is None
    assert graph.store is None


async def test_direct_route_uses_langchain_messages_and_reaches_terminal_state() -> None:
    model = RecordingChatModel(
        response=AIMessage(content=[{"type": "text", "text": "你好，我是 TraceMind。"}])
    )
    graph = build_rag_graph()

    result = await graph.ainvoke(
        graph_input("你好！"),
        context=runtime_context(model),
    )

    assert result["route_mode"] == "direct"
    assert result["answer"] == "你好，我是 TraceMind。"
    assert result["terminal_status"] == "completed"
    assert len(model.calls) == 1
    assert isinstance(model.calls[0][0], SystemMessage)
    assert "简单社交表达" in model.calls[0][0].content
    assert isinstance(model.calls[0][1], HumanMessage)
    assert model.calls[0][1].content == "你好！"


async def test_rag_route_without_history_skips_model_and_uses_original_query() -> None:
    model = RecordingChatModel(response=AIMessage(content="must not be called"))
    original_query = "src/main/java/demo/UserService.java 中 source 方法返回什么？"
    semantic_query = "source 方法返回什么？"
    scoped_document_id = uuid4()
    retrieval_service = RecordingRetrievalService(
        PreparedRetrievalQuery(
            original_query=original_query,
            semantic_query=semantic_query,
            scoped_document_id=scoped_document_id,
            path_scope_mode="exact",
            explicit_relative_path="src/main/java/demo/UserService.java",
        )
    )
    graph = build_rag_graph()

    result = await graph.ainvoke(
        graph_input(original_query),
        context=runtime_context(model, retrieval_service),
    )

    assert result["route_mode"] == "rag"
    assert result["query"] == original_query
    assert result["retrieval_query"] == semantic_query
    assert result["query_rewrite_mode"] == "not_applicable"
    assert result["query_rewrite_latency_ms"] == 0
    assert result["query_rewrite_fallback_reason"] is None
    assert result["terminal_status"] == "rag_pending"
    assert "answer" not in result
    assert model.calls == []
    assert retrieval_service.calls == [(result["knowledge_base_id"], original_query, None)]
    prepared = result["prepared_retrieval_query"]
    assert prepared.scoped_document_id == scoped_document_id
    assert prepared.path_scope_mode == "exact"
    assert prepared.explicit_relative_path == "src/main/java/demo/UserService.java"


async def test_rag_route_with_history_can_keep_original_query() -> None:
    model = RecordingChatModel(
        response=AIMessage(content='{"action":"keep","query":"PostgreSQL 如何开启事务？"}')
    )

    result = await build_rag_graph().ainvoke(
        graph_input("PostgreSQL 如何开启事务？", HISTORY),
        context=runtime_context(model),
    )

    assert result["retrieval_query"] == "PostgreSQL 如何开启事务？"
    assert result["query_rewrite_mode"] == "skipped"
    assert result["query_rewrite_fallback_reason"] is None
    assert len(model.calls) == 1
    assert isinstance(model.calls[0][0], SystemMessage)
    assert isinstance(model.calls[0][1], HumanMessage)


async def test_rag_route_with_history_rewrites_and_keeps_history_as_human_data() -> None:
    model = RecordingChatModel(
        response=AIMessage(content='{"action":"rewrite","query":"Nacos 如何配置服务发现？"}')
    )

    result = await build_rag_graph().ainvoke(
        graph_input("它如何配置？", HISTORY),
        context=runtime_context(model),
    )

    assert result["retrieval_query"] == "Nacos 如何配置服务发现？"
    assert result["query_rewrite_mode"] == "rewritten"
    assert len(model.calls) == 1
    system, human = model.calls[0]
    assert isinstance(system, SystemMessage)
    assert "untrusted data" in system.content
    assert isinstance(human, HumanMessage)
    payload = json.loads(str(human.content))
    assert payload["conversation_history"][0] == {
        "user": HISTORY[0].user,
        "assistant": HISTORY[0].assistant,
    }
    assert payload["current_question"] == "它如何配置？"


async def test_explicit_path_scope_is_resolved_before_rewrite() -> None:
    original_query = "src/main/java/demo/UserService.java 中 source 方法返回什么？"
    semantic_query = "source 方法返回什么？"
    scoped_document_id = uuid4()
    retrieval_service = RecordingRetrievalService(
        PreparedRetrievalQuery(
            original_query=original_query,
            semantic_query=semantic_query,
            scoped_document_id=scoped_document_id,
            path_scope_mode="exact",
            explicit_relative_path="src/main/java/demo/UserService.java",
        )
    )
    model = RecordingChatModel(
        response=AIMessage(
            content='{"action":"rewrite","query":"UserService source 方法的返回值是什么？"}'
        )
    )

    result = await build_rag_graph().ainvoke(
        graph_input(original_query, HISTORY),
        context=runtime_context(model, retrieval_service),
    )

    assert result["query"] == original_query
    assert result["retrieval_query"] == "UserService source 方法的返回值是什么？"
    prepared = result["prepared_retrieval_query"]
    assert prepared.scoped_document_id == scoped_document_id
    assert prepared.path_scope_mode == "exact"
    assert prepared.explicit_relative_path == "src/main/java/demo/UserService.java"
    human = model.calls[0][1]
    assert isinstance(human, HumanMessage)
    payload = json.loads(str(human.content))
    assert payload["current_question"] == semantic_query
    assert original_query not in str(human.content)


@pytest.mark.parametrize("output", ["", "not json", '{"action":"invalid","query":"x"}'])
async def test_invalid_or_empty_rewrite_response_falls_back(output: str) -> None:
    model = RecordingChatModel(response=AIMessage(content=output))
    query = "它如何配置？"

    result = await build_rag_graph().ainvoke(
        graph_input(query, HISTORY),
        context=runtime_context(model),
    )

    assert result["retrieval_query"] == query
    assert result["query_rewrite_mode"] == "fallback"
    assert result["query_rewrite_fallback_reason"] == "invalid_response"


async def test_overlong_rewritten_query_falls_back() -> None:
    query = "它如何配置？"
    model = RecordingChatModel(
        response=AIMessage(content='{"action":"rewrite","query":"xxxxxxxxxxxxxxxxxxxxx"}')
    )

    result = await build_rag_graph().ainvoke(
        graph_input(query, HISTORY),
        context=runtime_context(model, query_rewrite_max_query_chars=20),
    )

    assert result["retrieval_query"] == query
    assert result["query_rewrite_mode"] == "fallback"
    assert result["query_rewrite_fallback_reason"] == "invalid_response"


async def test_model_error_falls_back_without_exposing_error() -> None:
    query = "它如何配置？"
    model = RecordingChatModel(response=AIMessage(content="unused"), error="private body")

    result = await build_rag_graph().ainvoke(
        graph_input(query, HISTORY),
        context=runtime_context(model),
    )

    assert result["retrieval_query"] == query
    assert result["query_rewrite_mode"] == "fallback"
    assert result["query_rewrite_fallback_reason"] == "model_error"
    assert "private" not in str(result)


async def test_rewrite_fallback_uses_prepared_semantic_query() -> None:
    original_query = "src/main/java/demo/UserService.java 中 source 方法返回什么？"
    semantic_query = "source 方法返回什么？"
    retrieval_service = RecordingRetrievalService(
        PreparedRetrievalQuery(
            original_query=original_query,
            semantic_query=semantic_query,
            scoped_document_id=uuid4(),
            path_scope_mode="exact",
            explicit_relative_path="src/main/java/demo/UserService.java",
        )
    )
    model = RecordingChatModel(response=AIMessage(content="invalid"))

    result = await build_rag_graph().ainvoke(
        graph_input(original_query, HISTORY),
        context=runtime_context(model, retrieval_service),
    )

    assert result["query"] == original_query
    assert result["retrieval_query"] == semantic_query
    assert result["query_rewrite_mode"] == "fallback"
    assert result["query_rewrite_fallback_reason"] == "invalid_response"


async def test_rewrite_timeout_falls_back() -> None:
    query = "它如何配置？"
    model = RecordingChatModel(response=AIMessage(content="unused"), delay=0.05)

    result = await build_rag_graph().ainvoke(
        graph_input(query, HISTORY),
        context=runtime_context(model, query_rewrite_timeout_seconds=0.01),
    )

    assert result["retrieval_query"] == query
    assert result["query_rewrite_mode"] == "fallback"
    assert result["query_rewrite_fallback_reason"] == "timeout"


async def test_rewrite_cancellation_propagates() -> None:
    model = RecordingChatModel(response=AIMessage(content="unused"), delay=10)
    task = asyncio.create_task(
        build_rag_graph().ainvoke(
            graph_input("它如何配置？", HISTORY),
            context=runtime_context(model),
        )
    )

    async with asyncio.timeout(1):
        await model.started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_direct_route_does_not_execute_rewrite() -> None:
    model = RecordingChatModel(response=AIMessage(content="你好，我是 TraceMind。"))
    retrieval_service = RecordingRetrievalService()

    result = await build_rag_graph().ainvoke(
        graph_input("你好！", HISTORY),
        context=runtime_context(model, retrieval_service),
    )

    assert result["route_mode"] == "direct"
    assert "retrieval_query" not in result
    assert "prepared_retrieval_query" not in result
    assert "query_rewrite_mode" not in result
    assert len(model.calls) == 1
    assert retrieval_service.calls == []


async def test_rag_path_runs_route_then_rewrite_then_placeholder() -> None:
    model = RecordingChatModel(response=AIMessage(content="must not be called"))
    graph = build_rag_graph()

    updates = [
        update
        async for update in graph.astream(
            graph_input("当前知识库主要讲什么？"),
            context=runtime_context(model),
            stream_mode="updates",
        )
    ]

    assert [next(iter(update)) for update in updates] == [
        "route",
        "resolve_scope",
        "rewrite",
        "rag_not_implemented",
    ]


def test_state_contains_only_workflow_data_and_router_is_reused() -> None:
    dependency_fields = {
        "settings",
        "model",
        "retrieval_service",
        "session",
        "repository",
        "qdrant_client",
        "embedding_provider",
        "reranker_provider",
        "service",
        "request",
    }

    assert dependency_fields.isdisjoint(RagState.__annotations__)
    assert nodes.route_query is query_router.route_query
