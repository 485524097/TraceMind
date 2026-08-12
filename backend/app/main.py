import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import Database
from app.embedding import EmbeddingError, SentenceTransformerEmbeddingProvider
from app.integrations.qdrant import QdrantClient
from app.integrations.redis import RedisClient
from app.llm import OpenAICompatibleLLMProvider
from app.reranker import HttpRerankerProvider

logger = logging.getLogger(__name__)


async def prewarm_embedding_provider(provider: SentenceTransformerEmbeddingProvider) -> None:
    try:
        await asyncio.to_thread(provider.warmup)
        logger.info(
            "Query embedding model prewarmed model=%s device=%s",
            provider.model_name,
            provider.device,
        )
    except EmbeddingError:
        logger.warning(
            "Query embedding model prewarm failed model=%s device=%s",
            provider.model_name,
            provider.device,
            exc_info=True,
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = app_settings
        app.state.database = Database(app_settings)
        app.state.redis_client = RedisClient(app_settings)
        app.state.qdrant_client = QdrantClient(app_settings)
        app.state.embedding_provider = SentenceTransformerEmbeddingProvider(
            app_settings.embedding_model_name,
            app_settings.embedding_dimension,
            app_settings.embedding_batch_size,
            app_settings.resolved_query_embedding_device,
        )
        app.state.embedding_warmup_task = (
            asyncio.create_task(prewarm_embedding_provider(app.state.embedding_provider))
            if app_settings.rag_llm_enabled and app_settings.app_env.lower() != "test"
            else None
        )
        app.state.reranker_provider = (
            HttpRerankerProvider(
                app_settings.reranker_base_url,
                read_timeout_seconds=app_settings.reranker_timeout_seconds,
                max_candidates=app_settings.reranker_max_candidates,
            )
            if app_settings.reranker_enabled
            else None
        )
        app.state.llm_provider = (
            OpenAICompatibleLLMProvider(
                base_url=app_settings.llm_base_url or "",
                api_key=(
                    app_settings.llm_api_key.get_secret_value()
                    if app_settings.llm_api_key is not None
                    else None
                ),
                model=app_settings.llm_model or "",
                timeout=app_settings.llm_timeout_seconds,
                temperature=app_settings.llm_temperature,
                max_tokens=app_settings.llm_max_tokens,
                enable_thinking=app_settings.llm_enable_thinking,
            )
            if app_settings.rag_llm_enabled
            else None
        )
        try:
            yield
        finally:
            if app.state.embedding_warmup_task is not None:
                app.state.embedding_warmup_task.cancel()
                with suppress(asyncio.CancelledError):
                    await app.state.embedding_warmup_task
            if app.state.reranker_provider is not None:
                try:
                    await app.state.reranker_provider.close()
                except Exception:
                    logger.warning("Reranker provider did not close cleanly")
            if app.state.llm_provider is not None:
                try:
                    await app.state.llm_provider.close()
                except Exception:
                    logger.warning("LLM provider did not close cleanly")
            await app.state.qdrant_client.close()
            await app.state.redis_client.close()
            await app.state.database.close()

    docs_url = "/docs" if app_settings.app_env.lower() == "development" else None
    app = FastAPI(
        title=app_settings.app_name,
        version=app_settings.app_version,
        docs_url=docs_url,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router, prefix=app_settings.api_v1_prefix)
    return app


app = create_app()
