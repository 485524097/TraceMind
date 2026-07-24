import asyncio
import gc
import logging
from time import perf_counter
from typing import Any

import torch
from sentence_transformers import CrossEncoder

from app.reranker.base import (
    InvalidRerankerInputError,
    RerankerCandidate,
    RerankerProvider,
    RerankerResult,
    RerankerUnavailableError,
    validate_rerank_request,
)

logger = logging.getLogger(__name__)
_DTYPES = {
    "float16": torch.float16,
    "float32": torch.float32,
    "bfloat16": torch.bfloat16,
}


class QwenCrossEncoderProvider(RerankerProvider):
    def __init__(
        self,
        *,
        model_name: str,
        device: str,
        dtype: str,
        max_length: int,
        batch_size: int,
        max_candidates: int,
        max_concurrency: int,
        local_files_only: bool,
        cache_folder: str | None,
        instruction: str,
        queue_timeout_seconds: float,
    ) -> None:
        if max_concurrency != 1:
            raise InvalidRerankerInputError("only one concurrent inference is supported")
        self.model_name = model_name
        self.device = device
        self.batch_size = batch_size
        self.max_length = max_length
        self.max_candidates = max_candidates
        self.instruction = instruction
        self.queue_timeout_seconds = queue_timeout_seconds
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._ready = False
        try:
            self._model: CrossEncoder | None = CrossEncoder(
                model_name,
                device=device,
                cache_folder=cache_folder,
                local_files_only=local_files_only,
                trust_remote_code=True,
                max_length=max_length,
                prompts={"tracemind": instruction},
                default_prompt_name="tracemind",
                model_kwargs={"torch_dtype": _DTYPES[dtype]},
            )
            self._model.model.eval()
            self._ready = True
        except Exception as exc:
            self._cleanup_cuda()
            raise RerankerUnavailableError(reason="model_load") from exc

    @property
    def ready(self) -> bool:
        return self._ready and self._model is not None

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
        if not self.ready:
            raise RerankerUnavailableError(reason="unavailable")
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=self.queue_timeout_seconds,
            )
        except TimeoutError as exc:
            raise RerankerUnavailableError(reason="timeout") from exc

        started_at = perf_counter()
        try:
            return await asyncio.to_thread(self._rank_sync, query, candidates, limit)
        except torch.cuda.OutOfMemoryError as exc:
            self._ready = False
            self._cleanup_cuda()
            logger.error(
                "Reranker inference failed error_type=%s cuda_oom=true "
                "query_length=%s candidate_count=%s batch_size=%s max_length=%s",
                type(exc).__name__,
                len(query),
                len(candidates),
                self.batch_size,
                self.max_length,
            )
            raise RerankerUnavailableError(reason="cuda_oom") from exc
        except RerankerUnavailableError:
            raise
        except Exception as exc:
            logger.error(
                "Reranker inference failed error_type=%s cuda_oom=false "
                "query_length=%s candidate_count=%s batch_size=%s max_length=%s",
                type(exc).__name__,
                len(query),
                len(candidates),
                self.batch_size,
                self.max_length,
            )
            raise RerankerUnavailableError(reason="internal_error") from exc
        finally:
            self._semaphore.release()
            logger.info(
                "Reranker inference query_length=%s candidate_count=%s batch_size=%s "
                "max_length=%s latency_ms=%s",
                len(query),
                len(candidates),
                self.batch_size,
                self.max_length,
                round((perf_counter() - started_at) * 1_000),
            )

    def _rank_sync(
        self,
        query: str,
        candidates: list[RerankerCandidate],
        limit: int,
    ) -> list[RerankerResult]:
        model = self._model
        if model is None:
            raise RerankerUnavailableError(reason="unavailable")
        with torch.inference_mode():
            ranked: list[dict[str, Any]] = model.rank(
                query,
                [candidate.text for candidate in candidates],
                top_k=len(candidates),
                prompt=self.instruction,
                batch_size=self.batch_size,
                show_progress_bar=False,
            )
        scores = {int(item["corpus_id"]): float(item["score"]) for item in ranked}
        if set(scores) != set(range(len(candidates))):
            raise RerankerUnavailableError(reason="invalid_response")
        stable = sorted(range(len(candidates)), key=lambda index: (-scores[index], index))
        return [
            RerankerResult(candidates[index].candidate_id, scores[index], rank)
            for rank, index in enumerate(stable[:limit], start=1)
        ]

    async def close(self) -> None:
        self._ready = False
        self._model = None
        self._cleanup_cuda()

    @staticmethod
    def _cleanup_cuda() -> None:
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
