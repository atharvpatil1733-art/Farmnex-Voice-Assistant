from __future__ import annotations

from pathlib import Path

import pytest

from app.main import _build_auth_verifier
from voice_core.adapters.fakes.auth import FakeAuthVerifier
from voice_core.config import Settings


def test_dev_env_gets_the_dev_token_fake_verifier() -> None:
    settings = Settings(app_env="dev")
    verifier = _build_auth_verifier(settings)
    assert isinstance(verifier, FakeAuthVerifier)


def test_non_dev_env_refuses_to_start_without_a_real_verifier() -> None:
    settings = Settings(app_env="prod")
    with pytest.raises(RuntimeError, match="no real AuthVerifier"):
        _build_auth_verifier(settings)


def test_host_api_must_be_https_outside_dev_when_tools_call_it() -> None:
    from app.main import _check_host_api_transport

    prod_http = Settings(app_env="prod", host_api_base_url="http://host.example")
    with pytest.raises(RuntimeError, match="https"):
        _check_host_api_transport(prod_http, uses_host_api=True)
    _check_host_api_transport(prod_http, uses_host_api=False)  # all-mock pack: fine
    _check_host_api_transport(
        Settings(app_env="prod", host_api_base_url="https://host.example"), uses_host_api=True
    )
    _check_host_api_transport(Settings(app_env="dev"), uses_host_api=True)


def test_missing_speech_key_falls_back_to_fakes_only_in_dev() -> None:
    from app.main import _build_speech
    from voice_core.adapters.fakes.stt import FakeSTT
    from voice_core.adapters.fakes.tts import FakeTTS

    dev = Settings(app_env="dev", stt_provider="sarvam", tts_provider="sarvam", sarvam_api_key="")
    speech = _build_speech(dev)
    assert isinstance(speech.stt, FakeSTT) and isinstance(speech.tts, FakeTTS)
    assert speech.closers == []

    prod = Settings(app_env="prod", stt_provider="sarvam", tts_provider="sarvam", sarvam_api_key="")
    with pytest.raises(RuntimeError, match="SARVAM_API_KEY"):
        _build_speech(prod)
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        _build_speech(
            Settings(app_env="prod", stt_provider="groq", tts_provider="fake", groq_api_key="")
        )


def test_sarvam_key_builds_real_adapters() -> None:
    from app.main import _build_speech
    from voice_core.adapters.sarvam.stt import SarvamSTT
    from voice_core.adapters.sarvam.tts import SarvamTTS

    speech = _build_speech(
        Settings(stt_provider="sarvam", tts_provider="sarvam", sarvam_api_key="k")
    )
    assert isinstance(speech.stt, SarvamSTT) and isinstance(speech.tts, SarvamTTS)
    assert (speech.audio_encoding, speech.audio_sample_rate) == ("wav", 22_050)


def test_free_providers_groq_whisper_and_edge_tts() -> None:
    from app.main import _build_speech, _choose_speaker
    from voice_core.adapters.edge.tts import EdgeTTS
    from voice_core.adapters.groq.stt import GroqWhisperSTT
    from voice_core.packs.loader import load_pack

    settings = Settings(app_env="prod", stt_provider="groq", tts_provider="edge", groq_api_key="g")
    speech = _build_speech(settings)
    assert isinstance(speech.stt, GroqWhisperSTT) and isinstance(speech.tts, EdgeTTS)
    assert (speech.audio_encoding, speech.audio_sample_rate) == ("mp3", 24_000)
    packs_root = Path(__file__).resolve().parents[2] / "domain_packs"
    # edge picks a female voice per language, so no speaker choice is required even in prod
    assert _choose_speaker(settings, load_pack("farm_marketplace", packs_root)) == "default-female"


async def test_warm_up_touches_embeddings_and_store_and_never_raises() -> None:
    from app.main import _warm_up
    from voice_core.adapters.fakes.embeddings import FakeEmbedding
    from voice_core.adapters.fakes.knowledge import FakeKnowledgeStore
    from voice_core.packs.loader import load_pack

    pack = load_pack("farm_marketplace", Path(__file__).resolve().parents[2] / "domain_packs")
    calls: list[str] = []

    class Spy(FakeKnowledgeStore):
        async def match(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            calls.append("match")
            return []

    class Broken(FakeKnowledgeStore):
        async def match(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise ConnectionError("db down")

    await _warm_up(pack, FakeEmbedding(dim=4), Spy())
    assert calls == ["match"]
    await _warm_up(pack, FakeEmbedding(dim=4), Broken())  # logged, not raised
    await _warm_up(pack, FakeEmbedding(dim=4), None)
