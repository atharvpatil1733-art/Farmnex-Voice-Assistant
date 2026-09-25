from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from voice_core.adapters.embeddings_factory import build_embeddings
from voice_core.adapters.fakes.auth import FakeAuthVerifier
from voice_core.adapters.fakes.conversation import FakeConversationStore
from voice_core.adapters.fakes.llm import FakeLLM
from voice_core.adapters.fakes.stt import FakeSTT
from voice_core.adapters.fakes.tts import FakeTTS
from voice_core.adapters.sarvam.client import SarvamClient
from voice_core.adapters.sarvam.stt import SarvamSTT
from voice_core.adapters.sarvam.tts import SarvamTTS
from voice_core.adapters.supabase.conversation import SupabaseConversationStore
from voice_core.config import Settings, get_settings
from voice_core.packs.loader import LoadedPack, load_pack
from voice_core.ports.knowledge import KnowledgeStore
from voice_core.ports.llm import LLMProvider
from voice_core.ports.store import ConversationStore
from voice_core.ports.stt import STTProvider
from voice_core.ports.tts import TTSProvider
from voice_core.ports.types import Principal
from voice_core.tools.handlers.graphql import GraphQLToolHandler
from voice_core.tools.handlers.http import HttpToolHandler
from voice_core.tools.handlers.mock import MockToolHandler
from voice_core.tools.handlers.router import HandlerRouter
from voice_core.tools.registry import ToolRegistry
from voice_core.transport.rest import router as chat_router
from voice_core.transport.ws import VoiceDeps
from voice_core.transport.ws import router as voice_router

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


logger = logging.getLogger(__name__)


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


def _check_host_api_transport(settings: Settings, *, uses_host_api: bool) -> None:
    # forward_user_jwt sends the user's session JWT to the host API; never over plain http.
    if uses_host_api and settings.app_env != "dev":
        if not settings.host_api_base_url.startswith("https://"):
            raise RuntimeError("HOST_API_BASE_URL must use https outside app_env=dev")


def _build_conversation_store(settings: Settings) -> ConversationStore:
    if settings.database_url:
        return SupabaseConversationStore(
            database_url=settings.database_url,
            statement_cache_size=settings.db_statement_cache_size,
        )
    if settings.app_env != "dev":
        # Pending actions and the audit trail must survive restarts outside dev.
        raise RuntimeError("DATABASE_URL is required outside app_env=dev (confirmation gate)")
    return FakeConversationStore()


PLACEHOLDER_SPEAKER = "priya"  # until a voice is chosen by audition (docs/STATUS.md)


def _choose_speaker(settings: Settings, pack: LoadedPack) -> str:
    speaker = settings.tts_speaker or pack.tts_speaker
    if not speaker:
        if settings.app_env != "dev":
            raise RuntimeError("choose a TTS voice (pack voice.tts_speaker or TTS_SPEAKER)")
        logger.warning("no tts speaker chosen yet; using placeholder %r", PLACEHOLDER_SPEAKER)
        return PLACEHOLDER_SPEAKER
    return speaker


def _build_speech(
    settings: Settings,
) -> tuple[STTProvider, TTSProvider, SarvamClient | None]:
    needs_sarvam = "sarvam" in (settings.stt_provider, settings.tts_provider)
    if needs_sarvam and not settings.sarvam_api_key:
        if settings.app_env != "dev":
            raise RuntimeError("STT/TTS_PROVIDER=sarvam needs SARVAM_API_KEY outside app_env=dev")
        # Dev only: keep the text API usable while the voice key isn't configured yet.
        logger.warning("SARVAM_API_KEY not set: /v1/voice uses fake STT/TTS (dev only)")
        needs_sarvam = False
    client = SarvamClient(settings.sarvam_api_key) if needs_sarvam else None
    stt: STTProvider = (
        SarvamSTT(client, model=settings.stt_model, mode=settings.stt_mode)
        if client is not None and settings.stt_provider == "sarvam"
        else FakeSTT()
    )
    tts: TTSProvider = (
        SarvamTTS(client, model=settings.tts_model)
        if client is not None and settings.tts_provider == "sarvam"
        else FakeTTS()
    )
    return stt, tts, client


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
    _check_host_api_transport(
        settings,
        uses_host_api=any(t["handler"]["type"] in ("http", "graphql") for t in pack.tools),
    )
    http_handler = HttpToolHandler(
        settings.host_api_base_url,
        auth_mode=settings.host_api_auth_mode,
        service_token=settings.host_api_service_token,
    )
    graphql_handler = GraphQLToolHandler(
        settings.host_api_base_url,
        auth_mode=settings.host_api_auth_mode,
        service_token=settings.host_api_service_token,
    )
    app.state.tool_handler = HandlerRouter(
        {
            "mock": MockToolHandler(pack.pack_dir),
            "http": http_handler,
            "graphql": graphql_handler,
        }
    )
    app.state.llm = _build_llm(settings)
    app.state.embeddings = embeddings
    app.state.knowledge_store = knowledge_store
    app.state.auto_rag_min_sim = settings.auto_rag_min_sim
    app.state.auth_verifier = _build_auth_verifier(settings)
    conversation_store = _build_conversation_store(settings)
    app.state.conversation_store = conversation_store
    stt, tts, sarvam_client = _build_speech(settings)
    app.state.voice_deps = VoiceDeps(
        pack=pack,
        registry=app.state.registry,
        handler=app.state.tool_handler,
        llm=app.state.llm,
        store=conversation_store,
        auth_verifier=app.state.auth_verifier,
        stt=stt,
        tts=tts,
        speaker=_choose_speaker(settings, pack),
        pace=pack.speech_pace,
        max_utterance_ms=settings.max_utterance_seconds * 1000,
        embeddings=embeddings,
        knowledge_store=knowledge_store,
        auto_rag_min_sim=settings.auto_rag_min_sim,
    )

    yield

    if knowledge_store is not None:
        await knowledge_store.close()  # type: ignore[attr-defined]
    if isinstance(conversation_store, SupabaseConversationStore):
        await conversation_store.close()
    if sarvam_client is not None:
        await sarvam_client.aclose()
    await http_handler.aclose()
    await graphql_handler.aclose()


app = FastAPI(title="voice-core", lifespan=lifespan)
app.include_router(chat_router, prefix="/v1")
app.include_router(voice_router, prefix="/v1")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
