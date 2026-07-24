import asyncio
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import torch

from app.reranker import (
    InvalidRerankerInputError,
    RerankerCandidate,
    RerankerUnavailableError,
)
from app.reranker.base import validate_rerank_request
from app.reranker.cross_encoder import QwenCrossEncoderProvider


def candidates() -> list[RerankerCandidate]:
    return [
        RerankerCandidate("first", "first content"),
        RerankerCandidate("second", "second content"),
    ]


def provider_without_loading() -> QwenCrossEncoderProvider:
    provider = object.__new__(QwenCrossEncoderProvider)
    provider.model_name = "test"
    provider.device = "cpu"
    provider.batch_size = 2
    provider.max_length = 1024
    provider.max_candidates = 20
    provider.instruction = "instruction"
    provider.queue_timeout_seconds = 1
    provider._semaphore = asyncio.Semaphore(1)
    provider._ready = True
    return provider


def test_request_validation_rejects_duplicates_and_invalid_limit() -> None:
    with pytest.raises(InvalidRerankerInputError):
        validate_rerank_request(
            [RerankerCandidate("same", "a"), RerankerCandidate("same", "b")],
            limit=1,
            max_candidates=20,
        )
    with pytest.raises(InvalidRerankerInputError):
        validate_rerank_request(candidates(), limit=3, max_candidates=20)
    validate_rerank_request([], limit=0, max_candidates=20)


def test_cross_encoder_stably_orders_equal_raw_scores_without_sigmoid() -> None:
    provider = provider_without_loading()
    model = Mock()
    model.rank.return_value = [
        {"corpus_id": 1, "score": -3.0},
        {"corpus_id": 0, "score": -3.0},
    ]
    provider._model = model

    results = provider._rank_sync("query", candidates(), 2)

    assert [item.candidate_id for item in results] == ["first", "second"]
    assert [item.score for item in results] == [-3.0, -3.0]
    model.rank.assert_called_once_with(
        "query",
        ["first content", "second content"],
        top_k=2,
        prompt="instruction",
        batch_size=2,
        show_progress_bar=False,
    )


async def test_semaphore_prevents_parallel_inference() -> None:
    provider = provider_without_loading()
    provider._model = SimpleNamespace()
    active = 0
    max_active = 0

    def slow_rank(query: str, items: list[RerankerCandidate], limit: int) -> list[object]:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        time.sleep(0.05)
        active -= 1
        return []

    provider._rank_sync = slow_rank  # type: ignore[method-assign]
    await asyncio.gather(
        provider.rerank("one", candidates(), limit=1),
        provider.rerank("two", candidates(), limit=1),
    )
    assert max_active == 1


async def test_cuda_oom_is_safe_and_marks_provider_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = provider_without_loading()
    provider._model = SimpleNamespace()
    cleanup = Mock()
    monkeypatch.setattr(provider, "_cleanup_cuda", cleanup)

    def fail(query: str, items: list[RerankerCandidate], limit: int) -> list[object]:
        raise torch.cuda.OutOfMemoryError("private CUDA details")

    provider._rank_sync = fail  # type: ignore[method-assign]
    with pytest.raises(RerankerUnavailableError) as caught:
        await provider.rerank("private query", candidates(), limit=1)
    assert str(caught.value) == "Reranker is unavailable"
    assert caught.value.reason == "cuda_oom"
    assert provider.ready is False
    cleanup.assert_called_once()
