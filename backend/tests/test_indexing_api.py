from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.indexing import (
    get_document_indexing_service,
    get_document_reranking_service,
)
from app.core.config import Settings
from app.main import create_app
from app.models.document import DocumentVersion
from app.reranker import RerankerUnavailableError
from app.services.document_indexing import (
    DocumentIndexingService,
    IndexRequestResult,
    SemanticSearchResult,
)
from app.services.document_reranking import DocumentRerankingService
from app.services.exceptions import HybridSearchUnavailableError, SemanticSearchUnavailableError


async def client_for(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


def make_app(
    service: AsyncMock,
    *,
    reranking_service: AsyncMock | None = None,
) -> FastAPI:
    app = create_app(
        Settings(
            _env_file=None,
            app_env="test",
            embedding_dimension=3,
            reranker_enabled=reranking_service is not None,
        )
    )
    app.dependency_overrides[get_document_indexing_service] = lambda: service
    if reranking_service is not None:
        app.dependency_overrides[get_document_reranking_service] = lambda: reranking_service
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


async def test_semantic_search_empty_results_return_success() -> None:
    service = AsyncMock(spec=DocumentIndexingService)
    service.search.return_value = []
    knowledge_base_id = uuid4()
    app = make_app(service)

    async for client in client_for(app):
        response = await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/search/semantic",
            json={"query": "unanswered question", "limit": 5},
        )

    assert response.status_code == 200
    assert response.json() == {"items": []}


async def test_hybrid_search_path_and_safe_error() -> None:
    service = AsyncMock(spec=DocumentIndexingService)
    service.hybrid_search.return_value = []
    knowledge_base_id = uuid4()
    app = make_app(service)
    path = f"/api/v1/knowledge-bases/{knowledge_base_id}/search/hybrid"

    async for client in client_for(app):
        response = await client.post(
            path,
            json={"query": "DiscoveryClient", "limit": 5, "language": "java"},
        )

    assert response.status_code == 200
    assert response.json() == {"items": []}
    service.hybrid_search.assert_awaited_once_with(
        knowledge_base_id,
        query="DiscoveryClient",
        limit=5,
        language="java",
        document_id=None,
    )

    service.hybrid_search.side_effect = HybridSearchUnavailableError("Hybrid search is unavailable")
    async for client in client_for(app):
        failed = await client.post(path, json={"query": "private query"})
    assert failed.status_code == 503
    assert "private query" not in failed.text


async def test_reranked_search_returns_raw_and_original_rrf_scores() -> None:
    indexing = AsyncMock(spec=DocumentIndexingService)
    reranking = AsyncMock(spec=DocumentRerankingService)
    knowledge_base_id = uuid4()
    candidate = SemanticSearchResult(
        score=0.7,
        content="DiscoveryClient",
        knowledge_base_id=knowledge_base_id,
        document_id=uuid4(),
        document_version_id=uuid4(),
        chunk_id=uuid4(),
        index_generation=uuid4(),
        document_name="service.java",
        version_number=1,
        chunk_index=1,
        content_hash="a" * 64,
        chunk_type="code",
        language="java",
        section_title="Discovery",
        page_number=None,
        start_line=1,
        end_line=2,
        ranking_mode="hybrid",
        retrieval_score=0.7,
        retrieval_rank=1,
    )
    indexing.hybrid_search.return_value = [candidate]
    reranking.rerank.return_value = [
        replace(
            candidate,
            score=6.1,
            rerank_score=6.1,
            ranking_mode="reranker",
        )
    ]
    app = make_app(indexing, reranking_service=reranking)
    path = f"/api/v1/knowledge-bases/{knowledge_base_id}/search/reranked"

    async for client in client_for(app):
        response = await client.post(
            path,
            json={"query": "DiscoveryClient", "limit": 1},
        )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["score"] == item["rerank_score"] == 6.1
    assert item["retrieval_score"] == 0.7
    assert item["retrieval_rank"] == 1
    assert item["ranking_mode"] == "reranker"
    indexing.hybrid_search.assert_awaited_once_with(
        knowledge_base_id,
        query="DiscoveryClient",
        limit=10,
        language=None,
        document_id=None,
    )


async def test_reranked_search_disabled_or_unavailable_returns_503() -> None:
    indexing = AsyncMock(spec=DocumentIndexingService)
    disabled = make_app(indexing)
    path = f"/api/v1/knowledge-bases/{uuid4()}/search/reranked"
    async for client in client_for(disabled):
        response = await client.post(path, json={"query": "query", "limit": 1})
    assert response.status_code == 503

    reranking = AsyncMock(spec=DocumentRerankingService)
    indexing.hybrid_search.return_value = [
        SemanticSearchResult(
            score=0.7,
            content="content",
            knowledge_base_id=uuid4(),
            document_id=uuid4(),
            document_version_id=uuid4(),
            chunk_id=uuid4(),
            index_generation=uuid4(),
            document_name="doc.md",
            version_number=1,
            chunk_index=1,
            content_hash="a" * 64,
            chunk_type="paragraph",
            language=None,
            section_title=None,
            page_number=None,
            start_line=1,
            end_line=1,
        )
    ]
    reranking.rerank.side_effect = RerankerUnavailableError(reason="timeout")
    unavailable = make_app(indexing, reranking_service=reranking)
    async for client in client_for(unavailable):
        failed = await client.post(path, json={"query": "private query", "limit": 1})
    assert failed.status_code == 503
    assert "private query" not in failed.text
