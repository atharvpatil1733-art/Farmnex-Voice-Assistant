from __future__ import annotations

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


def test_missing_sarvam_key_falls_back_to_fakes_only_in_dev() -> None:
    from app.main import _build_speech
    from voice_core.adapters.fakes.stt import FakeSTT
    from voice_core.adapters.fakes.tts import FakeTTS

    dev = Settings(app_env="dev", stt_provider="sarvam", tts_provider="sarvam", sarvam_api_key="")
    stt, tts, client = _build_speech(dev)
    assert isinstance(stt, FakeSTT) and isinstance(tts, FakeTTS) and client is None

    prod = Settings(app_env="prod", stt_provider="sarvam", tts_provider="sarvam", sarvam_api_key="")
    with pytest.raises(RuntimeError, match="SARVAM_API_KEY"):
        _build_speech(prod)


def test_sarvam_key_builds_real_adapters() -> None:
    from app.main import _build_speech
    from voice_core.adapters.sarvam.stt import SarvamSTT
    from voice_core.adapters.sarvam.tts import SarvamTTS

    settings = Settings(stt_provider="sarvam", tts_provider="sarvam", sarvam_api_key="k")
    stt, tts, client = _build_speech(settings)
    assert isinstance(stt, SarvamSTT) and isinstance(tts, SarvamTTS) and client is not None
