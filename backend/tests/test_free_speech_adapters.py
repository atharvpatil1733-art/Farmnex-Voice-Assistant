from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from voice_core.adapters.edge import tts as edge_module
from voice_core.adapters.edge.tts import EdgeTTS, edge_rate
from voice_core.adapters.groq.stt import GroqWhisperSTT
from voice_core.ports.errors import ProviderBadRequest, ProviderRateLimited, ProviderUnavailable
from voice_core.ports.stt import STTProvider
from voice_core.ports.tts import TTSProvider

ONE_SECOND = b"\x01\x00" * 16_000


def _groq(handler: Any) -> GroqWhisperSTT:
    return GroqWhisperSTT("gsk-test", transport=httpx.MockTransport(handler))


def test_adapters_satisfy_ports() -> None:
    assert isinstance(GroqWhisperSTT("k"), STTProvider)
    assert isinstance(EdgeTTS(), TTSProvider)


async def test_groq_sends_wav_with_language_hint_and_maps_response() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "text": " माझा माल ",
                "language": "Marathi",
                "duration": 1.0,
                "segments": [{"text": " माझा माल ", "no_speech_prob": 0.01}],
            },
        )

    transcript = await _groq(handler).transcribe(ONE_SECOND, "pcm_s16le_16k", "mr-IN")

    assert transcript.text == "माझा माल"
    assert transcript.language_code == "mr-IN"
    assert transcript.language_confidence == 0.0  # Whisper gives none: no auto-switching
    body = seen[0].content
    assert seen[0].url.path.endswith("/audio/transcriptions")
    assert seen[0].headers["Authorization"] == "Bearer gsk-test"
    assert b"whisper-large-v3" in body and b"verbose_json" in body
    assert b'name="language"\r\n\r\nmr\r\n' in body  # ISO-639-1 hint, not BCP-47
    assert b"RIFF" in body


async def test_groq_drops_segments_whisper_flags_as_silence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "text": "Thank you.",
                "language": "English",
                "segments": [{"text": "Thank you.", "no_speech_prob": 0.93}],
            },
        )

    transcript = await _groq(handler).transcribe(ONE_SECOND, "pcm_s16le_16k", "en-IN")
    assert transcript.text == ""


async def test_groq_skips_accidental_taps() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={})

    transcript = await _groq(handler).transcribe(b"\x00\x00" * 1_600, "pcm_s16le_16k", "hi-IN")
    assert transcript.text == "" and calls == []


@pytest.mark.parametrize(
    ("status", "error"),
    [(429, ProviderRateLimited), (503, ProviderUnavailable), (400, ProviderBadRequest)],
)
async def test_groq_errors_map_and_never_include_the_key(
    status: int, error: type[Exception]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "nope"}})

    with pytest.raises(error) as info:
        await _groq(handler).transcribe(ONE_SECOND, "pcm_s16le_16k", "hi-IN")
    assert "gsk-test" not in str(info.value)


def test_edge_rate_and_voice_selection() -> None:
    assert edge_rate(1.0) == "+0%"
    assert edge_rate(0.95) == "-5%"
    assert edge_rate(1.2) == "+20%"
    assert edge_rate(9) == "+100%"
    tts = EdgeTTS()
    assert tts.voice_for("mr-IN", "priya") == "mr-IN-AarohiNeural"
    assert tts.voice_for("hi-IN", "") == "hi-IN-SwaraNeural"
    assert tts.voice_for("en-IN", "en-IN-NeerjaExpressiveNeural") == "en-IN-NeerjaExpressiveNeural"
    assert tts.voice_for("hi-IN", "en-IN-NeerjaNeural") == "hi-IN-SwaraNeural"  # wrong language


class FakeCommunicate:
    calls: list[dict[str, Any]] = []
    fail_times = 0

    def __init__(self, text: str, voice: str, **kwargs: Any) -> None:
        FakeCommunicate.calls.append({"text": text, "voice": voice, **kwargs})

    async def stream(self) -> AsyncIterator[dict[str, Any]]:
        if FakeCommunicate.fail_times > 0:
            FakeCommunicate.fail_times -= 1
            raise edge_module.edge_errors.NoAudioReceived("flaky")
        yield {"type": "WordBoundary", "offset": 0}
        yield {"type": "audio", "data": b"\xff\xf3" * 3000}
        yield {"type": "audio", "data": b"\xff\xf3" * 3000}


@pytest.fixture
def fake_edge(monkeypatch: pytest.MonkeyPatch) -> type[FakeCommunicate]:
    FakeCommunicate.calls = []
    FakeCommunicate.fail_times = 0
    monkeypatch.setattr(edge_module.edge_tts, "Communicate", FakeCommunicate)

    async def instant(_: float) -> None:
        return None

    monkeypatch.setattr(edge_module.asyncio, "sleep", instant)
    return FakeCommunicate


async def test_edge_collects_audio_chunks_as_mp3(fake_edge: type[FakeCommunicate]) -> None:
    segment = await EdgeTTS().synthesize("नमस्ते", "hi-IN", "whatever", 0.95)
    assert segment.fmt == "mp3_24k"
    assert len(segment.data) == 12_000
    assert segment.duration_ms == 12_000 * 8 // 48  # 48 kbps CBR
    assert fake_edge.calls[0]["voice"] == "hi-IN-SwaraNeural"
    assert fake_edge.calls[0]["rate"] == "-5%"


async def test_edge_retries_once_then_reports_unavailable(fake_edge: type[FakeCommunicate]) -> None:
    fake_edge.fail_times = 1
    assert (await EdgeTTS().synthesize("hi", "en-IN", "", 1.0)).data
    fake_edge.fail_times = 5
    with pytest.raises(ProviderUnavailable):
        await EdgeTTS().synthesize("hi", "en-IN", "", 1.0)
