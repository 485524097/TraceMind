from typing import Any
from uuid import uuid4

from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.graph.state import CompiledStateGraph

from app.rag.graph import RagRuntimeContext, RagState, build_rag_graph, nodes
from app.services import query_router


class RecordingChatModel(BaseChatModel):
    response: AIMessage
    calls: list[list[BaseMessage]] = []

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
        return ChatResult(generations=[ChatGeneration(message=self.response)])


def graph_input(query: str) -> RagState:
    return {
        "trace_id": uuid4(),
        "knowledge_base_id": uuid4(),
        "query": query,
        "language": None,
        "document_id": None,
    }


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
        context=RagRuntimeContext(model=model),
    )

    assert result["route_mode"] == "direct"
    assert result["answer"] == "你好，我是 TraceMind。"
    assert result["terminal_status"] == "completed"
    assert len(model.calls) == 1
    assert isinstance(model.calls[0][0], SystemMessage)
    assert "简单社交表达" in model.calls[0][0].content
    assert isinstance(model.calls[0][1], HumanMessage)
    assert model.calls[0][1].content == "你好！"


async def test_rag_route_uses_placeholder_without_calling_model() -> None:
    model = RecordingChatModel(response=AIMessage(content="must not be called"))
    graph = build_rag_graph()

    result = await graph.ainvoke(
        graph_input("当前知识库主要讲什么？"),
        context=RagRuntimeContext(model=model),
    )

    assert result["route_mode"] == "rag"
    assert result["terminal_status"] == "rag_pending"
    assert "answer" not in result
    assert model.calls == []


def test_state_contains_only_workflow_data_and_router_is_reused() -> None:
    dependency_fields = {
        "settings",
        "model",
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
