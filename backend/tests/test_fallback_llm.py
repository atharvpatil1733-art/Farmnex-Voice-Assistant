from __future__ import annotations

from collections.abc import AsyncIterator

from voice_core.adapters.fallback.llm import FallbackLLM
from voice_core.ports.types import (
    ChatMessage,
    Done,
    LLMError,
    LLMEvent,
    TextDelta,
    ToolCall,
    ToolSpec,
    Usage,
)

_MESSAGES = [ChatMessage(role="user", content="hi")]


class _ScriptedLLM:
    def __init__(self, name: str, events: list[LLMEvent]) -> None:
        self.name = name
        self._events = events
        self.calls = 0

    async def stream(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        *,
        temperature: float,
        max_tokens: int,
        timeout_s: float,
    ) -> AsyncIterator[LLMEvent]:
        self.calls += 1
        for event in self._events:
            yield event


async def _run(llm: FallbackLLM) -> list[LLMEvent]:
    return [
        e async for e in llm.stream(_MESSAGES, [], temperature=0.0, max_tokens=50, timeout_s=5.0)
    ]


async def test_first_provider_succeeds_second_never_called() -> None:
    first = _ScriptedLLM("first", [TextDelta(text="hi"), Done(usage=Usage(0, 0))])
    second = _ScriptedLLM("second", [TextDelta(text="unused"), Done(usage=Usage(0, 0))])
    llm = FallbackLLM([("first", first), ("second", second)])

    events = await _run(llm)

    assert events == [TextDelta(text="hi"), Done(usage=Usage(0, 0))]
    assert first.calls == 1
    assert second.calls == 0


async def test_falls_through_on_pre_output_error() -> None:
    first = _ScriptedLLM("first", [LLMError(code="rate_limited", message="429", retryable=True)])
    second = _ScriptedLLM("second", [TextDelta(text="from second"), Done(usage=Usage(0, 0))])
    llm = FallbackLLM([("first", first), ("second", second)])

    events = await _run(llm)

    assert events == [TextDelta(text="from second"), Done(usage=Usage(0, 0))]
    assert first.calls == 1
    assert second.calls == 1


async def test_does_not_fall_through_after_output_started() -> None:
    error = LLMError(code="timeout", message="dropped mid-stream", retryable=True)
    first = _ScriptedLLM("first", [TextDelta(text="partial"), error])
    second = _ScriptedLLM("second", [TextDelta(text="unused"), Done(usage=Usage(0, 0))])
    llm = FallbackLLM([("first", first), ("second", second)])

    events = await _run(llm)

    assert events == [TextDelta(text="partial"), error]
    assert first.calls == 1
    assert second.calls == 0


async def test_all_providers_fail_yields_last_error() -> None:
    err1 = LLMError(code="rate_limited", message="1", retryable=True)
    err2 = LLMError(code="unavailable", message="2", retryable=True)
    first = _ScriptedLLM("first", [err1])
    second = _ScriptedLLM("second", [err2])
    llm = FallbackLLM([("first", first), ("second", second)])

    events = await _run(llm)

    assert events == [err2]
    assert first.calls == 1
    assert second.calls == 1


class _RaisingLLM:
    """A provider that raises instead of yielding LLMError — the exact adapter bug that
    killed a real eval run before FallbackLLM guarded against it."""

    def __init__(self, name: str, exc: Exception, emit_first: LLMEvent | None = None) -> None:
        self.name = name
        self._exc = exc
        self._emit_first = emit_first
        self.calls = 0

    async def stream(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        *,
        temperature: float,
        max_tokens: int,
        timeout_s: float,
    ) -> AsyncIterator[LLMEvent]:
        self.calls += 1
        if self._emit_first is not None:
            yield self._emit_first
        raise self._exc


async def test_provider_that_raises_falls_through_to_next_provider() -> None:
    first = _RaisingLLM("first", ConnectionError("getaddrinfo failed"))
    second = _ScriptedLLM("second", [TextDelta(text="from second"), Done(usage=Usage(0, 0))])
    llm = FallbackLLM([("first", first), ("second", second)])

    events = await _run(llm)

    assert events == [TextDelta(text="from second"), Done(usage=Usage(0, 0))]
    assert first.calls == 1
    assert second.calls == 1


async def test_provider_that_raises_after_output_does_not_fall_through() -> None:
    first = _RaisingLLM("first", ConnectionError("dropped"), emit_first=TextDelta(text="partial"))
    second = _ScriptedLLM("second", [TextDelta(text="unused"), Done(usage=Usage(0, 0))])
    llm = FallbackLLM([("first", first), ("second", second)])

    events = await _run(llm)

    assert events[0] == TextDelta(text="partial")
    assert isinstance(events[1], LLMError)
    assert "ConnectionError" in events[1].message
    assert second.calls == 0


async def test_last_provider_raising_yields_error_not_exception() -> None:
    only = _RaisingLLM("only", ConnectionError("getaddrinfo failed"))
    llm = FallbackLLM([("only", only)])

    events = await _run(llm)

    assert len(events) == 1
    assert isinstance(events[0], LLMError)
    assert events[0].code == "provider_error"


async def test_tool_call_counts_as_output_started() -> None:
    call = ToolCall(id="1", name="get_weather", args_json="{}")
    error = LLMError(code="unavailable", message="dropped", retryable=True)
    first = _ScriptedLLM("first", [call, error])
    second = _ScriptedLLM("second", [TextDelta(text="unused"), Done(usage=Usage(0, 0))])
    llm = FallbackLLM([("first", first), ("second", second)])

    events = await _run(llm)

    assert events == [call, error]
    assert second.calls == 0
