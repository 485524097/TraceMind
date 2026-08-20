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
from app.reranker import RerankerError, RerankerUnavailableError
from app.services import query_router
from app.services.conversation import ConversationTurn
from app.services.document_indexing import PreparedHybridSearch, SemanticSearchResult
from app.services.document_reranking import DocumentRerankingService
from app.services.exceptions import HybridSearchUnavailableError
from app.services.rag_retrieval import (
    RagHybridRetrievalResult,
    RagRetrievalServiceProtocol,
    RetrievalSearchResult,
)
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
        "rag_not_implemented",
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
