import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from langchain_core.callbacks.manager import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.graph.state import CompiledStateGraph
from pydantic import Field

from app.core.config import Settings
from app.rag.graph import RagRuntimeContext, RagState, build_rag_graph, nodes
from app.rag.prompt import SYSTEM_PROMPT
from app.reranker import RerankerError, RerankerUnavailableError
from app.services import query_router
from app.services.conversation import ConversationTurn
from app.services.document_indexing import PreparedHybridSearch, SemanticSearchResult
from app.services.document_reranking import DocumentRerankingService
from app.services.exceptions import HybridSearchUnavailableError
from app.services.rag_retrieval import (
    KnowledgeSearchResult,
    RagHybridRetrievalResult,
    RagRetrievalServiceProtocol,
    RetrievalSearchResult,
)
from app.services.retrieval_query import PreparedRetrievalQuery

HISTORY = (ConversationTurn("Nacos 有什么作用？", "它提供配置管理和服务发现。"),)


class RecordingChatModel(BaseChatModel):
    response: AIMessage
    responses: list[AIMessage] = Field(default_factory=list)
    stream_chunks: list[str] = Field(default_factory=list)
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
        return ChatResult(generations=[ChatGeneration(message=self._next_response())])

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
        return ChatResult(generations=[ChatGeneration(message=self._next_response())])

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        self.calls.append(messages)
        self.started.set()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise RuntimeError(self.error)
        if self.stream_chunks:
            for text in self.stream_chunks:
                yield ChatGenerationChunk(message=AIMessageChunk(content=text))
            return
        response = self._next_response()
        yield ChatGenerationChunk(message=AIMessageChunk(content=response.content))

    def _next_response(self) -> AIMessage:
        return self.responses.pop(0) if self.responses else self.response


class RecordingRetrievalService:
    def __init__(
        self,
        prepared: PreparedRetrievalQuery | None = None,
        result: RagHybridRetrievalResult | None = None,
        *,
        error: Exception | None = None,
        delay: float = 0,
    ) -> None:
        self.prepared = prepared
        self.result = result or RagHybridRetrievalResult([], 0, 0, 0, 0, 0)
        self.error = error
        self.delay = delay
        self.calls: list[tuple[UUID, str, UUID | None]] = []
        self.hybrid_calls: list[dict[str, object]] = []
        self.execute_calls: list[PreparedHybridSearch] = []
        self.started = asyncio.Event()

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

    async def prepare_hybrid_search(
        self,
        knowledge_base_id: UUID,
        *,
        query: str,
        limit: int,
        language: str | None,
        document_id: UUID | None,
        prepared_query: PreparedRetrievalQuery | None = None,
    ) -> PreparedHybridSearch:
        self.hybrid_calls.append(
            {
                "knowledge_base_id": knowledge_base_id,
                "query": query,
                "limit": limit,
                "language": language,
                "document_id": document_id,
                "prepared_query": prepared_query,
            }
        )
        return PreparedHybridSearch(
            knowledge_base_id,
            prepared_query or PreparedRetrievalQuery(query, query, document_id),
            language,
            limit,
            (),
            None,
            0,
        )

    async def execute_hybrid_search(
        self,
        prepared: PreparedHybridSearch,
    ) -> RagHybridRetrievalResult:
        self.execute_calls.append(prepared)
        self.started.set()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.result


class RecordingRerankingService(DocumentRerankingService):
    def __init__(
        self,
        results: list[RetrievalSearchResult] | None = None,
        *,
        error: RerankerError | None = None,
        delay: float = 0,
    ) -> None:
        self.results = results
        self.error = error
        self.delay = delay
        self.calls: list[tuple[str, list[RetrievalSearchResult], int]] = []
        self.started = asyncio.Event()

    async def rerank(
        self,
        query: str,
        candidates: list[RetrievalSearchResult],
        *,
        limit: int,
    ) -> list[RetrievalSearchResult]:
        self.calls.append((query, candidates, limit))
        self.started.set()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.results if self.results is not None else candidates[:limit]


