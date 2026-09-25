from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx2
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)

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

_MAX_ATTEMPTS = 3
_DEFAULT_RETRY_DELAY_S = 5.0


def _to_openai_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """Merge consecutive same-role messages; map our `tool` role (already wrapped in
    `<tool_result>` tags by `agent/prompt.py`) onto plain `user` turns, since it is
    the model's own past output, not a tool-protocol message, that needs replaying."""
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = "user" if msg.role == "tool" else msg.role
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n\n" + msg.content
        else:
            out.append({"role": role, "content": msg.content})
    return out


def _to_openai_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


class OpenAICompatLLM:
    """LLMProvider over any OpenAI-compatible chat-completions endpoint (Groq, OpenRouter,
    a local Ollama, ...). Non-streaming: the whole reply is fetched, then replayed as events."""

    name = "openai_compat"

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, http_client=http_client)
        self._model = model

    async def stream(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        *,
        temperature: float,
        max_tokens: int,
        timeout_s: float,
    ) -> AsyncIterator[LLMEvent]:
        oa_messages = _to_openai_messages(messages)
        oa_tools = _to_openai_tools(tools)

        for attempt in range(_MAX_ATTEMPTS):
            try:
                completion = await self._client.with_options(
                    timeout=timeout_s
                ).chat.completions.create(
                    model=self._model,
                    messages=oa_messages,  # type: ignore[arg-type]
                    tools=oa_tools or None,  # type: ignore[arg-type]
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except APITimeoutError as exc:
                yield LLMError(code="timeout", message=str(exc), retryable=True)
                return
            except APIConnectionError as exc:
                # DNS/connection failures. Must stay below APITimeoutError, which subclasses
                # this. Without it these escape uncaught and deny the caller (e.g.
                # FallbackLLM) any chance to try another provider.
                if attempt < _MAX_ATTEMPTS - 1:
                    await asyncio.sleep(_DEFAULT_RETRY_DELAY_S)
                    continue
                yield LLMError(code="unavailable", message=str(exc), retryable=True)
                return
            except RateLimitError as exc:
                if attempt < _MAX_ATTEMPTS - 1:
                    await asyncio.sleep(_retry_delay_seconds(exc))
                    continue
                yield LLMError(code="rate_limited", message=str(exc), retryable=True)
                return
            except APIStatusError as exc:
                if exc.status_code >= 500 and attempt < _MAX_ATTEMPTS - 1:
                    await asyncio.sleep(_DEFAULT_RETRY_DELAY_S)
                    continue
                code = "unavailable" if exc.status_code >= 500 else "bad_request"
                yield LLMError(code=code, message=str(exc), retryable=exc.status_code >= 500)
                return
            else:
                break

        if not completion.choices:
            yield LLMError(code="bad_response", message="no choices in response", retryable=False)
            return

        message = completion.choices[0].message
        if message.content:
            yield TextDelta(text=message.content)
        for call in message.tool_calls or []:
            if call.type != "function":
                continue  # we never request "custom" (freeform) tools
            yield ToolCall(
                id=call.id,
                name=call.function.name,
                args_json=call.function.arguments or "{}",
            )
        usage = completion.usage
        yield Done(
            usage=Usage(
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
            )
        )


def _retry_delay_seconds(exc: RateLimitError) -> float:
    header = exc.response.headers.get("retry-after") if exc.response is not None else None
    if header is not None:
        try:
            return float(header) + 1.0
        except ValueError:
            pass
    return _DEFAULT_RETRY_DELAY_S
