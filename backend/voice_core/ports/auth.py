from __future__ import annotations

from typing import Protocol, runtime_checkable

from voice_core.ports.types import Principal


@runtime_checkable
class AuthVerifier(Protocol):
    async def verify(self, token: str) -> Principal: ...