def search_result(content: str, *, score: float, rank: int) -> SemanticSearchResult:
    return SemanticSearchResult(
        score=score,
        content=content,
        knowledge_base_id=uuid4(),
        document_id=uuid4(),
        document_version_id=uuid4(),
        chunk_id=uuid4(),
        index_generation=uuid4(),
        document_name="guide.md",
        relative_path="docs/guide.md",
        version_number=1,
        chunk_index=rank,
        content_hash="a" * 64,
        chunk_type="paragraph",
        language="markdown",
        section_title=None,
        page_number=None,
        start_line=None,
        end_line=None,
        ranking_mode="hybrid",
        retrieval_score=score,
        retrieval_rank=rank,
    )


def knowledge_search_result(
    content: str,
    *,
    score: float,
    rank: int,
) -> KnowledgeSearchResult:
    return KnowledgeSearchResult(
        score=score,
        content=content,
        knowledge_base_id=uuid4(),
        knowledge_entry_id=uuid4(),
        chunk_id=uuid4(),
        index_generation=uuid4(),
        knowledge_question="事务为什么失败？",
        knowledge_updated_at=datetime.now(UTC),
        chunk_index=rank,
        content_hash="b" * 64,
        chunk_type="knowledge_entry",
        section_title="Solution",
        ranking_mode="hybrid",
        retrieval_score=score,
        retrieval_rank=rank,
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
    reranking_service: DocumentRerankingService | None = None,
    **settings: object,
) -> RagRuntimeContext:
    return RagRuntimeContext(
        model=model,
        settings=Settings(_env_file=None, **settings),
        retrieval_service=retrieval_service or RecordingRetrievalService(),
        reranking_service=reranking_service,
    )


async def collect_custom_stream(
    state: RagState,
    context: RagRuntimeContext,
) -> list[dict[str, Any]]:
    product_events: list[dict[str, Any]] = []
    async for event in build_rag_graph().astream(
        state,
        context=context,
        stream_mode="custom",
    ):
        assert isinstance(event, dict)
        product_events.append(event)
    return product_events


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


async def test_direct_event_stream_emits_tokens_then_completed_done() -> None:
    model = RecordingChatModel(
        response=AIMessage(content="unused"),
        stream_chunks=["你", "好", "", "！"],
    )

    context = runtime_context(model)
    product_events = await collect_custom_stream(
        graph_input("你好！"),
        context,
    )
    output = await build_rag_graph().ainvoke(
        graph_input("你好！"),
        context=context,
    )

    assert [event["type"] for event in product_events] == ["token", "token", "token", "done"]
    assert [event["text"] for event in product_events[:-1]] == ["你", "好", "！"]
    assert "".join(event["text"] for event in product_events[:-1]) == output["answer"]
    assert output["answer"] == "你好！"
    assert output["terminal_status"] == "completed"
    assert all(event["type"] != "sources" for event in product_events)
    done = product_events[-1]
    assert done == {
        "type": "done",
        "terminal_status": "completed",
        "grounded": False,
        "valid_citation_count": 0,
        "invalid_citation_count": 0,
    }


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
    assert result["terminal_status"] == "no_answer"
    assert result["answer"] == "知识库中未找到足够相关的信息。"
    assert result["grounded"] is False
    assert result["valid_citation_count"] == 0
    assert result["invalid_citation_count"] == 0
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
    assert "rag_context" not in result
    assert len(model.calls) == 1
    assert retrieval_service.calls == []
    assert retrieval_service.hybrid_calls == []
    assert retrieval_service.execute_calls == []


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
        "retrieve",
        "rerank",
        "prepare_context",
        "no_answer",
        "finalize",
    ]


