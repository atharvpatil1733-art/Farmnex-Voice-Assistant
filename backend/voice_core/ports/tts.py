from __future__ import annotations

from typing import Protocol, runtime_checkable

from voice_core.ports.types import AudioSegment


@runtime_checkable
class TTSProvider(Protocol):
    async def synthesize(
        self, text: str, language: str, speaker: str, pace: float
    ) -> AudioSegment: ...
