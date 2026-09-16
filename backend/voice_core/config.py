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

    # LLM
    llm_provider: Literal["openai_compat", "sarvam", "gemini", "anthropic", "fake"] = "fake"
    llm_model: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_temperature: float = 0.2
    llm_timeout_s: float = 12.0

    # STT / TTS
    stt_provider: Literal["sarvam", "fake"] = "fake"
    stt_model: str = "saaras:v3"
    stt_mode: str = "transcribe"
    tts_provider: Literal["sarvam", "fake"] = "fake"
    tts_model: str = "bulbul:v3"
    sarvam_api_key: str = ""

    # Embeddings
    embedding_provider: Literal["local", "openai_compat", "fake"] = "fake"
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024
    auto_rag_min_sim: float = 0.45

    # Host app backend
    host_api_base_url: str = "http://localhost:9000"
    host_api_auth_mode: Literal["forward_user_jwt", "service_token"] = "forward_user_jwt"
    host_api_service_token: str = ""

    # Limits & privacy
    max_utterance_seconds: int = 30
    rate_limit_turns_per_min: int = 20
    rate_limit_turns_per_day: int = 300
    store_audio: bool = False
    transcript_retention_days: int = 90


@lru_cache
def get_settings() -> Settings:
    return Settings()
