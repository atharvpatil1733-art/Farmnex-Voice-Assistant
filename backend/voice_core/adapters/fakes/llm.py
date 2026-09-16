from __future__ import annotations

from collections.abc import AsyncIterator

from voice_core.ports.types import ChatMessage, Done, LLMEvent, ToolSpec, Usage


class FakeLLM:
    """Scriptable fake: replays a fixed sequence of events for each call to stream()."""

    name = "fake"

    def __init__(self, scripted_events: list[LLMEvent] | None = None) -> None:
        self._scripted_events = scripted_events or [Done(usage=Usage(0, 0))]
        self.calls: list[list[ChatMessage]] = []

    async def stream(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        *,
        temperature: float,
        max_tokens: int,
        timeout_s: float,
    ) -> AsyncIterator[LLMEvent]:
        self.calls.append(messages)
        for event in self._scripted_events:
            yield event
