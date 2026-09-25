from __future__ import annotations

import io
import wave

import pytest

from voice_core.config import get_settings

pytestmark = pytest.mark.live

_settings = get_settings()
_skip = pytest.mark.skipif(
    not _settings.sarvam_api_key, reason="SARVAM_API_KEY not set (backend/.env)"
)


@_skip
@pytest.mark.parametrize(
    ("language", "text", "expect"),
    [
        ("hi-IN", "मेरे प्याज़ पर सबसे ऊँची बोली कितनी आई है", "बोली"),
        ("mr-IN", "माझा माल घ्यायला गाडी कधी येणार आहे", "गाडी"),
        ("en-IN", "When will I get paid for my soybean", "paid"),
    ],
)
async def test_tts_then_stt_round_trip(language: str, text: str, expect: str) -> None:
    """Synthesize a sentence, transcribe it back, and check the words and language survive."""
    from voice_core.adapters.sarvam.client import SarvamClient
    from voice_core.adapters.sarvam.stt import SarvamSTT
    from voice_core.adapters.sarvam.tts import SarvamTTS

    client = SarvamClient(_settings.sarvam_api_key)
    try:
        speaker = _settings.tts_speaker or "priya"
        segment = await SarvamTTS(client, sample_rate=16_000).synthesize(
            text, language, speaker, 1.0
        )
        assert segment.duration_ms > 500
        with wave.open(io.BytesIO(segment.data), "rb") as wav:
            assert wav.getframerate() == 16_000
            pcm = wav.readframes(wav.getnframes())

        transcript = await SarvamSTT(client).transcribe(pcm, "pcm_s16le_16k", None)
    finally:
        await client.aclose()

    assert expect.lower() in transcript.text.lower()
    assert transcript.language_code == language
    assert transcript.language_confidence > 0.5