async def test_retrieve_uses_rewritten_query_scope_candidate_limit_and_diagnostics() -> None:
    original_query = "src/main/java/demo/UserService.java 中它返回什么？"
    scoped_document_id = uuid4()
    prepared_scope = PreparedRetrievalQuery(
        original_query=original_query,
        semantic_query="它返回什么？",
        scoped_document_id=scoped_document_id,
        path_scope_mode="exact",
        explicit_relative_path="src/main/java/demo/UserService.java",
    )
    candidates = [
        search_result("first", score=0.8, rank=1),
        search_result("second", score=0.7, rank=2),
    ]
    retrieval_service = RecordingRetrievalService(
        prepared_scope,
        RagHybridRetrievalResult(candidates, 11, 22, 33, 7, 5),
    )
    model = RecordingChatModel(
        response=AIMessage(
            content='{"action":"rewrite","query":"UserService source 方法返回什么？"}'
        )
    )

    result = await build_rag_graph().ainvoke(
        graph_input(original_query, HISTORY),
        context=runtime_context(
            model,
            retrieval_service,
            rag_retrieval_limit=2,
            rag_rerank_candidate_limit=3,
        ),
    )

    assert result["query"] == original_query
    assert result["prepared_retrieval_query"] == prepared_scope
    assert result["retrieval_candidates"] == candidates
    assert result["embedding_latency_ms"] == 11
    assert result["qdrant_latency_ms"] == 22
    assert result["fusion_latency_ms"] == 33
    assert result["dense_candidate_count"] == 7
    assert result["sparse_candidate_count"] == 5
    assert len(retrieval_service.hybrid_calls) == 1
    call = retrieval_service.hybrid_calls[0]
    assert call["query"] == "UserService source 方法返回什么？"
    assert call["query"] != original_query
    assert call["limit"] == 3
    retrieval_scope = call["prepared_query"]
    assert isinstance(retrieval_scope, PreparedRetrievalQuery)
    assert retrieval_scope.semantic_query == "UserService source 方法返回什么？"
    assert retrieval_scope.scoped_document_id == scoped_document_id
    assert retrieval_scope.path_scope_mode == "exact"
    assert retrieval_scope.explicit_relative_path == "src/main/java/demo/UserService.java"


async def test_hybrid_search_unavailable_propagates_from_graph() -> None:
    retrieval_service = RecordingRetrievalService(
        error=HybridSearchUnavailableError("Hybrid search is unavailable")
    )

    with pytest.raises(HybridSearchUnavailableError):
        await build_rag_graph().ainvoke(
            graph_input("如何配置 Nacos？"),
            context=runtime_context(
                RecordingChatModel(response=AIMessage(content="unused")),
                retrieval_service,
            ),
        )


async def test_retrieval_cancellation_propagates() -> None:
    retrieval_service = RecordingRetrievalService(delay=10)
    task = asyncio.create_task(
        build_rag_graph().ainvoke(
            graph_input("如何配置 Nacos？"),
            context=runtime_context(
                RecordingChatModel(response=AIMessage(content="unused")),
                retrieval_service,
            ),
        )
    )

    async with asyncio.timeout(1):
        await retrieval_service.started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_empty_candidates_skip_reranker() -> None:
    retrieval_service = RecordingRetrievalService()
    reranking_service = RecordingRerankingService()

    result = await build_rag_graph().ainvoke(
        graph_input("如何配置 Nacos？"),
        context=runtime_context(
            RecordingChatModel(response=AIMessage(content="unused")),
            retrieval_service,
            reranking_service,
            reranker_enabled=True,
        ),
    )

    assert result["ranked_results"] == []
    assert result["retrieval_mode"] == "hybrid"
    assert result["rerank_latency_ms"] == 0
    assert result["reranker_fallback"] is False
    assert result["reranker_fallback_reason"] is None
    assert reranking_service.calls == []
    assert result["answer"] == "知识库中未找到足够相关的信息。"
    assert result["terminal_status"] == "no_answer"


