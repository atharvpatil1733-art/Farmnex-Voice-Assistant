from __future__ import annotations

import pytest

from voice_core.config import get_settings

pytestmark = pytest.mark.live

_settings = get_settings()


@pytest.mark.skipif(not _settings.groq_api_key, reason="GROQ_API_KEY not set (backend/.env)")
@pytest.mark.parametrize(
    ("language", "text", "expect"),
    [
        ("hi-IN", "मेरे प्याज़ पर सबसे ऊँची बोली कितनी आई है", "बोली"),
        ("mr-IN", "माझ्या कांद्याला सगळ्यात जास्त बोली किती आली", "कांद्याला"),
        ("en-IN", "When will I get paid for my soybean", "paid"),
    ],
)
async def test_edge_tts_then_groq_whisper_round_trip(language: str, text: str, expect: str) -> None:
    """Free voice stack end to end: edge-tts speaks a sentence, Groq Whisper hears it back."""
    from voice_core.adapters.edge.tts import EdgeTTS
    from voice_core.adapters.groq.stt import GroqWhisperSTT

    segment = await EdgeTTS().synthesize(text, language, "", 1.0)
    assert segment.fmt == "mp3_24k" and segment.duration_ms > 500

    stt = GroqWhisperSTT(
        _settings.groq_api_key, base_url=_settings.groq_base_url, model=_settings.groq_stt_model
    )
    try:
        transcript = await stt.transcribe(segment.data, "mp3_24k", language)
    finally:
        await stt.aclose()

    assert expect.lower() in transcript.text.lower()
    assert transcript.language_code == language
