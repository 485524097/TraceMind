import asyncio

from qdrant_client import AsyncQdrantClient

from app.core.config import Settings


class QdrantClient:
    def __init__(self, settings: Settings) -> None:
        self.healthcheck_timeout = settings.healthcheck_timeout_seconds
        self.client = AsyncQdrantClient(
            url=settings.qdrant_url,
            timeout=settings.qdrant_operation_timeout_seconds,
            check_compatibility=False,
            trust_env=False,
        )

    async def check_connection(self) -> None:
        await asyncio.wait_for(
            self.client.get_collections(),
            timeout=self.healthcheck_timeout,
        )

    async def close(self) -> None:
        await self.client.close()
