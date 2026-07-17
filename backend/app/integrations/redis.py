from redis.asyncio import Redis

from app.core.config import Settings


class RedisClient:
    def __init__(self, settings: Settings) -> None:
        self.client: Redis = Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=settings.healthcheck_timeout_seconds,
            socket_timeout=settings.healthcheck_timeout_seconds,
        )

    async def ping(self) -> bool:
        return bool(await self.client.ping())

    async def close(self) -> None:
        await self.client.aclose()
