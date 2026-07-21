from collections.abc import Awaitable
from unittest.mock import AsyncMock, patch

import pytest
from qdrant_client import AsyncQdrantClient

from app.core.config import Settings
from app.integrations.qdrant import QdrantClient


async def test_operation_and_healthcheck_timeouts_are_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncMock(spec=AsyncQdrantClient)
    captured_timeout: list[float | None] = []

    async def wait_for(awaitable: Awaitable[object], **options: float | None) -> object:
        captured_timeout.append(options["timeout"])
        return await awaitable

    monkeypatch.setattr("app.integrations.qdrant.asyncio.wait_for", wait_for)
    settings = Settings(
        healthcheck_timeout_seconds=3,
        qdrant_operation_timeout_seconds=75,
    )
    with patch("app.integrations.qdrant.AsyncQdrantClient", return_value=client) as factory:
        qdrant = QdrantClient(settings)

    assert factory.call_args.kwargs["timeout"] == 75
    assert factory.call_args.kwargs["check_compatibility"] is False
    assert factory.call_args.kwargs["trust_env"] is False

    await qdrant.check_connection()

    assert captured_timeout == [3]
    client.get_collections.assert_awaited_once_with()
