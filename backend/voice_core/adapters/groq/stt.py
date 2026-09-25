"""Groq Whisper speech-to-text (OpenAI-compatible `POST /audio/transcriptions`), free tier.

Verified 2026-09-25 (console.groq.com docs + live probe): models `whisper-large-v3` and
`whisper-large-v3-turbo`, free tier 20 RPM / 2,000 RPD / 28,800 audio-seconds per day, min 10 s
billed per request; `response_format=verbose_json` returns `language` as an English name
("Hindi") and per-segment `no_speech_prob`. There is no language-confidence score.

Measured on our own hi/mr/en samples: without a hint Whisper labels Marathi as Hindi and
garbles it; `whisper-large-v3` *with* the session language as `language` hint is clearly best.
So the hint is always sent, and `language_confidence` is reported as 0.0 (unknown) — automatic
language switching therefore never triggers from Whisper; users switch explicitly.
"""

from __future__ import annotations

from typing import Any

import httpx

from voice_core.ports.errors import (
    ProviderBadRequest,
    ProviderError,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from voice_core.ports.types import AudioFormat, Transcript
from voice_core.speech.audio import MIN_AUDIO_MS, pcm16_duration_ms, pcm16_to_wav

DEFAULT_MODEL = "whisper-large-v3"
NO_SPEECH_THRESHOLD = 0.6  # Whisper invents text for silence; drop segments it flags as such
_LANGUAGE_NAMES = {"hindi": "hi-IN", "marathi": "mr-IN", "english": "en-IN"}


class GroqWhisperSTT:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.groq.com/openai/v1",
        model: str = DEFAULT_MODEL,
        timeout_s: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("GROQ_API_KEY is not set")
        self._model = model
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_s,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def transcribe(
        self, audio: bytes, fmt: AudioFormat, language_hint: str | None
    ) -> Transcript:
        if fmt == "pcm_s16le_16k":
            duration_ms = pcm16_duration_ms(audio)
            if duration_ms < MIN_AUDIO_MS:
                return Transcript(
                    text="", language_code="", language_confidence=0.0, duration_ms=duration_ms
                )
            upload = ("utterance.wav", pcm16_to_wav(audio), "audio/wav")
        elif fmt == "mp3_24k":
            duration_ms = 0
            upload = ("utterance.mp3", audio, "audio/mpeg")
        else:
            raise ProviderBadRequest(f"unsupported input format {fmt!r}")

        data = {"model": self._model, "response_format": "verbose_json"}
        if language_hint:
            data["language"] = language_hint.split("-")[0]  # ISO-639-1: "hi", "mr", "en"
        body = await self._post(files={"file": upload}, data=data)

        segments: list[dict[str, Any]] = [
            s for s in body.get("segments") or [] if isinstance(s, dict)
        ]
        spoken = [
            s for s in segments if float(s.get("no_speech_prob") or 0.0) < NO_SPEECH_THRESHOLD
        ]
        if segments:
            text = " ".join(str(s.get("text") or "").strip() for s in spoken).strip()
        else:
            text = str(body.get("text") or "").strip()
        detected = _LANGUAGE_NAMES.get(str(body.get("language") or "").lower(), "")
        return Transcript(
            text=text,
            language_code=detected,
            language_confidence=0.0,  # Whisper gives none; see module docstring
            duration_ms=duration_ms or int(float(body.get("duration") or 0) * 1000),
        )

    def open_stream(self, fmt: AudioFormat, language_hint: str | None) -> Any:
        raise NotImplementedError("realtime STT is M6")

    async def _post(self, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._client.post("/audio/transcriptions", **kwargs)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout("groq stt timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"groq stt unreachable: {type(exc).__name__}") from exc
        if response.status_code == 200:
            body = response.json()
            if not isinstance(body, dict):
                raise ProviderError("groq stt: unexpected response shape")
            return body
        detail = response.text[:200]  # never the request: it carries the key header
        if response.status_code == 429:
            raise ProviderRateLimited(f"groq stt 429: {detail}")
        if response.status_code >= 500:
            raise ProviderUnavailable(f"groq stt {response.status_code}: {detail}")
        raise ProviderBadRequest(f"groq stt {response.status_code}: {detail}")