async def test_disabled_reranker_uses_hybrid_top_n_without_calling_service() -> None:
    candidates = [
        search_result("first", score=0.8, rank=1),
        search_result("second", score=0.7, rank=2),
        search_result("third", score=0.6, rank=3),
    ]
    retrieval_service = RecordingRetrievalService(
        result=RagHybridRetrievalResult(candidates, 1, 2, 3, 3, 2)
    )
    reranking_service = RecordingRerankingService()

    result = await build_rag_graph().ainvoke(
        graph_input("如何配置 Nacos？"),
        context=runtime_context(
            RecordingChatModel(response=AIMessage(content="unused")),
            retrieval_service,
            reranking_service,
            reranker_enabled=False,
            rag_retrieval_limit=2,
        ),
    )

    assert result["retrieval_candidates"] == candidates
    assert result["ranked_results"] == candidates[:2]
    assert result["retrieval_mode"] == "hybrid"
    assert result["reranker_fallback"] is False
    assert reranking_service.calls == []


async def test_enabled_reranker_receives_retrieval_query_and_final_limit() -> None:
    candidates = [
        search_result("first", score=0.8, rank=1),
        search_result("second", score=0.7, rank=2),
        search_result("third", score=0.6, rank=3),
    ]
    reranked = [candidates[1], candidates[0]]
    retrieval_service = RecordingRetrievalService(
        result=RagHybridRetrievalResult(candidates, 1, 2, 3, 3, 2)
    )
    reranking_service = RecordingRerankingService(reranked)
    model = RecordingChatModel(
        response=AIMessage(content='{"action":"rewrite","query":"Nacos 服务发现配置"}')
    )

    result = await build_rag_graph().ainvoke(
        graph_input("它如何配置？", HISTORY),
        context=runtime_context(
            model,
            retrieval_service,
            reranking_service,
            reranker_enabled=True,
            rag_retrieval_limit=2,
        ),
    )

    assert reranking_service.calls == [("Nacos 服务发现配置", candidates, 2)]
    assert result["ranked_results"] == reranked
    assert result["retrieval_mode"] == "hybrid_reranker"
    assert result["reranker_fallback"] is False
    assert result["reranker_fallback_reason"] is None


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (RerankerUnavailableError(reason="timeout"), "timeout"),
        (RerankerError("internal detail"), "internal_error"),
    ],
)
async def test_reranker_error_falls_back_to_hybrid_top_n(
    error: RerankerError,
    reason: str,
) -> None:
    candidates = [
        search_result("first", score=0.8, rank=1),
        search_result("second", score=0.7, rank=2),
        search_result("third", score=0.6, rank=3),
    ]
    retrieval_service = RecordingRetrievalService(
        result=RagHybridRetrievalResult(candidates, 1, 2, 3, 3, 2)
    )
    reranking_service = RecordingRerankingService(error=error)

    result = await build_rag_graph().ainvoke(
        graph_input("如何配置 Nacos？"),
        context=runtime_context(
            RecordingChatModel(response=AIMessage(content="unused")),
            retrieval_service,
            reranking_service,
            reranker_enabled=True,
            rag_retrieval_limit=2,
        ),
    )

    assert result["ranked_results"] == candidates[:2]
    assert result["retrieval_mode"] == "hybrid_fallback"
    assert result["reranker_fallback"] is True
    assert result["reranker_fallback_reason"] == reason


async def test_reranker_cancellation_propagates() -> None:
    candidates = [search_result("first", score=0.8, rank=1)]
    retrieval_service = RecordingRetrievalService(
        result=RagHybridRetrievalResult(candidates, 1, 2, 3, 1, 1)
    )
    reranking_service = RecordingRerankingService(delay=10)
    task = asyncio.create_task(
        build_rag_graph().ainvoke(
            graph_input("如何配置 Nacos？"),
            context=runtime_context(
                RecordingChatModel(response=AIMessage(content="unused")),
                retrieval_service,
                reranking_service,
                reranker_enabled=True,
            ),
        )
    )

    async with asyncio.timeout(1):
        await reranking_service.started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_prepare_context_uses_ranked_results_and_context_budget() -> None:
    first = search_result("a" * 600, score=0.9, rank=1)
    second = search_result("b" * 600, score=0.8, rank=2)
    retrieval_service = RecordingRetrievalService(
        result=RagHybridRetrievalResult([first, second], 1, 2, 3, 2, 2)
    )
    reranking_service = RecordingRerankingService([second, first])

    result = await build_rag_graph().ainvoke(
        graph_input("哪个片段相关？"),
        context=runtime_context(
            RecordingChatModel(response=AIMessage(content="第二个片段相关 [S1]")),
            retrieval_service,
            reranking_service,
            reranker_enabled=True,
            rag_max_context_chars=1_000,
        ),
    )

    assert result["retrieval_candidates"] == [first, second]
    assert result["ranked_results"] == [second, first]
    assert [source.content for source in result["rag_context"].sources] == [second.content]
    assert [source.source_id for source in result["rag_context"].sources] == ["S1"]


