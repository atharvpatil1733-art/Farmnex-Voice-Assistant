"""Sarvam text-to-speech (`POST /text-to-speech`, model bulbul:v3), verified 2026-09-25.

Request JSON: `text` (≤ 2500 chars for v3; our segmenter sends one sentence),
`target_language_code`, `model`, `speaker`, `pace` (0.5-2.0), `speech_sample_rate`.
`enable_preprocessing` stays off: our
own normalizer already produced speakable text. Response: `audios` = list of base64 WAV.
"""

from __future__ import annotations

import base64
import io
import wave

from voice_core.adapters.sarvam.client import SarvamClient
from voice_core.ports.errors import ProviderError
from voice_core.ports.types import AudioSegment

MAX_CHARS = 2500
SAMPLE_RATE = 22_050


def wav_duration_ms(data: bytes) -> int:
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            return int(wav.getnframes() / wav.getframerate() * 1000)
    except (wave.Error, EOFError, ZeroDivisionError):
        return 0


class SarvamTTS:
    def __init__(
        self, client: SarvamClient, *, model: str = "bulbul:v3", sample_rate: int = SAMPLE_RATE
    ) -> None:
        self._client = client
        self._model = model
        self._sample_rate = sample_rate

    async def synthesize(self, text: str, language: str, speaker: str, pace: float) -> AudioSegment:
        body = await self._client.post(
            "/text-to-speech",
            json={
                "text": text[:MAX_CHARS],
                "target_language_code": language,
                "model": self._model,
                "speaker": speaker,
                "pace": min(2.0, max(0.5, pace)),
                "speech_sample_rate": self._sample_rate,
            },
        )
        audios = body.get("audios")
        if not isinstance(audios, list) or not audios:
            raise ProviderError("sarvam tts returned no audio")
        # One sentence per request, so one clip; if the API ever splits, keep the first
        # rather than concatenating WAV files (which would corrupt the header).
        data = base64.b64decode(audios[0])
        return AudioSegment(data=data, fmt="wav_22050", duration_ms=wav_duration_ms(data))
