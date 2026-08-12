from dataclasses import dataclass
from typing import Protocol


class RerankerError(Exception):
    """Safe reranker error."""


class RerankerUnavailableError(RerankerError):
    def __init__(self, message: str = "Reranker is unavailable", *, reason: str = "unavailable"):
        super().__init__(message)
        self.reason = reason


class InvalidRerankerInputError(RerankerError):
    pass


@dataclass(frozen=True)
class RerankerCandidate:
    candidate_id: str
    text: str


@dataclass(frozen=True)
class RerankerResult:
    candidate_id: str
    score: float
    rank: int


def validate_rerank_request(
    candidates: list[RerankerCandidate],
    *,
    limit: int,
    max_candidates: int,
) -> None:
    if not candidates:
        if limit != 0:
            raise InvalidRerankerInputError("limit must be zero for empty candidates")
        return
    if len(candidates) > max_candidates:
        raise InvalidRerankerInputError("candidate count exceeds the configured maximum")
    if not 1 <= limit <= len(candidates):
        raise InvalidRerankerInputError("limit must be between 1 and candidate count")
    candidate_ids = [candidate.candidate_id for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise InvalidRerankerInputError("candidate IDs must be unique")


class RerankerProvider(Protocol):
    async def rerank(
        self,
        query: str,
        candidates: list[RerankerCandidate],
        *,
        limit: int,
    ) -> list[RerankerResult]: ...

    async def close(self) -> None: ...
