from __future__ import annotations

from voice_core.config import Settings


def test_defaults_are_safe_for_local_dev() -> None:
    settings = Settings(_env_file=None)

    assert settings.tool_mode == "mock"
    assert settings.llm_provider == "fake"
    assert settings.store_audio is False


def test_env_overrides_defaults(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "sarvam")
    monkeypatch.setenv("EMBEDDING_DIM", "512")

    settings = Settings(_env_file=None)

    assert settings.llm_provider == "sarvam"
    assert settings.embedding_dim == 512
