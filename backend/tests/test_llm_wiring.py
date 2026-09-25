from __future__ import annotations

import pytest

from app.main import _build_chain_provider as main_build_chain_provider
from voice_core.config import Settings
from voice_core.evals.run import _build_chain_provider as eval_build_chain_provider


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg,arg-type]


@pytest.mark.parametrize(
    "build_chain_provider", [main_build_chain_provider, eval_build_chain_provider]
)
def test_missing_model_raises(build_chain_provider: object) -> None:
    with pytest.raises(ValueError, match="missing a model"):
        build_chain_provider("gemini", "", _settings())  # type: ignore[operator]


@pytest.mark.parametrize(
    "build_chain_provider", [main_build_chain_provider, eval_build_chain_provider]
)
def test_unknown_provider_raises(build_chain_provider: object) -> None:
    with pytest.raises(ValueError, match="unknown provider"):
        build_chain_provider("openrouter", "some-model", _settings())  # type: ignore[operator]


@pytest.mark.parametrize(
    "build_chain_provider", [main_build_chain_provider, eval_build_chain_provider]
)
def test_gemini_branch_never_falls_back_to_generic_llm_api_key(
    build_chain_provider: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: an unrelated vendor's key in LLM_API_KEY must never be sent to
    Gemini just because GEMINI_API_KEY was left blank (cross-vendor secret leak)."""
    from voice_core.adapters.gemini import llm as gemini_llm_module

    seen_keys: list[str] = []
    monkeypatch.setattr(
        gemini_llm_module,
        "GeminiLLM",
        lambda api_key, model, **_: seen_keys.append(api_key) or object(),
    )
    settings = _settings(gemini_api_key="the-real-gemini-key", llm_api_key="unrelated-vendor-key")
    build_chain_provider("gemini", "gemini-2.5-flash", settings)  # type: ignore[operator]
    assert seen_keys == ["the-real-gemini-key"]


@pytest.mark.parametrize(
    "build_chain_provider", [main_build_chain_provider, eval_build_chain_provider]
)
def test_groq_uses_groq_credentials(build_chain_provider: object) -> None:
    from voice_core.adapters.openai_compat.llm import OpenAICompatLLM

    settings = _settings(groq_api_key="groq-key", groq_base_url="https://api.groq.com/openai/v1")
    provider = build_chain_provider("groq", "openai/gpt-oss-20b", settings)  # type: ignore[operator]
    assert isinstance(provider, OpenAICompatLLM)


def test_chain_links_are_built_single_attempt() -> None:
    from app.main import _build_chain_provider
    from voice_core.config import Settings

    settings = Settings(gemini_api_key="g", groq_api_key="q")
    assert _build_chain_provider("gemini", "gemini-2.5-flash", settings)._max_attempts == 1
    assert _build_chain_provider("groq", "openai/gpt-oss-20b", settings)._max_attempts == 1


def test_groq_gpt_oss_links_use_configured_reasoning_effort() -> None:
    from app.main import _build_chain_provider
    from voice_core.config import Settings

    settings = Settings(groq_api_key="q", llm_reasoning_effort="low")
    link = _build_chain_provider("groq", "openai/gpt-oss-20b", settings)
    assert link._extra_body == {"reasoning_effort": "low", "include_reasoning": False}
    other = _build_chain_provider("groq", "llama-3.3-70b-versatile", settings)
    assert other._extra_body is None
