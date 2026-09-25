from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from voice_core.ports.llm import LLMProvider
from voice_core.ports.types import (
    ChatMessage,
    Done,
    LLMError,
    LLMEvent,
    TextDelta,
    ToolCall,
    ToolSpec,
)

logger = logging.getLogger(__name__)


class FallbackLLM:
    """Tries a list of LLMProviders in priority order. Only advances to the next one if the
    current provider fails *before* emitting any text or tool call — once a provider has
    started answering, its output (including a later error) is forwarded as-is, never
    silently swapped for another provider's attempt (SPEC: never retry after first output)."""

    name = "fallback"

    def __init__(self, providers: list[tuple[str, LLMProvider]]) -> None:
        if not providers:
            raise ValueError("FallbackLLM needs at least one provider")
        self._providers = providers

    async def stream(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        *,
        temperature: float,
        max_tokens: int,
        timeout_s: float,
    ) -> AsyncIterator[LLMEvent]:
        last_error: LLMError | None = None
        for index, (label, provider) in enumerate(self._providers):
            emitted = False
            is_last = index == len(self._providers) - 1
            failure: LLMError | None = None

            try:
                async for event in provider.stream(
                    messages,
                    tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout_s=timeout_s,
                ):
                    match event:
                        case TextDelta() | ToolCall():
                            emitted = True
                            yield event
                        case Done():
                            logger.info("llm_provider_used", extra={"provider": label})
                            yield event
                            return
                        case LLMError():
                            failure = event
                            break
                else:
                    # Stream ended without Done or LLMError (defensive; shouldn't happen for
                    # a well-behaved LLMProvider) — treat as success if it emitted, otherwise
                    # as a pre-output failure, so the contract "every stream ends in Done or
                    # LLMError" still holds for the caller.
                    if emitted:
                        return
                    failure = LLMError(
                        code="provider_error",
                        message=f"{label} stream ended without Done or LLMError",
                        retryable=False,
                    )
            except Exception as exc:
                # A provider that RAISES instead of yielding LLMError (e.g. an adapter that
                # forgot to map a network error) must not defeat the whole chain — that is
                # precisely the failure this class exists to survive.
                failure = LLMError(
                    code="provider_error",
                    message=f"{label} raised {type(exc).__name__}: {exc}",
                    retryable=False,
                )

            if failure is not None:
                logger.warning(
                    "llm_provider_failed", extra={"provider": label, "code": failure.code}
                )
                if emitted or is_last:
                    yield failure
                    return
                last_error = failure

        if last_error is not None:
            yield last_error
