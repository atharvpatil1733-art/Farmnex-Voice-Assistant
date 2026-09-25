from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from voice_core.adapters.sarvam import client as client_module
from voice_core.adapters.sarvam.client import SarvamClient
from voice_core.adapters.sarvam.stt import SarvamSTT, pcm16_to_wav
from voice_core.adapters.sarvam.tts import SarvamTTS
from voice_core.ports.errors import ProviderBadRequest, ProviderRateLimited
from voice_core.ports.stt import STTProvider
from voice_core.ports.tts import TTSProvider

ONE_SECOND_PCM = b"\x00\x00" * 16_000


class Replay:
    def __init__(self, *responses: httpx.Response) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]


def _client(replay: Replay) -> SarvamClient:
    return SarvamClient("key-123", transport=httpx.MockTransport(replay))


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_: float) -> None:
        return None

    monkeypatch.setattr(client_module.asyncio, "sleep", instant)


def test_adapters_satisfy_ports() -> None:
    client = SarvamClient("k")
    assert isinstance(SarvamSTT(client), STTProvider)
    assert isinstance(SarvamTTS(client), TTSProvider)


def test_missing_key_fails_fast() -> None:
    with pytest.raises(ValueError, match="SARVAM_API_KEY"):
        SarvamClient("")


async def test_stt_sends_wav_with_auto_detect_and_maps_response() -> None:
    replay = Replay(
        httpx.Response(
            200,
            json={
                "request_id": "r",
                "transcript": " मेरी बोली कितनी आई ",
                "language_code": "hi-IN",
                "language_probability": 0.93,
            },
        )
    )
    transcript = await SarvamSTT(_client(replay)).transcribe(
        ONE_SECOND_PCM, "pcm_s16le_16k", "hi-IN"
    )

    assert transcript.text == "मेरी बोली कितनी आई"
    assert transcript.language_code == "hi-IN"
    assert transcript.language_confidence == pytest.approx(0.93)
    assert transcript.duration_ms == 1000
    request = replay.requests[0]
    assert request.url.path == "/speech-to-text"
    assert request.headers["api-subscription-key"] == "key-123"
    body = request.content
    assert b'name="model"' in body and b"saaras:v3" in body
    assert b'name="mode"' in body and b"transcribe" in body
    assert b"unknown" in body  # auto-detect, never pinned to the hint
    assert b"RIFF" in body  # PCM was wrapped as WAV


async def test_stt_skips_accidental_taps_without_calling_the_api() -> None:
    replay = Replay(httpx.Response(200, json={}))
    transcript = await SarvamSTT(_client(replay)).transcribe(
        b"\x00\x00" * 1600, "pcm_s16le_16k", None
    )
    assert transcript.text == ""
    assert replay.requests == []


async def test_stt_null_language_maps_to_empty() -> None:
    replay = Replay(
        httpx.Response(
            200, json={"transcript": "", "language_code": None, "language_probability": 0.0}
        )
    )
    transcript = await SarvamSTT(_client(replay)).transcribe(ONE_SECOND_PCM, "pcm_s16le_16k", None)
    assert transcript.language_code == ""
    assert transcript.text == ""


async def test_tts_request_and_wav_decoding() -> None:
    wav = pcm16_to_wav(b"\x00\x00" * 22_050, sample_rate=22_050)  # 1 s
    replay = Replay(httpx.Response(200, json={"audios": [base64.b64encode(wav).decode()]}))

    segment = await SarvamTTS(_client(replay)).synthesize("नमस्ते", "hi-IN", "priya", 0.95)

    assert segment.data == wav
    assert segment.duration_ms == 1000
    sent: dict[str, Any] = json.loads(replay.requests[0].content)
    assert sent == {
        "text": "नमस्ते",
        "target_language_code": "hi-IN",
        "model": "bulbul:v3",
        "speaker": "priya",
        "pace": 0.95,
        "speech_sample_rate": 22050,
    }


async def test_rate_limit_is_retried_then_succeeds() -> None:
    replay = Replay(
        httpx.Response(429, json={"error": "quota"}),
        httpx.Response(
            200, json={"transcript": "ok", "language_code": "en-IN", "language_probability": 0.9}
        ),
    )
    transcript = await SarvamSTT(_client(replay)).transcribe(ONE_SECOND_PCM, "pcm_s16le_16k", None)
    assert transcript.text == "ok"
    assert len(replay.requests) == 2


async def test_persistent_rate_limit_raises_after_two_retries() -> None:
    replay = Replay(httpx.Response(429, json={"error": "quota"}))
    with pytest.raises(ProviderRateLimited):
        await SarvamTTS(_client(replay)).synthesize("hi", "en-IN", "priya", 1.0)
    assert len(replay.requests) == 3


async def test_bad_request_is_not_retried_and_hides_the_key() -> None:
    replay = Replay(httpx.Response(400, json={"error": "bad speaker"}))
    with pytest.raises(ProviderBadRequest) as info:
        await SarvamTTS(_client(replay)).synthesize("hi", "en-IN", "nobody", 1.0)
    assert len(replay.requests) == 1
    assert "key-123" not in str(info.value)
