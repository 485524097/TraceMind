import asyncio
import logging
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Annotated, Literal, TypedDict, cast

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import Settings

router = APIRouter(prefix="/health", tags=["health"])
logger = logging.getLogger(__name__)
HealthCheck = Callable[[], Awaitable[None]]


class LiveResponse(TypedDict):
    status: Literal["ok"]
    service: str
    version: str


async def check_postgres(request: Request) -> None:
    async with request.app.state.database.engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def check_redis(request: Request) -> None:
    if not await request.app.state.redis_client.ping():
        raise RuntimeError("Redis PING returned a false response")


async def check_qdrant(request: Request) -> None:
    await request.app.state.qdrant_client.check_connection()


def get_postgres_check(request: Request) -> HealthCheck:
    return partial(check_postgres, request)


def get_redis_check(request: Request) -> HealthCheck:
    return partial(check_redis, request)


def get_qdrant_check(request: Request) -> HealthCheck:
    return partial(check_qdrant, request)


def get_app_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


SettingsDependency = Annotated[Settings, Depends(get_app_settings)]
PostgresCheckDependency = Annotated[HealthCheck, Depends(get_postgres_check)]
RedisCheckDependency = Annotated[HealthCheck, Depends(get_redis_check)]
QdrantCheckDependency = Annotated[HealthCheck, Depends(get_qdrant_check)]


@router.get("/live")
async def live(settings: SettingsDependency) -> LiveResponse:
    return {"status": "ok", "service": settings.app_name, "version": settings.app_version}


@router.get("/ready")
async def ready(
    postgres_check: PostgresCheckDependency,
    redis_check: RedisCheckDependency,
    qdrant_check: QdrantCheckDependency,
) -> JSONResponse:
    checks = {
        "postgres": postgres_check,
        "redis": redis_check,
        "qdrant": qdrant_check,
    }
    results = await asyncio.gather(*(check() for check in checks.values()), return_exceptions=True)
    statuses: dict[str, str] = {}
    for component, result in zip(checks, results, strict=True):
        if isinstance(result, BaseException):
            statuses[component] = "error"
            logger.warning("Readiness check failed for %s (%s)", component, type(result).__name__)
        else:
            statuses[component] = "ok"

    is_ready = all(value == "ok" for value in statuses.values())
    return JSONResponse(
        status_code=status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "ok" if is_ready else "unavailable", "checks": statuses},
    )
