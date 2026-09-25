"""Shared HTTP plumbing for Sarvam's REST APIs (verified against docs.sarvam.ai, 2026-09-25):
base https://api.sarvam.ai, auth header `api-subscription-key`, 429 = quota, 503 = overloaded."""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx

from voice_core.ports.errors import (
    ProviderBadRequest,
    ProviderError,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)

DEFAULT_BASE_URL = "https://api.sarvam.ai"
MAX_RETRIES = 2  # SKILL: at most 2 retries with jitter, idempotent calls only


class SarvamClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("SARVAM_API_KEY is not set")
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"api-subscription-key": api_key},
            timeout=timeout_s,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def post(self, path: str, **kwargs: Any) -> dict[str, Any]:
        """POST with retries on 429/503/timeouts. Raises a core ProviderError otherwise."""
        last: ProviderError | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await self._client.post(path, **kwargs)
            except httpx.TimeoutException as exc:
                last = ProviderTimeout(f"sarvam {path} timed out")
                last.__cause__ = exc
            except httpx.HTTPError as exc:
                last = ProviderUnavailable(f"sarvam {path} unreachable: {type(exc).__name__}")
                last.__cause__ = exc
            else:
                if response.status_code == 200:
                    body = response.json()
                    if not isinstance(body, dict):
                        raise ProviderError(f"sarvam {path}: unexpected response shape")
                    return body
                last = _error_for(path, response)
                if not isinstance(last, ProviderRateLimited | ProviderUnavailable):
                    raise last
            if attempt < MAX_RETRIES:
                await asyncio.sleep(0.25 * (2**attempt) + random.uniform(0, 0.2))  # nosec B311
        raise last or ProviderError(f"sarvam {path} failed")


def _error_for(path: str, response: httpx.Response) -> ProviderError:
    # Never include the request (it carries the key header); status + short body only.
    detail = response.text[:200]
    code = response.status_code
    if code == 429:
        return ProviderRateLimited(f"sarvam {path} 429: {detail}")
    if code in (500, 502, 503, 504):
        return ProviderUnavailable(f"sarvam {path} {code}: {detail}")
    return ProviderBadRequest(f"sarvam {path} {code}: {detail}")
