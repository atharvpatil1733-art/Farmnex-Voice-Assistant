from __future__ import annotations

from voice_core.config import Settings
from voice_core.ports.embeddings import EmbeddingProvider


def build_embeddings(settings: Settings) -> EmbeddingProvider:
    """Single place that turns EMBEDDING_PROVIDER into an adapter.

    Raises on a provider with no adapter rather than silently substituting fake vectors —
    a fake embedder pointed at a real knowledge store would ingest meaningless embeddings.
    """
    if settings.embedding_provider == "gemini":
        if not settings.gemini_api_key:
            raise ValueError("EMBEDDING_PROVIDER=gemini requires GEMINI_API_KEY")
        from voice_core.adapters.gemini.embeddings import GeminiEmbedding

        return GeminiEmbedding(api_key=settings.gemini_api_key, dim=settings.embedding_dim)
    if settings.embedding_provider == "fake":
        from voice_core.adapters.fakes.embeddings import FakeEmbedding

        return FakeEmbedding(dim=settings.embedding_dim)
    raise ValueError(
        f"EMBEDDING_PROVIDER={settings.embedding_provider!r} has no adapter yet "
        "(supported: gemini, fake)"
    )
