from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock
from uuid import uuid4

from app.core.config import Settings
from app.llm import LLMMessage, LLMStreamDelta
from app.services.document_indexing import (
    DocumentIndexingService,
    HybridRetrievalResult,
    PreparedHybridSearch,
    SemanticSearchResult,
)
from app.services.rag import RagService
from app.services.retrieval_query import PreparedRetrievalQuery


class StreamingProvider:
    def __init__(self, text: str) -> None:
        self.text = text
        self.messages: list[LLMMessage] | None = None

    async def stream(self, messages: list[LLMMessage]) -> AsyncGenerator[LLMStreamDelta]:
        self.messages = messages

        async def generate() -> AsyncGenerator[LLMStreamDelta]:
            yield LLMStreamDelta(self.text, finish_reason="stop")

        return generate()

    async def close(self) -> None:
        return None


def source() -> SemanticSearchResult:
    return SemanticSearchResult(
        score=0.9,
        content="配置值为 true",
        knowledge_base_id=uuid4(),
        document_id=uuid4(),
        document_version_id=uuid4(),
        chunk_id=uuid4(),
        index_generation=uuid4(),
        document_name="config.md",
        relative_path="docs/config.md",
        version_number=1,
        chunk_index=0,
        content_hash="a" * 64,
        chunk_type="paragraph",
        language="markdown",
        section_title="配置",
        page_number=None,
        start_line=1,
        end_line=2,
        ranking_mode="hybrid",
        retrieval_score=0.9,
        retrieval_rank=1,
    )


async def collect(service: RagService, query: str) -> list[tuple[str, dict[str, object]]]:
    return [
        event
        async for event in service.stream_query(
            uuid4(), query=query, language=None, document_id=None
        )
    ]


async def test_direct_route_streams_without_any_retrieval_or_citation_guard() -> None:
    indexing = AsyncMock(spec=DocumentIndexingService)
    provider = StreamingProvider("你好，我是 TraceMind。")
    events = await collect(RagService(indexing, provider, Settings(_env_file=None)), "你好！")

    assert [event for event, _ in events] == [
        "pipeline",
        "pipeline",
        "pipeline",
        "pipeline",
        "retrieval",
        "pipeline",
        "token",
        "pipeline",
        "pipeline",
        "done",
    ]
    routing = next(
        data
        for event, data in events
        if event == "pipeline" and data["phase"] == "routing" and data["status"] == "completed"
    )
    assert routing["route_mode"] == "direct"
    retrieval = next(data for event, data in events if event == "retrieval")
    assert retrieval["sources"] == []
    assert retrieval["embedding_latency_ms"] == retrieval["qdrant_latency_ms"] == 0
    done = events[-1][1]
    assert done["route_mode"] == "direct"
    assert done["valid_citation_count"] == 0
    indexing.prepare_retrieval_query.assert_not_awaited()
    indexing.prepare_hybrid_search.assert_not_awaited()
    indexing.execute_hybrid_search.assert_not_awaited()
    assert provider.messages is not None
    assert provider.messages[-1].content == "你好！"


async def test_non_whitelisted_query_emits_real_rag_pipeline_and_stable_timings() -> None:
    indexing = AsyncMock(spec=DocumentIndexingService)
    prepared_query = PreparedRetrievalQuery(
        original_query="资料里的 hello world 是什么意思",
        semantic_query="资料里的 hello world 是什么意思",
        scoped_document_id=None,
    )
    hybrid = PreparedHybridSearch(uuid4(), prepared_query, None, 10, (uuid4(),), [1.0, 0.0], 12)
    indexing.prepare_retrieval_query.return_value = prepared_query
    indexing.prepare_hybrid_search.return_value = hybrid
    indexing.execute_hybrid_search.return_value = HybridRetrievalResult([source()], 12, 20, 2, 5, 4)
    provider = StreamingProvider("解释 [S1]")
    events = await collect(
        RagService(indexing, provider, Settings(_env_file=None)),
        prepared_query.original_query,
    )

    phases = [(data["phase"], data["status"]) for event, data in events if event == "pipeline"]
    assert ("query_rewrite", "skipped") in phases
    assert ("query_embedding", "completed") in phases
    assert ("hybrid_retrieval", "completed") in phases
    assert ("candidates", "completed") in phases
    assert ("reranking", "skipped") in phases
    assert ("generating", "completed") in phases
    assert phases[-1] == ("completed", "completed")
    retrieval = next(data for event, data in events if event == "retrieval")
    assert retrieval["route_mode"] == "rag"
    assert retrieval["embedding_latency_ms"] == 12
    assert retrieval["qdrant_latency_ms"] == 20
    assert retrieval["fusion_latency_ms"] == 2
    assert retrieval["dense_candidate_count"] == 5
    assert retrieval["sparse_candidate_count"] == 4
    assert "retrieval_query" not in retrieval
    assert events[-1][0] == "done"
    assert events[-1][1]["grounded"] is True
