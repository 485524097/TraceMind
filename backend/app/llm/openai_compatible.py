import asyncio
from collections.abc import AsyncGenerator
from typing import Any, cast

from openai import AsyncOpenAI

from app.llm.base import LLMMessage, LLMProviderError, LLMStreamDelta


class OpenAICompatibleLLMProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout: float,
        temperature: float,
        max_tokens: int,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.client = client or AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "not-required",
            timeout=timeout,
        )

    async def stream(self, messages: list[LLMMessage]) -> AsyncGenerator[LLMStreamDelta]:
        try:
            upstream = await self.client.chat.completions.create(
                model=self.model,
                messages=cast(
                    Any,
                    [{"role": item.role, "content": item.content} for item in messages],
                ),
                stream=True,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
        except Exception as exc:
            raise LLMProviderError("LLM stream could not be started") from exc

        async def deltas() -> AsyncGenerator[LLMStreamDelta]:
            try:
                async for chunk in upstream:
                    choice: Any = chunk.choices[0] if chunk.choices else None
                    if choice is None:
                        continue
                    text = choice.delta.content or ""
                    finish_reason = choice.finish_reason
                    if text or finish_reason:
                        yield LLMStreamDelta(text=text, finish_reason=finish_reason)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise LLMProviderError("LLM stream was interrupted") from exc
            finally:
                try:
                    await upstream.close()
                except Exception:
                    pass

        return deltas()

    async def close(self) -> None:
        await self.client.close()
