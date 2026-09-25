from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from voice_core.adapters.embeddings_factory import build_embeddings
from voice_core.adapters.fakes.auth import FakeAuthVerifier
from voice_core.adapters.fakes.llm import FakeLLM
from voice_core.config import Settings, get_settings
from voice_core.packs.loader import load_pack
from voice_core.ports.knowledge import KnowledgeStore
from voice_core.ports.llm import LLMProvider
from voice_core.ports.types import Principal
from voice_core.tools.handlers.mock import MockToolHandler
from voice_core.tools.registry import ToolRegistry
from voice_core.transport.rest import router as chat_router

# Dev-only bearer token for manual testing / the M1 REST endpoint before a real Supabase
# AuthVerifier adapter exists. Never used outside app_env == "dev".
_DEV_TOKEN = "dev-token"  # nosec B105 - dev-only bearer token, not a real credential
_DEV_USER_REF = "dev-user"


def _build_chain_provider(entry_provider: str, model: str, settings: Settings) -> LLMProvider:
    if not model:
        raise ValueError(f"LLM_FALLBACK_CHAIN entry {entry_provider!r} is missing a model")
    if entry_provider == "gemini":
        from voice_core.adapters.gemini.llm import GeminiLLM

        return GeminiLLM(api_key=settings.gemini_api_key, model=model)
    if entry_provider == "groq":
        from voice_core.adapters.openai_compat.llm import OpenAICompatLLM

        return OpenAICompatLLM(
            api_key=settings.groq_api_key, model=model, base_url=settings.groq_base_url
        )
    raise ValueError(f"unknown provider {entry_provider!r} in LLM_FALLBACK_CHAIN")


def _build_llm(settings: Settings) -> LLMProvider:
    if settings.llm_fallback_chain:
        from voice_core.adapters.fallback.llm import FallbackLLM

        providers = []
        for entry in settings.llm_fallback_chain.split(","):
            entry_provider, _, model = entry.strip().partition(":")
            providers.append((entry, _build_chain_provider(entry_provider, model, settings)))
        return FallbackLLM(providers)
    if settings.llm_provider == "gemini":
        from voice_core.adapters.gemini.llm import GeminiLLM

        return GeminiLLM(api_key=settings.llm_api_key, model=settings.llm_model)
    if settings.llm_provider == "openai_compat":
        from voice_core.adapters.openai_compat.llm import OpenAICompatLLM

        return OpenAICompatLLM(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
        )
    return FakeLLM()


def _build_knowledge_store(settings: Settings) -> KnowledgeStore | None:
    if not settings.database_url:
        return None
    from voice_core.adapters.supabase.store import SupabaseKnowledgeStore

    return SupabaseKnowledgeStore(
        database_url=settings.database_url,
        statement_cache_size=settings.db_statement_cache_size,
    )


def _build_auth_verifier(settings: Settings) -> FakeAuthVerifier:
    if settings.app_env != "dev":
        raise RuntimeError(
            "no real AuthVerifier adapter exists yet (pre-M3/port-to-host); "
            f"refusing to start with app_env={settings.app_env!r} using the dev-token fake"
        )
    return FakeAuthVerifier({_DEV_TOKEN: Principal(user_ref=_DEV_USER_REF)})


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    backend_dir = Path(__file__).resolve().parents[1]
    packs_root = (backend_dir / settings.domain_packs_dir).resolve()

    pack = load_pack(settings.domain_pack, packs_root)
    embeddings = build_embeddings(settings)
    knowledge_store = _build_knowledge_store(settings)

    app.state.pack = pack
    app.state.registry = ToolRegistry(pack, embeddings=embeddings, knowledge_store=knowledge_store)
    app.state.tool_handler = MockToolHandler(pack.pack_dir)
    app.state.llm = _build_llm(settings)
    app.state.embeddings = embeddings
    app.state.knowledge_store = knowledge_store
    app.state.auto_rag_min_sim = settings.auto_rag_min_sim
    app.state.auth_verifier = _build_auth_verifier(settings)

    yield

    if knowledge_store is not None:
        await knowledge_store.close()  # type: ignore[attr-defined]


app = FastAPI(title="voice-core", lifespan=lifespan)
app.include_router(chat_router, prefix="/v1")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
