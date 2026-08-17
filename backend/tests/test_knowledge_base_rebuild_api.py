from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from app.api.routes.rebuilds import get_rebuild_service
from app.core.config import Settings
from app.main import create_app
from app.schemas.knowledge_base_rebuild import KnowledgeBaseRebuildResponse
from app.services.exceptions import (
    KnowledgeBaseNotFoundError,
    KnowledgeBaseRebuildAlreadyActiveError,
    KnowledgeBaseRebuildNotRetryableError,
)
from app.services.knowledge_base_rebuild import KnowledgeBaseRebuildService


def make_app(service: AsyncMock) -> FastAPI:
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_rebuild_service] = lambda: service
    return app


async def client_for(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


async def request(app: FastAPI, method: str, path: str) -> Response:
    async for client in client_for(app):
        return await client.request(method, path)
    raise RuntimeError("Test client was not created")


def response(knowledge_base_id: object, status: str) -> KnowledgeBaseRebuildResponse:
    return KnowledgeBaseRebuildResponse(
        knowledge_base_id=knowledge_base_id,
        operation_id=uuid4() if status != "not_started" else None,
        status=status,
        document_versions_total=3,
        document_versions_parsed=1,
        documents_total=1,
        knowledge_entries_total=1,
        started_at=datetime.now(UTC) if status != "not_started" else None,
    )


async def test_start_status_and_retry_routes() -> None:
    knowledge_base_id = uuid4()
    service = AsyncMock(spec=KnowledgeBaseRebuildService)
    service.start.return_value = response(knowledge_base_id, "queued")
    service.get_status.return_value = response(knowledge_base_id, "running")
    service.retry.return_value = response(knowledge_base_id, "queued")
    app = make_app(service)

    started = await request(app, "POST", f"/api/v1/knowledge-bases/{knowledge_base_id}/rebuild")
    current = await request(app, "GET", f"/api/v1/knowledge-bases/{knowledge_base_id}/rebuild")
    retried = await request(
        app, "POST", f"/api/v1/knowledge-bases/{knowledge_base_id}/rebuild/retry"
    )

    assert started.status_code == retried.status_code == 202
    assert started.json()["status"] == retried.json()["status"] == "queued"
    assert current.status_code == 200
    assert current.json()["status"] == "running"
    assert current.json()["document_versions_total"] == 3


async def test_rebuild_api_maps_not_found_and_conflicts() -> None:
    knowledge_base_id = uuid4()
    service = AsyncMock(spec=KnowledgeBaseRebuildService)
    service.start.side_effect = KnowledgeBaseNotFoundError(knowledge_base_id)
    service.get_status.side_effect = KnowledgeBaseRebuildAlreadyActiveError(knowledge_base_id)
    service.retry.side_effect = KnowledgeBaseRebuildNotRetryableError(knowledge_base_id)
    app = make_app(service)

    missing = await request(app, "POST", f"/api/v1/knowledge-bases/{knowledge_base_id}/rebuild")
    retry = await request(app, "POST", f"/api/v1/knowledge-bases/{knowledge_base_id}/rebuild/retry")

    assert missing.status_code == 404
    assert retry.status_code == 409
