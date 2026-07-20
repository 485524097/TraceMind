from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.exc import OperationalError

from app.api.routes.knowledge_bases import get_knowledge_base_service
from app.core.config import Settings
from app.main import create_app
from app.models.knowledge_base import KnowledgeBase
from app.services.exceptions import (
    KnowledgeBaseNameConflictError,
    KnowledgeBaseNotEmptyError,
    KnowledgeBaseNotFoundError,
)
from app.services.knowledge_base import KnowledgeBaseService


def make_knowledge_base(name: str = "Backend Notes") -> KnowledgeBase:
    now = datetime.now(UTC)
    return KnowledgeBase(
        id=uuid4(),
        name=name,
        description="Notes",
        created_at=now,
        updated_at=now,
    )


def make_app(service: AsyncMock) -> FastAPI:
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_knowledge_base_service] = lambda: service
    return app


async def client_for(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def request(app: FastAPI, method: str, path: str, **kwargs: object) -> Response:
    async for client in client_for(app):
        return await client.request(method, path, **kwargs)
    raise RuntimeError("Test client was not created")


def make_service() -> AsyncMock:
    return AsyncMock(spec=KnowledgeBaseService)


async def test_create_returns_201() -> None:
    service = make_service()
    knowledge_base = make_knowledge_base()
    service.create.return_value = knowledge_base

    response = await request(
        make_app(service),
        "POST",
        "/api/v1/knowledge-bases",
        json={"name": "Backend Notes", "description": "Notes"},
    )

    assert response.status_code == 201
    assert response.json()["id"] == str(knowledge_base.id)


async def test_list_returns_pagination() -> None:
    service = make_service()
    service.list.return_value = ([make_knowledge_base()], 1)

    response = await request(make_app(service), "GET", "/api/v1/knowledge-bases?offset=0&limit=20")

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["limit"] == 20


async def test_get_detail() -> None:
    service = make_service()
    knowledge_base = make_knowledge_base()
    service.get.return_value = knowledge_base

    response = await request(
        make_app(service), "GET", f"/api/v1/knowledge-bases/{knowledge_base.id}"
    )

    assert response.status_code == 200
    assert response.json()["name"] == knowledge_base.name


async def test_update_knowledge_base() -> None:
    service = make_service()
    knowledge_base = make_knowledge_base("Updated")
    service.update.return_value = knowledge_base

    response = await request(
        make_app(service),
        "PATCH",
        f"/api/v1/knowledge-bases/{knowledge_base.id}",
        json={"name": "Updated"},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Updated"


async def test_delete_returns_204_without_body() -> None:
    service = make_service()
    knowledge_base_id = uuid4()

    response = await request(
        make_app(service), "DELETE", f"/api/v1/knowledge-bases/{knowledge_base_id}"
    )

    assert response.status_code == 204
    assert response.content == b""


@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
async def test_missing_knowledge_base_returns_404(method: str) -> None:
    service = make_service()
    knowledge_base_id = uuid4()
    error = KnowledgeBaseNotFoundError(knowledge_base_id)
    service_method = getattr(
        service,
        {"GET": "get", "PATCH": "update", "DELETE": "delete"}[method],
    )
    service_method.side_effect = error
    kwargs = {"json": {"name": "Updated"}} if method == "PATCH" else {}

    response = await request(
        make_app(service), method, f"/api/v1/knowledge-bases/{knowledge_base_id}", **kwargs
    )

    assert response.status_code == 404


async def test_name_conflict_returns_409() -> None:
    service = make_service()
    service.create.side_effect = KnowledgeBaseNameConflictError("Existing")

    response = await request(
        make_app(service), "POST", "/api/v1/knowledge-bases", json={"name": "Existing"}
    )

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


async def test_non_empty_knowledge_base_delete_returns_409() -> None:
    service = make_service()
    knowledge_base_id = uuid4()
    service.delete.side_effect = KnowledgeBaseNotEmptyError(knowledge_base_id)

    response = await request(
        make_app(service), "DELETE", f"/api/v1/knowledge-bases/{knowledge_base_id}"
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Knowledge base must be empty before deletion"


@pytest.mark.parametrize(
    "payload",
    [{"name": "   "}, {"name": "x" * 101}, {}],
)
async def test_invalid_create_payload_returns_422(payload: dict[str, object]) -> None:
    response = await request(
        make_app(make_service()), "POST", "/api/v1/knowledge-bases", json=payload
    )
    assert response.status_code == 422


async def test_empty_update_and_invalid_uuid_return_422() -> None:
    app = make_app(make_service())
    empty_update = await request(app, "PATCH", f"/api/v1/knowledge-bases/{uuid4()}", json={})
    invalid_uuid = await request(app, "GET", "/api/v1/knowledge-bases/not-a-uuid")

    assert empty_update.status_code == 422
    assert invalid_uuid.status_code == 422


async def test_database_error_returns_generic_500() -> None:
    service = make_service()
    service.list.side_effect = OperationalError(
        "SELECT secret-password FROM private_table",
        {},
        Exception("postgresql://private-connection"),
    )

    response = await request(make_app(service), "GET", "/api/v1/knowledge-bases")

    assert response.status_code == 500
    assert "secret-password" not in response.text
    assert "private-connection" not in response.text
