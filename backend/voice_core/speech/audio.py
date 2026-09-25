"""Small, dependency-free helpers for the client's push-to-talk audio (16 kHz mono PCM16)."""

from __future__ import annotations

import io
import wave

PCM_SAMPLE_RATE = 16_000
MIN_AUDIO_MS = 300  # shorter = accidental tap; not worth an STT call


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
