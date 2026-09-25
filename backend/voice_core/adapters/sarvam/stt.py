"""Sarvam speech-to-text (`POST /speech-to-text`, model saaras:v3), verified 2026-09-25.

Request: multipart `file`, `model`, `mode` (transcribe keeps the spoken language — never
translate before the LLM), `language_code` ("unknown" = auto-detect; a real code skips
detection, which would break automatic language switching). Response: `transcript`,
`language_code` (null if none detected), `language_probability` (0-1, always present).
REST accepts clips up to 30 s.
"""

from __future__ import annotations

import io
import wave

from voice_core.adapters.sarvam.client import SarvamClient
from voice_core.ports.errors import ProviderBadRequest
from voice_core.ports.types import AudioFormat, Transcript

PCM_SAMPLE_RATE = 16_000
MIN_AUDIO_MS = 300  # shorter = accidental tap; not worth an API call


def pcm16_duration_ms(pcm: bytes, sample_rate: int = PCM_SAMPLE_RATE) -> int:
    return int(len(pcm) / 2 / sample_rate * 1000)


def pcm16_to_wav(pcm: bytes, sample_rate: int = PCM_SAMPLE_RATE) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buffer.getvalue()


class SarvamSTT:
    def __init__(
        self, client: SarvamClient, *, model: str = "saaras:v3", mode: str = "transcribe"
    ) -> None:
        self._client = client
        self._model = model
        self._mode = mode

    async def transcribe(
        self, audio: bytes, fmt: AudioFormat, language_hint: str | None
    ) -> Transcript:
        if fmt != "pcm_s16le_16k":
            raise ProviderBadRequest(f"unsupported input format {fmt!r}")
        duration_ms = pcm16_duration_ms(audio)
        if duration_ms < MIN_AUDIO_MS:
            return Transcript(
                text="", language_code="", language_confidence=0.0, duration_ms=duration_ms
            )

        body = await self._client.post(
            "/speech-to-text",
            files={"file": ("utterance.wav", pcm16_to_wav(audio), "audio/wav")},
            data={"model": self._model, "mode": self._mode, "language_code": "unknown"},
        )
        return Transcript(
            text=str(body.get("transcript") or "").strip(),
            language_code=str(body.get("language_code") or ""),
            language_confidence=float(body.get("language_probability") or 0.0),
            duration_ms=duration_ms,
        )

    def open_stream(self, fmt: AudioFormat, language_hint: str | None) -> SarvamSTTStream:
        raise NotImplementedError("realtime STT is M6")


class SarvamSTTStream:  # pragma: no cover - M6 placeholder to satisfy the port's return type
    async def push(self, chunk: bytes) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError
