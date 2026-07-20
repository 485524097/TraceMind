from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.indexing import get_document_indexing_service
from app.core.config import Settings
from app.main import create_app
from app.models.document import DocumentVersion
from app.services.document_indexing import (
    DocumentIndexingService,
    IndexRequestResult,
    SemanticSearchResult,
)
from app.services.exceptions import SemanticSearchUnavailableError


async def client_for(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


def make_app(service: AsyncMock) -> FastAPI:
    app = create_app(Settings(app_env="test", embedding_dimension=3))
    app.dependency_overrides[get_document_indexing_service] = lambda: service
    return app


def make_version() -> DocumentVersion:
    now = datetime.now(UTC)
    return DocumentVersion(
        id=uuid4(),
        document_id=uuid4(),
        version_number=1,
        content_hash="a" * 64,
        file_size=7,
        mime_type="text/markdown",
        extension=".md",
        storage_path="hidden/content.md",
        parse_status="succeeded",
        chunk_count=1,
        index_status="pending",
        active_index_generation=None,
        index_started_at=None,
        indexed_at=None,
        last_index_attempt_at=None,
        indexed_chunk_count=0,
        embedding_model=None,
        embedding_dimension=None,
        index_error_code=None,
        index_error_message=None,
        created_at=now,
    )


async def test_index_request_body_and_status_api() -> None:
    service = AsyncMock(spec=DocumentIndexingService)
    version = make_version()
    service.request_index.return_value = IndexRequestResult(True, version)
    service.get_status.return_value = version
    knowledge_base_id = uuid4()
    base = (
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents/{version.document_id}"
        f"/versions/{version.id}"
    )
    app = make_app(service)

    async for client in client_for(app):
        queued = await client.post(f"{base}/index", json={"force": True})
        current = await client.get(f"{base}/index-status")

    assert queued.status_code == 202
    assert queued.json()["version"]["index_status"] == "pending"
    assert current.status_code == 200
    service.request_index.assert_awaited_once_with(
        knowledge_base_id, version.document_id, version.id, force=True
    )


async def test_semantic_search_validation_and_traceable_response() -> None:
    service = AsyncMock(spec=DocumentIndexingService)
    knowledge_base_id = uuid4()
    document_id, version_id, chunk_id, generation = uuid4(), uuid4(), uuid4(), uuid4()
    service.search.return_value = [
        SemanticSearchResult(
            score=0.91,
            content="service content",
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            document_version_id=version_id,
            chunk_id=chunk_id,
            index_generation=generation,
            document_name="sample.md",
            version_number=2,
            chunk_index=3,
            content_hash="a" * 64,
            chunk_type="code",
            language="python",
            section_title="Service",
            page_number=None,
            start_line=10,
            end_line=14,
        )
    ]
    app = make_app(service)
    path = f"/api/v1/knowledge-bases/{knowledge_base_id}/search/semantic"

    async for client in client_for(app):
        response = await client.post(
            path,
            json={"query": " service layer ", "limit": 5, "language": "python"},
        )
        blank = await client.post(path, json={"query": " "})
        too_many = await client.post(path, json={"query": "test", "limit": 51})

    assert response.status_code == 200
    assert response.json()["items"][0]["start_line"] == 10
    assert response.json()["items"][0]["document_name"] == "sample.md"
    assert blank.status_code == too_many.status_code == 422
    service.search.assert_awaited_once_with(
        knowledge_base_id,
        query="service layer",
        limit=5,
        language="python",
        document_id=None,
    )


async def test_qdrant_unavailable_returns_controlled_503() -> None:
    service = AsyncMock(spec=DocumentIndexingService)
    service.search.side_effect = SemanticSearchUnavailableError("Semantic search is unavailable")
    app = make_app(service)

    async for client in client_for(app):
        response = await client.post(
            f"/api/v1/knowledge-bases/{uuid4()}/search/semantic",
            json={"query": "test"},
        )

    assert response.status_code == 503
    assert "private" not in response.text
