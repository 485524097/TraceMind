import asyncio
from dataclasses import replace

from app.reranker import (
    RerankerCandidate,
    RerankerError,
    RerankerProvider,
    RerankerUnavailableError,
)
from app.services.document_indexing import SemanticSearchResult


def build_reranker_candidate_text(result: SemanticSearchResult) -> str:
    parts = [result.document_name]
    if result.section_title:
        parts.append(result.section_title)
    parts.append(result.content)
    return "\n".join(parts)


class DocumentRerankingService:
    def __init__(self, provider: RerankerProvider) -> None:
        self.provider = provider

    async def rerank(
        self,
        query: str,
        candidates: list[SemanticSearchResult],
        *,
        limit: int,
    ) -> list[SemanticSearchResult]:
        if not candidates:
            return []
        by_id = {str(candidate.chunk_id): candidate for candidate in candidates}
        if len(by_id) != len(candidates):
            raise RerankerUnavailableError(reason="invalid_response")
        try:
            results = await self.provider.rerank(
                query,
                [
                    RerankerCandidate(
                        candidate_id=str(candidate.chunk_id),
                        text=build_reranker_candidate_text(candidate),
                    )
                    for candidate in candidates
                ],
                limit=limit,
            )
        except asyncio.CancelledError:
            raise
        except RerankerError:
            raise
        except Exception as exc:
            raise RerankerUnavailableError(reason="internal_error") from exc
        result_ids = [result.candidate_id for result in results]
        if (
            len(results) != limit
            or len(result_ids) != len(set(result_ids))
            or not set(result_ids).issubset(by_id)
        ):
            raise RerankerUnavailableError(reason="invalid_response")
        return [
            replace(
                by_id[result.candidate_id],
                score=result.score,
                ranking_mode="reranker",
                rerank_score=result.score,
            )
            for result in results
        ]
