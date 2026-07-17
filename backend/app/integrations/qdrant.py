import asyncio

from qdrant_client import AsyncQdrantClient

from app.core.config import Settings


class QdrantClient:
    def __init__(self, settings: Settings) -> None:
        self.timeout = settings.healthcheck_timeout_seconds
        self.client = AsyncQdrantClient(
            url=settings.qdrant_url,
            timeout=self.timeout,
            check_compatibility=False,
            trust_env=False,
        )

    async def check_connection(self) -> None:
        await asyncio.wait_for(self.client.get_collections(), timeout=self.timeout)

    async def close(self) -> None:
        await self.client.close()
