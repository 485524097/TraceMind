from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.reranker import RerankerResult, RerankerUnavailableError
from app.services.document_indexing import SemanticSearchResult
from app.services.document_reranking import (
    DocumentRerankingService,
    build_reranker_candidate_text,
)


def search_result(
    content: str,
    *,
    score: float,
    rank: int,
    section_title: str | None = "配置",
) -> SemanticSearchResult:
    return SemanticSearchResult(
        score=score,
        content=content,
        knowledge_base_id=uuid4(),
        document_id=uuid4(),
        document_version_id=uuid4(),
        chunk_id=uuid4(),
        index_generation=uuid4(),
        document_name="application.yml",
        version_number=1,
        chunk_index=rank,
        content_hash="a" * 64,
        chunk_type="code",
        language="yaml",
        section_title=section_title,
        page_number=None,
        start_line=rank,
        end_line=rank + 1,
        ranking_mode="hybrid",
        retrieval_score=score,
        retrieval_rank=rank,
    )


def test_candidate_text_preserves_document_section_and_raw_content() -> None:
    content = "spring.cloud.nacos.discovery.server-addr=localhost:8848"
    result = search_result(content, score=0.7, rank=1)
    assert build_reranker_candidate_text(result) == f"application.yml\n配置\n{content}"
    no_section = search_result(content, score=0.7, rank=1, section_title=None)
    assert build_reranker_candidate_text(no_section) == f"application.yml\n{content}"


async def test_reranking_maps_ids_preserves_rrf_metadata_and_allows_negative_scores() -> None:
    first = search_result("first", score=0.8, rank=1)
    second = search_result("second", score=0.7, rank=2)
    provider = AsyncMock()
    provider.rerank.return_value = [
        RerankerResult(str(second.chunk_id), 4.2, 1),
        RerankerResult(str(first.chunk_id), -2.5, 2),
    ]

    results = await DocumentRerankingService(provider).rerank("query", [first, second], limit=2)

    assert [item.chunk_id for item in results] == [second.chunk_id, first.chunk_id]
    assert [item.score for item in results] == [4.2, -2.5]
    assert results[0].rerank_score == 4.2
    assert results[0].retrieval_score == 0.7
    assert results[0].retrieval_rank == 2
    assert results[0].ranking_mode == "reranker"
    sent = provider.rerank.await_args.args[1]
    assert sent[0].candidate_id == str(first.chunk_id)
    assert sent[0].text.endswith("first")


@pytest.mark.parametrize("result_ids", [["unknown"], ["duplicate", "duplicate"]])
async def test_reranking_rejects_unknown_or_duplicate_response_ids(
    result_ids: list[str],
) -> None:
    candidates = [
        search_result("first", score=0.8, rank=1),
        search_result("second", score=0.7, rank=2),
    ]
    provider = AsyncMock()
    provider.rerank.return_value = [
        RerankerResult(candidate_id, float(index), index + 1)
        for index, candidate_id in enumerate(result_ids)
    ]
    with pytest.raises(RerankerUnavailableError):
        await DocumentRerankingService(provider).rerank("query", candidates, limit=len(result_ids))


async def test_empty_candidates_do_not_call_provider() -> None:
    provider = AsyncMock()
    assert await DocumentRerankingService(provider).rerank("query", [], limit=0) == []
    provider.rerank.assert_not_called()


async def test_unexpected_provider_error_is_converted_to_safe_unavailable_error() -> None:
    provider = AsyncMock()
    provider.rerank.side_effect = RuntimeError("sensitive provider detail")
    candidate = search_result("first", score=0.8, rank=1)

    with pytest.raises(RerankerUnavailableError) as exc_info:
        await DocumentRerankingService(provider).rerank("query", [candidate], limit=1)

    assert exc_info.value.reason == "internal_error"
    assert "sensitive provider detail" not in str(exc_info.value)