async def test_grounded_prompt_preserves_document_knowledge_and_scope_payload() -> None:
    original_query = "docs/guide.md 中事务为什么失败？"
    document = search_result("Document answer", score=0.9, rank=1)
    knowledge = knowledge_search_result("Maintained answer", score=0.8, rank=2)
    prepared_scope = PreparedRetrievalQuery(
        original_query=original_query,
        semantic_query="事务为什么失败？",
        scoped_document_id=document.document_id,
        path_scope_mode="exact",
        explicit_relative_path="docs/guide.md",
    )
    retrieval_service = RecordingRetrievalService(
        prepared_scope,
        RagHybridRetrievalResult([document, knowledge], 1, 2, 3, 2, 2),
    )
    model = RecordingChatModel(response=AIMessage(content="文档结论 [S1]，知识条目 [S2]"))

    result = await build_rag_graph().ainvoke(
        graph_input(original_query),
        context=runtime_context(model, retrieval_service),
    )

    context = result["rag_context"]
    assert [source.source_id for source in context.sources] == ["S1", "S2"]
    assert context.sources[0].source_type == "document"
    assert context.sources[0].document_id == document.document_id
    assert context.sources[0].document_version_id == document.document_version_id
    assert context.sources[0].relative_path == document.relative_path
    assert context.sources[1].source_type == "knowledge_entry"
    assert context.sources[1].knowledge_entry_id == knowledge.knowledge_entry_id
    assert len(model.calls) == 1
    system, human = model.calls[0]
    assert isinstance(system, SystemMessage)
    assert system.content == SYSTEM_PROMPT
    assert isinstance(human, HumanMessage)
    payload = json.loads(str(human.content))
    assert payload["question"] == original_query
    assert payload["conversation_history"] == []
    assert payload["scoped_relative_path"] == "docs/guide.md"
    document_payload, knowledge_payload = payload["sources"]
    assert document_payload["source_id"] == "S1"
    assert document_payload["document_id"] == str(document.document_id)
    assert document_payload["relative_path"] == document.relative_path
    assert document_payload["content"] == document.content
    assert knowledge_payload["source_id"] == "S2"
    assert knowledge_payload["knowledge_entry_id"] == str(knowledge.knowledge_entry_id)
    assert knowledge_payload["validation_status"] == "verified"
    assert knowledge_payload["content"] == knowledge.content
    assert result["answer"] == "文档结论 [S1]，知识条目 [S2]"
    assert result["grounded"] is True
    assert result["valid_citation_count"] == 2
    assert result["invalid_citation_count"] == 0
    assert result["terminal_status"] == "completed"


@pytest.mark.parametrize(
    ("rewrite_output", "expected_mode", "history_in_payload"),
    [
        ('{"action":"rewrite","query":"Nacos 服务发现配置"}', "rewritten", True),
        ("invalid", "fallback", True),
        ('{"action":"keep","query":"它如何配置？"}', "skipped", False),
    ],
)
async def test_grounded_history_depends_on_query_rewrite_mode(
    rewrite_output: str,
    expected_mode: str,
    history_in_payload: bool,
) -> None:
    candidate = search_result("Nacos configuration", score=0.9, rank=1)
    retrieval_service = RecordingRetrievalService(
        result=RagHybridRetrievalResult([candidate], 1, 2, 3, 1, 1)
    )
    model = RecordingChatModel(
        response=AIMessage(content="unused"),
        responses=[
            AIMessage(content=rewrite_output),
            AIMessage(content="配置方法 [S1]"),
        ],
    )

    result = await build_rag_graph().ainvoke(
        graph_input("它如何配置？", HISTORY),
        context=runtime_context(model, retrieval_service),
    )

    assert result["query_rewrite_mode"] == expected_mode
    assert len(model.calls) == 2
    payload = json.loads(str(model.calls[1][1].content))
    expected_history = (
        [{"user": HISTORY[0].user, "assistant": HISTORY[0].assistant}] if history_in_payload else []
    )
    assert payload["conversation_history"] == expected_history


