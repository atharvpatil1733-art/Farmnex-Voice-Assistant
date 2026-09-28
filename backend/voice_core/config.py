from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    log_level: str = "INFO"
    domain_pack: str = "farm_marketplace"
    domain_packs_dir: Path = Path("../domain_packs")
    tool_mode: Literal["mock", "live"] = "mock"

    # Supabase / Postgres
    database_url: str = ""
    db_statement_cache_size: int = 0
    supabase_url: str = ""
    supabase_jwt_audience: str = "authenticated"
    supabase_jwks_url: str = ""
    supabase_jwt_secret: str = ""

    # LLM (single-provider mode — used when llm_fallback_chain is empty)
    llm_provider: Literal["openai_compat", "sarvam", "gemini", "anthropic", "fake"] = "fake"
    llm_model: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_temperature: float = 0.2
    llm_timeout_s: float = 12.0

    # LLM fallback chain mode — comma-separated "provider:model" entries tried in order,
    # e.g. "gemini:gemini-2.5-flash,groq:openai/gpt-oss-120b". Empty means single-provider
    # mode above is used instead. Each chain provider has its own credentials below.
    llm_fallback_chain: str = ""
    gemini_api_key: str = ""
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    # Groq gpt-oss reasoning models. Reasoning text is never sent back (fewer tokens against
    # the free 8K tokens/min). "medium" (Groq default): "low" was only ~0.15 s faster and
    # the golden suite showed weaker multi-step tool use with it (2026-09-27).
    llm_reasoning_effort: str = "medium"

    # STT / TTS
    stt_provider: Literal["sarvam", "groq", "fake"] = "fake"
    stt_model: str = "saaras:v3"  # Sarvam model
    groq_stt_model: str = "whisper-large-v3"  # best hi/mr accuracy in our probe, 2026-09-25
    groq_stt_model_fast: str = "whisper-large-v3-turbo"  # hi/en: faster, accepted trade-off
    groq_stt_accurate_languages: str = "mr-IN"  # comma-separated; these use groq_stt_model
    stt_mode: str = "transcribe"
    tts_provider: Literal["sarvam", "edge", "fake"] = "fake"
    tts_model: str = "bulbul:v3"
    tts_speaker: str = ""  # overrides pack.yaml voice.tts_speaker when set
    sarvam_api_key: str = ""

    # Embeddings
    embedding_provider: Literal["local", "openai_compat", "gemini", "fake"] = "fake"
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024
    auto_rag_min_sim: float = 0.45
    # Off: no knowledge search on every turn (~1.2 s); the LLM calls search_knowledge only
    # for how-to questions. Chosen for voice latency, 2026-09-27.
    auto_rag_enabled: bool = False

    # Host app backend
    host_api_base_url: str = "http://localhost:9000"
    host_api_auth_mode: Literal["forward_user_jwt", "service_token"] = "forward_user_jwt"
    host_api_service_token: str = ""

    # Limits & privacy
    max_utterance_seconds: int = 30
    # Play the pack filler if no answer is ready this long into thinking (seconds).
    voice_filler_after_s: float = 0.8
    rate_limit_turns_per_min: int = 20
    rate_limit_turns_per_day: int = 300
    store_audio: bool = False
    transcript_retention_days: int = 90


@lru_cache
def get_settings() -> Settings:
    return Settings()
