from __future__ import annotations

import pytest

from voice_core.adapters.embeddings_factory import build_embeddings
from voice_core.adapters.fakes.embeddings import FakeEmbedding
from voice_core.config import Settings


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_fake_provider_builds_fake_embedding() -> None:
    assert isinstance(build_embeddings(_settings(embedding_provider="fake")), FakeEmbedding)


@pytest.mark.parametrize("provider", ["local", "openai_compat"])
def test_provider_without_adapter_raises_instead_of_silently_going_fake(provider: str) -> None:
    with pytest.raises(ValueError, match="no adapter"):
        build_embeddings(_settings(embedding_provider=provider))


def test_gemini_requires_gemini_api_key() -> None:
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        build_embeddings(_settings(embedding_provider="gemini", gemini_api_key=""))


def test_gemini_uses_gemini_key_never_the_generic_llm_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _Capture:
        def __init__(self, api_key: str, dim: int = 1024) -> None:
            captured["api_key"] = api_key

    import voice_core.adapters.gemini.embeddings as gemini_embeddings

    monkeypatch.setattr(gemini_embeddings, "GeminiEmbedding", _Capture)
    build_embeddings(
        _settings(embedding_provider="gemini", gemini_api_key="g-key", llm_api_key="other-key")
    )
    assert captured["api_key"] == "g-key"
