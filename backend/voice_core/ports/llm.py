from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from voice_core.ports.types import ChatMessage, LLMEvent, ToolSpec


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    def stream(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        *,
        temperature: float,
        max_tokens: int,
        timeout_s: float,
    ) -> AsyncIterator[LLMEvent]: ...
