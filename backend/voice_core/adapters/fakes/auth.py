from __future__ import annotations

from voice_core.ports.types import Principal


class AuthError(Exception):
    pass


class FakeAuthVerifier:
    """Returns the configured principal for any token that matches, else raises AuthError."""

    def __init__(self, tokens: dict[str, Principal] | None = None) -> None:
        self._tokens = tokens or {}

    async def verify(self, token: str) -> Principal:
        try:
            return self._tokens[token]
        except KeyError as exc:
            raise AuthError("invalid or expired token") from exc
