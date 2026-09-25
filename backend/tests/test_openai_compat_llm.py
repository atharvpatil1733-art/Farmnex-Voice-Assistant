from __future__ import annotations

import json

import httpx2
import pytest

from voice_core.adapters.openai_compat.llm import OpenAICompatLLM, _to_openai_messages
from voice_core.ports.types import ChatMessage, Done, LLMError, TextDelta, ToolCall, ToolSpec


def _llm(handler: httpx2.MockTransport) -> OpenAICompatLLM:
    client = httpx2.AsyncClient(transport=handler)
    return OpenAICompatLLM(
        api_key="test-key",
        model="test-model",
        base_url="https://example.test/v1",
        http_client=client,
    )


def _completion_response(
    *, content: str | None = None, tool_calls: list[dict] | None = None
) -> httpx2.Response:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    body = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "test-model",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    return httpx2.Response(200, json=body)


def test_to_openai_messages_merges_tool_role_into_user_and_consecutive_roles() -> None:
    messages = [
        ChatMessage(role="system", content="rules"),
        ChatMessage(role="user", content="hi"),
        ChatMessage(role="assistant", content="calling tool"),
        ChatMessage(role="tool", content='<tool_result tool="x">{}</tool_result>'),
    ]
    out = _to_openai_messages(messages)
    assert out == [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "calling tool"},
        {"role": "user", "content": '<tool_result tool="x">{}</tool_result>'},
    ]


async def test_plain_text_reply_yields_text_delta_and_done() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return _completion_response(content="hello there")

    llm = _llm(httpx2.MockTransport(handler))
    events = [
        e
        async for e in llm.stream(
            [ChatMessage(role="user", content="hi")],
            [],
            temperature=0.0,
            max_tokens=50,
            timeout_s=5.0,
        )
    ]
    assert events[0] == TextDelta(text="hello there")
    assert isinstance(events[-1], Done)
    assert events[-1].usage.input_tokens == 10
    assert events[-1].usage.output_tokens == 5


async def test_tool_call_reply_yields_tool_call() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return _completion_response(
            tool_calls=[
                {
                    "id": "call_abc",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": json.dumps({"city": "Pune"})},
                }
            ]
        )

    llm = _llm(httpx2.MockTransport(handler))
    tool = ToolSpec(name="get_weather", description="d", parameters={"type": "object"})
    events = [
        e
        async for e in llm.stream(
            [ChatMessage(role="user", content="weather in Pune?")],
            [tool],
            temperature=0.0,
            max_tokens=50,
            timeout_s=5.0,
        )
    ]
    calls = [e for e in events if isinstance(e, ToolCall)]
    assert len(calls) == 1
    assert calls[0].name == "get_weather"
    assert json.loads(calls[0].args_json) == {"city": "Pune"}
    assert any(isinstance(e, Done) for e in events)


async def test_rate_limit_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("voice_core.adapters.openai_compat.llm.asyncio.sleep", no_sleep)
    attempts = {"n": 0}

    async def handler(request: httpx2.Request) -> httpx2.Response:
        attempts["n"] += 1
        if attempts["n"] < 2:
            return httpx2.Response(429, json={"error": {"message": "rate limited"}})
        return _completion_response(content="ok now")

    llm = _llm(httpx2.MockTransport(handler))
    events = [
        e
        async for e in llm.stream(
            [ChatMessage(role="user", content="hi")],
            [],
            temperature=0.0,
            max_tokens=50,
            timeout_s=5.0,
        )
    ]
    assert attempts["n"] == 2
    assert any(isinstance(e, TextDelta) and e.text == "ok now" for e in events)


async def test_connection_error_yields_llm_error_not_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DNS/connection failure must become an LLMError so a caller like FallbackLLM can
    try the next provider — it must never escape as a raw exception."""

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("voice_core.adapters.openai_compat.llm.asyncio.sleep", no_sleep)

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("[Errno 11001] getaddrinfo failed")

    llm = _llm(httpx2.MockTransport(handler))
    events = [
        e
        async for e in llm.stream(
            [ChatMessage(role="user", content="hi")],
            [],
            temperature=0.0,
            max_tokens=50,
            timeout_s=5.0,
        )
    ]
    assert len(events) == 1
    assert isinstance(events[0], LLMError)
    assert events[0].code == "unavailable"
    assert events[0].retryable is True


async def test_bad_request_yields_llm_error_without_retry() -> None:
    attempts = {"n": 0}

    async def handler(request: httpx2.Request) -> httpx2.Response:
        attempts["n"] += 1
        return httpx2.Response(400, json={"error": {"message": "bad request"}})

    llm = _llm(httpx2.MockTransport(handler))
    events = [
        e
        async for e in llm.stream(
            [ChatMessage(role="user", content="hi")],
            [],
            temperature=0.0,
            max_tokens=50,
            timeout_s=5.0,
        )
    ]
    assert attempts["n"] == 1
    assert len(events) == 1
    assert isinstance(events[0], LLMError)
    assert events[0].code == "bad_request"
    assert events[0].retryable is False
