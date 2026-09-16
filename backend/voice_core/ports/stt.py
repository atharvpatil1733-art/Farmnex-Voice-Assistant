from __future__ import annotations

from typing import Protocol, runtime_checkable

from voice_core.ports.types import AudioFormat, Transcript


@runtime_checkable
class STTStream(Protocol):
    """Realtime transcription stream (M6). Reserved; not used before then."""

    async def push(self, chunk: bytes) -> None: ...
    async def close(self) -> None: ...


@runtime_checkable
class STTProvider(Protocol):
    async def transcribe(
        self, audio: bytes, fmt: AudioFormat, language_hint: str | None
    ) -> Transcript: ...

    def open_stream(self, fmt: AudioFormat, language_hint: str | None) -> STTStream: ...
