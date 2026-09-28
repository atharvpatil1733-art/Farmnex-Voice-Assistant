from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Callable

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

# Circuit breaker: after a 429 a link is skipped for a while instead of being retried on every
# LLM round (each retry cost 0.2-0.5 s of dead air). Free tiers limit per minute and per day.
RATE_LIMIT_COOLDOWN_S = 60.0
DAILY_QUOTA_COOLDOWN_S = 3600.0
_DAILY_MARKERS = ("perday", "per day", "daily")


def _cooldown_for(error: LLMError) -> float:
    compact = error.message.lower()
    if any(marker in compact or marker in compact.replace(" ", "") for marker in _DAILY_MARKERS):
        return DAILY_QUOTA_COOLDOWN_S
    return RATE_LIMIT_COOLDOWN_S


class FallbackLLM:
    """Tries a list of LLMProviders in priority order. Only advances to the next one if the
    current provider fails *before* emitting any text or tool call — once a provider has
    started answering, its output (including a later error) is forwarded as-is, never
    silently swapped for another provider's attempt (SPEC: never retry after first output)."""

    name = "fallback"

    def __init__(
        self,
        providers: list[tuple[str, LLMProvider]],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not providers:
            raise ValueError("FallbackLLM needs at least one provider")
        self._providers = providers
        self._clock = clock
        self._cooling_until: dict[str, float] = {}

    async def warm(self) -> None:
        """Warm every link that supports it; best-effort, failures are ignored."""
        for _, provider in self._providers:
            warm = getattr(provider, "warm", None)
            if warm is not None:
                try:
                    await warm()
                except Exception:
                    logger.info("llm_warm_failed", exc_info=True)

    def _order(self) -> list[tuple[str, LLMProvider]]:
        """Healthy links in priority order; if every link is cooling, only the one that
        recovers first (so a turn still gets one real attempt)."""
        now = self._clock()
        healthy = [
            (label, provider)
            for label, provider in self._providers
            if self._cooling_until.get(label, 0.0) <= now
        ]
        if healthy:
            return healthy
        soonest = min(self._providers, key=lambda lp: self._cooling_until.get(lp[0], 0.0))
        return [soonest]

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
        order = self._order()
        for index, (label, provider) in enumerate(order):
            emitted = False
            is_last = index == len(order) - 1
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
                if failure.code == "rate_limited":
                    cooldown = _cooldown_for(failure)
                    self._cooling_until[label] = self._clock() + cooldown
                    logger.info("llm_link_cooling", extra={"provider": label, "seconds": cooldown})
                if emitted or is_last:
                    yield failure
                    return
                last_error = failure

        if last_error is not None:
            yield last_error
