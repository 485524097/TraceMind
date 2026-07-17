from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import Database
from app.integrations.qdrant import QdrantClient
from app.integrations.redis import RedisClient


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = app_settings
        app.state.database = Database(app_settings)
        app.state.redis_client = RedisClient(app_settings)
        app.state.qdrant_client = QdrantClient(app_settings)
        try:
            yield
        finally:
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
