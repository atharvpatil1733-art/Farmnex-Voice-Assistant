from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import AsyncIterator

import httpx
from google import genai
from google.genai import types
from google.genai.errors import APIError, ClientError, ServerError

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
_DEFAULT_RETRY_DELAY_S = 15.0
_RETRY_DELAY_PATTERN = re.compile(r"retry in ([\d.]+)s", re.IGNORECASE)


def _to_genai_contents(messages: list[ChatMessage]) -> tuple[list[types.Content], str | None]:
    system_parts: list[str] = []
    contents: list[types.Content] = []
    for msg in messages:
        if msg.role == "system":
            system_parts.append(msg.content)
            continue
        role = "model" if msg.role == "assistant" else "user"
        part = types.Part.from_text(text=msg.content)
        if contents and contents[-1].role == role and contents[-1].parts is not None:
            contents[-1].parts.append(part)
        else:
            contents.append(types.Content(role=role, parts=[part]))
    system_instruction = "\n\n".join(system_parts) if system_parts else None
    return contents, system_instruction


def _retry_delay_seconds(message: str) -> float:
    match = _RETRY_DELAY_PATTERN.search(message)
    return float(match.group(1)) + 1.0 if match else _DEFAULT_RETRY_DELAY_S


class GeminiLLM:
    name = "gemini"

    def __init__(self, api_key: str, model: str) -> None:
        self._client = genai.Client(api_key=api_key)
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
        contents, system_instruction = _to_genai_contents(messages)
        function_declarations = [
            types.FunctionDeclaration(
                name=t.name, description=t.description, parameters_json_schema=t.parameters
            )
            for t in tools
        ]
        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            system_instruction=system_instruction,
            tools=[types.Tool(function_declarations=function_declarations)]
            if function_declarations
            else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            http_options=types.HttpOptions(timeout=int(timeout_s * 1000)),
        )

        for attempt in range(_MAX_ATTEMPTS):
            seen_calls: set[tuple[str, str]] = set()
            usage = Usage(input_tokens=0, output_tokens=0)
            emitted_any = False
            try:
                stream = await self._client.aio.models.generate_content_stream(
                    model=self._model, contents=contents, config=config
                )
                async for chunk in stream:
                    if chunk.text:
                        emitted_any = True
                        yield TextDelta(text=chunk.text)
                    for call in chunk.function_calls or []:
                        args_json = json.dumps(call.args or {})
                        signature = (call.name or "", args_json)
                        if signature in seen_calls:
                            continue
                        seen_calls.add(signature)
                        emitted_any = True
                        yield ToolCall(
                            id=f"call_{uuid.uuid4().hex[:8]}",
                            name=call.name or "",
                            args_json=args_json,
                        )
                    if chunk.usage_metadata is not None:
                        usage = Usage(
                            input_tokens=chunk.usage_metadata.prompt_token_count or 0,
                            output_tokens=chunk.usage_metadata.candidates_token_count or 0,
                        )
                yield Done(usage=usage)
                return
            except httpx.TimeoutException as exc:
                yield LLMError(code="timeout", message=str(exc), retryable=True)
                return
            except httpx.TransportError as exc:
                # DNS/connection failures (httpx.ConnectError and friends) are NOT
                # google.genai APIErrors, so without this they escape uncaught and deny the
                # caller (e.g. FallbackLLM) any chance to try another provider.
                if not emitted_any and attempt < _MAX_ATTEMPTS - 1:
                    await asyncio.sleep(_DEFAULT_RETRY_DELAY_S)
                    continue
                yield LLMError(code="unavailable", message=str(exc), retryable=True)
                return
            except ClientError as exc:
                is_rate_limited = exc.code == 429
                # A request that already emitted content must not be retried (SPEC: never retry
                # after the first token was emitted); a 429/5xx fails before any content, so a
                # bounded retry-with-backoff here stays within "retry only idempotent calls".
                if is_rate_limited and not emitted_any and attempt < _MAX_ATTEMPTS - 1:
                    await asyncio.sleep(_retry_delay_seconds(str(exc)))
                    continue
                code = "rate_limited" if is_rate_limited else "bad_request"
                yield LLMError(code=code, message=str(exc), retryable=is_rate_limited)
                return
            except ServerError as exc:
                if not emitted_any and attempt < _MAX_ATTEMPTS - 1:
                    await asyncio.sleep(_DEFAULT_RETRY_DELAY_S)
                    continue
                yield LLMError(code="unavailable", message=str(exc), retryable=True)
                return
            except APIError as exc:
                yield LLMError(code="provider_error", message=str(exc), retryable=False)
                return
