from __future__ import annotations

from voice_core.ports.types import AudioFormat, Transcript


class FakeSTTStream:
    async def push(self, chunk: bytes) -> None:
        return None

    async def close(self) -> None:
        return None


class FakeSTT:
    """Scriptable fake: always returns the configured transcript."""

    def __init__(self, transcript: Transcript | None = None) -> None:
        self._transcript = transcript or Transcript(
            text="", language_code="en-IN", language_confidence=1.0, duration_ms=0
        )

    async def transcribe(
        self, audio: bytes, fmt: AudioFormat, language_hint: str | None
    ) -> Transcript:
        return self._transcript

    def open_stream(self, fmt: AudioFormat, language_hint: str | None) -> FakeSTTStream:
        return FakeSTTStream()