@pytest.mark.parametrize(
    ("model_answer", "safe_answer", "grounded", "valid_count", "invalid_count"),
    [
        ("有效 [S1]", "有效 [S1]", True, 1, 0),
        ("非法 [S999]", "非法 ", False, 0, 1),
        ("混合 [S1] [S999]", "混合 [S1] ", True, 1, 1),
        ("没有引用", "没有引用", False, 0, 0),
    ],
)
async def test_grounded_generation_filters_and_counts_citations(
    model_answer: str,
    safe_answer: str,
    grounded: bool,
    valid_count: int,
    invalid_count: int,
) -> None:
    candidate = search_result("source", score=0.9, rank=1)
    retrieval_service = RecordingRetrievalService(
        result=RagHybridRetrievalResult([candidate], 1, 2, 3, 1, 1)
    )

    result = await build_rag_graph().ainvoke(
        graph_input("来源是什么？"),
        context=runtime_context(
            RecordingChatModel(response=AIMessage(content=model_answer)),
            retrieval_service,
        ),
    )

    assert result["answer"] == safe_answer
    assert result["grounded"] is grounded
    assert result["valid_citation_count"] == valid_count
    assert result["invalid_citation_count"] == invalid_count
    assert result["terminal_status"] == "completed"


@pytest.mark.parametrize(
    ("chunks", "token_texts", "grounded", "valid_count", "invalid_count"),
    [
        (["答案 [S", "1]"], ["答案 ", "[S1]"], True, 1, 0),
        (["答案 [S", "999]"], ["答案 "], False, 0, 1),
        (["答案 [S1"], ["答案 "], False, 0, 1),
        (["答案 [S999"], ["答案 "], False, 0, 1),
        (["没有引用"], ["没有引用"], False, 0, 0),
    ],
)
async def test_grounded_event_stream_is_citation_safe_across_chunks_and_eof(
    chunks: list[str],
    token_texts: list[str],
    grounded: bool,
    valid_count: int,
    invalid_count: int,
) -> None:
    candidate = search_result("source", score=0.9, rank=1)
    retrieval_service = RecordingRetrievalService(
        result=RagHybridRetrievalResult([candidate], 1, 2, 3, 1, 1)
    )
    model = RecordingChatModel(
        response=AIMessage(content="unused"),
        stream_chunks=chunks,
    )

    context = runtime_context(model, retrieval_service)
    product_events = await collect_custom_stream(
        graph_input("来源是什么？"),
        context,
    )
    output = await build_rag_graph().ainvoke(
        graph_input("来源是什么？"),
        context=context,
    )

    assert [event["type"] for event in product_events] == [
        "sources",
        *("token" for _ in token_texts),
        "done",
    ]
    source_event = product_events[0]
    assert source_event["source_count"] == 1
    assert source_event["sources"][0]["source_id"] == "S1"
    streamed_texts = [event["text"] for event in product_events if event["type"] == "token"]
    assert streamed_texts == token_texts
    assert "[S999]" not in "".join(streamed_texts)
    assert "[S999" not in "".join(streamed_texts)
    assert "[S1" not in "".join(streamed_texts).replace("[S1]", "")
    assert output["answer"] == "".join(streamed_texts)
    assert output["grounded"] is grounded
    assert output["valid_citation_count"] == valid_count
    assert output["invalid_citation_count"] == invalid_count
    done = product_events[-1]
    assert done["terminal_status"] == "completed"
    assert done["grounded"] is grounded
    assert done["valid_citation_count"] == valid_count
    assert done["invalid_citation_count"] == invalid_count


