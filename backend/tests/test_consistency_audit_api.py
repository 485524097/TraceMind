from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from app.api.routes.consistency_audits import get_consistency_audit_service
from app.core.config import Settings
from app.main import create_app
from app.schemas.consistency_audit import (
    AuditScope,
    ConsistencyAuditResponse,
    ConsistencyAuditSummary,
)
from app.services.consistency_audit import ConsistencyAuditService
from app.services.exceptions import KnowledgeBaseNotFoundError


def make_app(service: AsyncMock) -> FastAPI:
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_consistency_audit_service] = lambda: service
    return app


async def request(app: FastAPI, method: str, path: str) -> Response:
    async def clients() -> AsyncIterator[AsyncClient]:
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                yield client

    async for client in clients():
        return await client.request(method, path)
    raise RuntimeError("Test client was not created")


def report(knowledge_base_id: UUID | None, scope: AuditScope) -> ConsistencyAuditResponse:
    now = datetime.now(UTC)
    return ConsistencyAuditResponse(
        audit_id=uuid4(),
        scope=scope,
        status="completed",
        knowledge_base_id=knowledge_base_id,
        started_at=now,
        completed_at=now,
        summary=ConsistencyAuditSummary(
            healthy=True,
            warning_count=0,
            error_count=0,
            critical_count=0,
        ),
        findings=[],
    )


async def test_single_and_global_consistency_audit_routes() -> None:
    knowledge_base_id = uuid4()
    service = AsyncMock(spec=ConsistencyAuditService)
    service.audit_knowledge_base.return_value = report(knowledge_base_id, "knowledge_base")
    service.audit_all.return_value = report(None, "global")
    app = make_app(service)

    single = await request(
        app,
        "POST",
        f"/api/v1/knowledge-bases/{knowledge_base_id}/consistency-audit",
    )
    global_report = await request(app, "POST", "/api/v1/consistency-audit")

    assert single.status_code == global_report.status_code == 200
    assert single.json()["scope"] == "knowledge_base"
    assert global_report.json()["scope"] == "global"
    service.audit_knowledge_base.assert_awaited_once_with(knowledge_base_id)
    service.audit_all.assert_awaited_once_with()


async def test_single_audit_maps_missing_knowledge_base() -> None:
    knowledge_base_id = uuid4()
    service = AsyncMock(spec=ConsistencyAuditService)
    service.audit_knowledge_base.side_effect = KnowledgeBaseNotFoundError(knowledge_base_id)
    app = make_app(service)

    response = await request(
        app,
        "POST",
        f"/api/v1/knowledge-bases/{knowledge_base_id}/consistency-audit",
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Knowledge base not found"}
