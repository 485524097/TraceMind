import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Protocol

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.reranker import (
    InvalidRerankerInputError,
    RerankerCandidate,
    RerankerResult,
    RerankerUnavailableError,
)
from app.reranker.cross_encoder import QwenCrossEncoderProvider
from app.reranker.schemas import RerankRequest, RerankResponse, RerankResultResponse

logger = logging.getLogger(__name__)


class ServerReranker(Protocol):
    @property
    def ready(self) -> bool: ...

    async def rerank(
        self,
        query: str,
        candidates: list[RerankerCandidate],
        *,
        limit: int,
    ) -> list[RerankerResult]: ...

    async def close(self) -> None: ...


ProviderFactory = Callable[[Settings], ServerReranker]


def build_cross_encoder(settings: Settings) -> ServerReranker:
    return QwenCrossEncoderProvider(
        model_name=settings.reranker_model_name,
        device=settings.reranker_device,
        dtype=settings.reranker_dtype,
        max_length=settings.reranker_max_length,
        batch_size=settings.reranker_batch_size,
        max_candidates=settings.reranker_max_candidates,
        max_concurrency=settings.reranker_max_concurrency,
        local_files_only=settings.reranker_local_files_only,
        cache_folder=(
            str(settings.reranker_cache_folder)
            if settings.reranker_cache_folder is not None
            else None
        ),
        instruction=settings.reranker_instruction,
        queue_timeout_seconds=settings.reranker_timeout_seconds,
    )


def create_reranker_app(
    settings: Settings | None = None,
    *,
    provider_factory: ProviderFactory = build_cross_encoder,
) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = app_settings
        app.state.reranker_provider = None
        try:
            app.state.reranker_provider = await asyncio.to_thread(provider_factory, app_settings)
        except Exception as exc:
            logger.error("Reranker model load failed error_type=%s", type(exc).__name__)
        try:
            yield
        finally:
            provider = app.state.reranker_provider
            if provider is not None:
                try:
                    await provider.close()
                except Exception:
                    logger.warning("Reranker provider did not close cleanly")

    app = FastAPI(
        title="TraceMind Local Reranker",
        version=app_settings.app_version,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    @app.get("/health/live")
    async def live() -> dict[str, bool]:
        return {"live": True}

    @app.get("/health/ready")
    async def ready(request: Request) -> JSONResponse:
        provider = request.app.state.reranker_provider
        is_ready = provider is not None and provider.ready
        return JSONResponse(
            {"ready": is_ready},
            status_code=200 if is_ready else 503,
        )

    @app.post("/rerank", response_model=RerankResponse)
    async def rerank(body: RerankRequest, request: Request) -> RerankResponse:
        provider = request.app.state.reranker_provider
        if provider is None or not provider.ready:
            raise HTTPException(status_code=503, detail="Reranker is unavailable")
        started_at = perf_counter()
        try:
            results = await provider.rerank(
                body.query,
                [
                    RerankerCandidate(candidate.candidate_id, candidate.text)
                    for candidate in body.candidates
                ],
                limit=body.limit,
            )
        except InvalidRerankerInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RerankerUnavailableError as exc:
            raise HTTPException(status_code=503, detail="Reranker is unavailable") from exc
        return RerankResponse(
            model=app_settings.reranker_model_name,
            items=[
                RerankResultResponse(
                    candidate_id=result.candidate_id,
                    rank=result.rank,
                    score=result.score,
                )
                for result in results
            ],
            latency_ms=round((perf_counter() - started_at) * 1_000),
        )

    return app


app = create_reranker_app()
