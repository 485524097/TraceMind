import math

import httpx
from pydantic import ValidationError

from app.reranker.base import (
    InvalidRerankerInputError,
    RerankerCandidate,
    RerankerProvider,
    RerankerResult,
    RerankerUnavailableError,
    validate_rerank_request,
)
from app.reranker.schemas import RerankResponse


class HttpRerankerProvider(RerankerProvider):
    def __init__(
        self,
        base_url: str,
        *,
        read_timeout_seconds: float,
        max_candidates: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.max_candidates = max_candidates
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(
                connect=0.5,
                read=read_timeout_seconds,
                write=read_timeout_seconds,
                pool=0.5,
            ),
            trust_env=False,
        )

    async def rerank(
        self,
        query: str,
        candidates: list[RerankerCandidate],
        *,
        limit: int,
    ) -> list[RerankerResult]:
        if not candidates:
            if limit != 0:
                raise InvalidRerankerInputError("limit must be zero for empty candidates")
            return []
        validate_rerank_request(candidates, limit=limit, max_candidates=self.max_candidates)
        try:
            response = await self._client.post(
                "/rerank",
                json={
                    "query": query,
                    "limit": limit,
                    "candidates": [
                        {"candidate_id": item.candidate_id, "text": item.text}
                        for item in candidates
                    ],
                },
            )
        except httpx.TimeoutException as exc:
            raise RerankerUnavailableError(reason="timeout") from exc
        except httpx.HTTPError as exc:
            raise RerankerUnavailableError(reason="unavailable") from exc
        if not response.is_success:
            raise RerankerUnavailableError(reason="unavailable")
        try:
            payload = RerankResponse.model_validate_json(response.content)
        except (ValidationError, ValueError) as exc:
            raise RerankerUnavailableError(reason="invalid_response") from exc

        expected_ids = {candidate.candidate_id for candidate in candidates}
        result_ids = [item.candidate_id for item in payload.items]
        valid_ranks = [item.rank for item in payload.items] == list(
            range(1, len(payload.items) + 1)
        )
        if (
            len(payload.items) != limit
            or len(result_ids) != len(set(result_ids))
            or not set(result_ids).issubset(expected_ids)
            or not valid_ranks
            or any(not math.isfinite(item.score) for item in payload.items)
        ):
            raise RerankerUnavailableError(reason="invalid_response")
        return [RerankerResult(item.candidate_id, item.score, item.rank) for item in payload.items]

    async def close(self) -> None:
        await self._client.aclose()
