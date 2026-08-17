from collections.abc import AsyncIterator
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from app.api.routes.consistency_repairs import get_repair_service
from app.core.config import Settings
from app.main import create_app
from app.schemas.consistency_repair import ConsistencyRepairResponse
from app.services.consistency_repair import ConsistencyRepairService


def make_app(service: AsyncMock) -> FastAPI:
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_repair_service] = lambda: service
    return app


async def request(app: FastAPI, method: str, path: str, **kwargs: object) -> Response:
    async def clients() -> AsyncIterator[AsyncClient]:
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                yield client

    async for client in clients():
        return await client.request(method, path, **kwargs)
    raise RuntimeError("Test client was not created")


async def test_repair_defaults_to_side_effect_free_dry_run() -> None:
    kb_id, audit_id, finding_id = uuid4(), uuid4(), uuid4()
    service = AsyncMock(spec=ConsistencyRepairService)
    service.start.return_value = ConsistencyRepairResponse(
        knowledge_base_id=kb_id,
        audit_id=audit_id,
        operation_id=None,
        dry_run=True,
        status="planned",
        items=[],
    )
    app = make_app(service)

    response = await request(
        app,
        "POST",
        f"/api/v1/knowledge-bases/{kb_id}/consistency-repair",
        json={
            "audit_id": str(audit_id),
            "knowledge_base_id": str(kb_id),
            "finding_ids": [str(finding_id)],
        },
    )

    assert response.status_code == 200
    submitted = service.start.await_args.args[0]
    assert submitted.dry_run is True
    assert response.json()["operation_id"] is None


async def test_repair_rejects_body_path_scope_mismatch() -> None:
    path_kb, body_kb = uuid4(), uuid4()
    service = AsyncMock(spec=ConsistencyRepairService)
    app = make_app(service)
    response = await request(
        app,
        "POST",
        f"/api/v1/knowledge-bases/{path_kb}/consistency-repair",
        json={
            "audit_id": str(uuid4()),
            "knowledge_base_id": str(body_kb),
            "finding_ids": [str(uuid4())],
        },
    )
    assert response.status_code == 422
    service.start.assert_not_awaited()


async def test_retry_dispatches_stale_repair_operation() -> None:
    kb_id, audit_id, operation_id = uuid4(), uuid4(), uuid4()
    service = AsyncMock(spec=ConsistencyRepairService)
    service.retry.return_value = ConsistencyRepairResponse(
        knowledge_base_id=kb_id,
        audit_id=audit_id,
        operation_id=operation_id,
        dry_run=False,
        status="queued",
        items=[],
    )
    app = make_app(service)

    response = await request(
        app,
        "POST",
        f"/api/v1/knowledge-bases/{kb_id}/consistency-repair/{operation_id}/retry",
    )

    assert response.status_code == 202
    service.retry.assert_awaited_once_with(kb_id, operation_id)