async def test_no_answer_event_stream_emits_no_answer_then_done_without_model() -> None:
    model = RecordingChatModel(
        response=AIMessage(content="must not be called"),
        stream_chunks=["must not be called"],
    )

    context = runtime_context(model)
    product_events = await collect_custom_stream(
        graph_input("知识库里不存在的问题"),
        context,
    )
    output = await build_rag_graph().ainvoke(
        graph_input("知识库里不存在的问题"),
        context=context,
    )

    assert product_events == [
        {
            "type": "no_answer",
            "message": "知识库中未找到足够相关的信息。",
        },
        {
            "type": "done",
            "terminal_status": "no_answer",
            "grounded": False,
            "valid_citation_count": 0,
            "invalid_citation_count": 0,
        },
    ]
    assert model.calls == []
    assert output["answer"] == "知识库中未找到足够相关的信息。"
    assert output["terminal_status"] == "no_answer"


@pytest.mark.parametrize("grounded", [False, True])
async def test_event_stream_model_cancellation_propagates(grounded: bool) -> None:
    model = RecordingChatModel(
        response=AIMessage(content="unused"),
        delay=10,
    )
    retrieval_service = (
        RecordingRetrievalService(
            result=RagHybridRetrievalResult(
                [search_result("source", score=0.9, rank=1)],
                1,
                2,
                3,
                1,
                1,
            )
        )
        if grounded
        else RecordingRetrievalService()
    )
    state = graph_input("来源是什么？" if grounded else "你好！")

    async def consume() -> None:
        async for _ in build_rag_graph().astream(
            state,
            context=runtime_context(model, retrieval_service),
            stream_mode="custom",
        ):
            pass

    task = asyncio.create_task(consume())
    async with asyncio.timeout(1):
        await model.started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_event_stream_model_error_propagates_without_custom_error_event() -> None:
    model = RecordingChatModel(
        response=AIMessage(content="unused"),
        error="private stream detail",
    )
    custom_events: list[dict[str, Any]] = []

    with pytest.raises(RuntimeError, match="private stream detail"):
        async for event in build_rag_graph().astream(
            graph_input("你好！"),
            context=runtime_context(model),
            stream_mode="custom",
        ):
            assert isinstance(event, dict)
            custom_events.append(event)
    assert all(event["type"] != "error" for event in custom_events)


async def test_grounded_model_error_propagates() -> None:
    candidate = search_result("source", score=0.9, rank=1)
    retrieval_service = RecordingRetrievalService(
        result=RagHybridRetrievalResult([candidate], 1, 2, 3, 1, 1)
    )

    with pytest.raises(RuntimeError, match="private model detail"):
        await build_rag_graph().ainvoke(
            graph_input("来源是什么？"),
            context=runtime_context(
                RecordingChatModel(
                    response=AIMessage(content="unused"),
                    error="private model detail",
                ),
                retrieval_service,
            ),
        )


async def test_grounded_generation_cancellation_propagates() -> None:
    candidate = search_result("source", score=0.9, rank=1)
    retrieval_service = RecordingRetrievalService(
        result=RagHybridRetrievalResult([candidate], 1, 2, 3, 1, 1)
    )
    model = RecordingChatModel(response=AIMessage(content="unused"), delay=10)
    task = asyncio.create_task(
        build_rag_graph().ainvoke(
            graph_input("来源是什么？"),
            context=runtime_context(model, retrieval_service),
        )
    )

    async with asyncio.timeout(1):
        await model.started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


def test_state_contains_only_workflow_data_and_router_is_reused() -> None:
    dependency_fields = {
        "settings",
        "model",
        "retrieval_service",
        "reranking_service",
        "session",
        "repository",
        "provider",
        "qdrant",
        "client",
        "qdrant_client",
        "embedding_provider",
        "reranker_provider",
        "service",
        "request",
    }

    assert dependency_fields.isdisjoint(RagState.__annotations__)
    assert "PreparedHybridSearch" not in RagState.__annotations__
    assert nodes.route_query is query_router.route_query
