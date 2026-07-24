import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from openai import AsyncOpenAI

from app.llm import LLMMessage, LLMProviderError, OpenAICompatibleLLMProvider


class FakeUpstream:
    def __init__(self, chunks: list[object]) -> None:
        self.chunks = chunks
        self.close = AsyncMock()

    def __aiter__(self) -> Any:
        async def iterator() -> Any:
            for chunk in self.chunks:
                yield chunk

        return iterator()


def fake_client(upstream: object) -> Any:
    create = AsyncMock(return_value=upstream)
    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        close=AsyncMock(),
    )


async def test_openai_provider_passes_parameters_extracts_deltas_and_closes() -> None:
    upstream = FakeUpstream(
        [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content="hello"),
                        finish_reason=None,
                    )
                ]
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=None),
                        finish_reason="stop",
                    )
                ]
            ),
        ]
    )
    client = fake_client(upstream)
    provider = OpenAICompatibleLLMProvider(
        base_url="http://localhost/v1",
        api_key=None,
        model="local-model",
        timeout=12,
        temperature=0.2,
        max_tokens=300,
        client=cast(AsyncOpenAI, client),
    )
    stream = await provider.stream([LLMMessage(role="user", content="question")])
    deltas = [delta async for delta in stream]

    assert [delta.text for delta in deltas] == ["hello", ""]
    assert deltas[-1].finish_reason == "stop"
    client.chat.completions.create.assert_awaited_once_with(
        model="local-model",
        messages=[{"role": "user", "content": "question"}],
        stream=True,
        temperature=0.2,
        max_tokens=300,
    )
    upstream.close.assert_awaited_once()


async def test_openai_provider_converts_start_error_to_safe_exception() -> None:
    client = fake_client(None)
    client.chat.completions.create.side_effect = TimeoutError("secret upstream response")
    provider = OpenAICompatibleLLMProvider(
        base_url="http://localhost/v1?token=private",
        api_key="private-key",
        model="model",
        timeout=1,
        temperature=0,
        max_tokens=1,
        client=cast(AsyncOpenAI, client),
    )
    with pytest.raises(LLMProviderError, match="could not be started") as caught:
        await provider.stream([LLMMessage(role="user", content="x")])
    assert "private" not in str(caught.value)


async def test_openai_provider_ignores_empty_delta_and_closes_on_cancellation() -> None:
    ready = asyncio.Event()

    class BlockingUpstream(FakeUpstream):
        def __aiter__(self) -> Any:
            async def iterator() -> Any:
                ready.set()
                await asyncio.Event().wait()
                yield None

            return iterator()

    upstream = BlockingUpstream([])
    client = fake_client(upstream)
    provider = OpenAICompatibleLLMProvider(
        base_url="http://localhost/v1",
        api_key=None,
        model="model",
        timeout=1,
        temperature=0,
        max_tokens=1,
        client=cast(AsyncOpenAI, client),
    )
    stream = await provider.stream([LLMMessage(role="user", content="x")])
    task = asyncio.create_task(anext(stream))
    await ready.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    upstream.close.assert_awaited_once()
