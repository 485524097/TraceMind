from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Literal, Protocol


class LLMProviderError(RuntimeError):
    """Safe provider error that contains no upstream response details."""


@dataclass(frozen=True)
class LLMMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class LLMStreamDelta:
    text: str = ""
    finish_reason: str | None = None


class LLMProvider(Protocol):
    async def stream(self, messages: list[LLMMessage]) -> AsyncGenerator[LLMStreamDelta]: ...

    async def close(self) -> None: ...
