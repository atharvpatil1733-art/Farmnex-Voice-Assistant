from __future__ import annotations

from voice_core.ports.types import AudioSegment


class FakeTTS:
    """Returns a zero-length audio segment tagging the text length, no real synthesis."""

    async def synthesize(self, text: str, language: str, speaker: str, pace: float) -> AudioSegment:
        return AudioSegment(data=text.encode("utf-8"), fmt="wav_22050", duration_ms=len(text) * 50)
