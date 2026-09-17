from __future__ import annotations

from voice_core.ports.embeddings import EmbeddingProvider
from voice_core.ports.knowledge import KnowledgeStore
from voice_core.ports.types import Chunk


async def search_knowledge(
    *,
    query: str,
    pack_id: str,
    language: str,
    embeddings: EmbeddingProvider,
    store: KnowledgeStore,
    k: int = 3,
    min_similarity: float = 0.3,
    domains: list[str] | None = None,
) -> list[Chunk]:
    vectors = await embeddings.embed([query], kind="query")
    if not vectors:
        return []
    return await store.match(
        pack_id, vectors[0], k, min_similarity, domains, prefer_language=language
    )


async def auto_retrieve(
    *,
    query: str,
    pack_id: str,
    language: str,
    embeddings: EmbeddingProvider,
    store: KnowledgeStore,
    k: int = 3,
    min_similarity: float,
) -> list[Chunk]:
    """Per-turn automatic retrieval (SPEC §6 step 4): only inject chunks if the best match
    clears min_similarity; otherwise the LLM can still call search_knowledge explicitly."""
    chunks = await search_knowledge(
        query=query,
        pack_id=pack_id,
        language=language,
        embeddings=embeddings,
        store=store,
        k=k,
        min_similarity=min_similarity,
    )
    if not chunks or chunks[0].similarity < min_similarity:
        return []
    return chunks
