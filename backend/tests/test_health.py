from collections.abc import Awaitable, Callable

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from app.api.routes.health import get_postgres_check, get_qdrant_check, get_redis_check
from app.core.config import Settings
from app.main import create_app

HealthCheck = Callable[[], Awaitable[None]]


async def successful_check() -> None:
    return None


def create_test_app(
    *,
    postgres: HealthCheck = successful_check,
    redis: HealthCheck = successful_check,
    qdrant: HealthCheck = successful_check,
    settings: Settings | None = None,
) -> FastAPI:
    app = create_app(settings or Settings(app_env="test"))
    app.dependency_overrides[get_postgres_check] = lambda: postgres
    app.dependency_overrides[get_redis_check] = lambda: redis
    app.dependency_overrides[get_qdrant_check] = lambda: qdrant
    return app


async def get(app: FastAPI, path: str) -> Response:
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)


async def test_live_returns_expected_response() -> None:
    response = await get(create_test_app(), "/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "TraceMind API",
        "version": "0.1.0",
    }


async def test_ready_returns_ok_when_all_dependencies_are_available() -> None:
    response = await get(create_test_app(), "/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"postgres": "ok", "redis": "ok", "qdrant": "ok"},
    }


@pytest.mark.parametrize("failed_component", ["postgres", "redis", "qdrant"])
async def test_ready_returns_503_without_internal_details(failed_component: str) -> None:
    async def failed_check() -> None:
        raise RuntimeError("secret-password postgresql://private-connection")

    checks: dict[str, HealthCheck] = {
        "postgres": successful_check,
        "redis": successful_check,
        "qdrant": successful_check,
    }
    checks[failed_component] = failed_check
    app = create_test_app(
        postgres=checks["postgres"],
        redis=checks["redis"],
        qdrant=checks["qdrant"],
    )
    response = await get(app, "/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"][failed_component] == "error"
    assert "secret-password" not in response.text
    assert "private-connection" not in response.text


async def test_settings_can_be_overridden() -> None:
    settings = Settings(app_name="TraceMind Test API", app_version="9.9.9", app_env="test")
    response = await get(create_test_app(settings=settings), "/api/v1/health/live")

    assert response.json()["service"] == "TraceMind Test API"
    assert response.json()["version"] == "9.9.9"
