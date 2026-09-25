"""Microsoft Edge read-aloud voices via the `edge-tts` package (free, no API key).

Verified 2026-09-25 against edge-tts 7.2.8's source and `list_voices()`: output is always
`audio-24khz-48kbitrate-mono-mp3`; female voices hi-IN-SwaraNeural, mr-IN-AarohiNeural,
en-IN-NeerjaNeural. This is an *unofficial* endpoint Microsoft can change or block at any time:
fine for a prototype, keep a paid provider (e.g. the Sarvam adapter) ready for production.
"""

from __future__ import annotations

import asyncio

import aiohttp
import edge_tts
from edge_tts import exceptions as edge_errors

from voice_core.ports.errors import ProviderError, ProviderUnavailable
from voice_core.ports.types import AudioSegment

FEMALE_VOICES = {
    "hi-IN": "hi-IN-SwaraNeural",
    "mr-IN": "mr-IN-AarohiNeural",
    "en-IN": "en-IN-NeerjaNeural",
}
MP3_KBPS = 48
MAX_ATTEMPTS = 2  # one retry; synthesis is idempotent and nothing was emitted yet


def edge_rate(pace: float) -> str:
    """Our pace (0.5-2.0, 1.0 = normal) as edge-tts's relative rate, e.g. 0.95 -> "-5%"."""
    percent = round((min(2.0, max(0.5, pace)) - 1.0) * 100)
    return f"{percent:+d}%"


class EdgeTTS:
    def __init__(self, voices: dict[str, str] | None = None, *, timeout_s: int = 15) -> None:
        self._voices = {**FEMALE_VOICES, **(voices or {})}
        self._timeout_s = timeout_s

    def voice_for(self, language: str, speaker: str) -> str:
        # A full edge voice name for this language (e.g. TTS_SPEAKER=hi-IN-SwaraNeural) wins;
        # anything else (a placeholder, another provider's speaker) uses the language default.
        if speaker.startswith(f"{language}-"):
            return speaker
        return self._voices.get(language, FEMALE_VOICES["en-IN"])

    async def synthesize(self, text: str, language: str, speaker: str, pace: float) -> AudioSegment:
        voice = self.voice_for(language, speaker)
        last: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                audio = await self._stream(text, voice, edge_rate(pace))
            except (edge_errors.EdgeTTSException, aiohttp.ClientError, TimeoutError) as exc:
                last = exc
                if attempt + 1 < MAX_ATTEMPTS:
                    await asyncio.sleep(0.3)
                continue
            if not audio:
                raise ProviderError("edge-tts returned no audio")
            return AudioSegment(data=audio, fmt="mp3_24k", duration_ms=len(audio) * 8 // MP3_KBPS)
        raise ProviderUnavailable(f"edge-tts failed: {type(last).__name__}") from last

    async def _stream(self, text: str, voice: str, rate: str) -> bytes:
        communicate = edge_tts.Communicate(
            text, voice, rate=rate, connect_timeout=self._timeout_s, receive_timeout=self._timeout_s
        )
        chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks)


async def female_voices(languages: tuple[str, ...]) -> list[str]:
    """All female edge voices for these locales, straight from the service's voice list."""
    voices = await edge_tts.list_voices()
    return [
        str(v["ShortName"])
        for v in voices
        if v.get("Locale") in languages and v.get("Gender") == "Female"
    ]
