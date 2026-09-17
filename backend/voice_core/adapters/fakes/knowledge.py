from __future__ import annotations

from voice_core.ports.types import Chunk, KBChunk, KBDocument


class FakeKnowledgeStore:
    """In-memory store: match() returns whatever chunks were configured, ignoring the vector."""

    def __init__(self, chunks: list[Chunk] | None = None) -> None:
        self._chunks = chunks or []
        self.published: list[tuple[KBDocument, list[KBChunk]]] = []

    async def match(
        self,
        pack_id: str,
        embedding: list[float],
        k: int,
        min_similarity: float,
        domains: list[str] | None,
        prefer_language: str | None = None,
    ) -> list[Chunk]:
        return [c for c in self._chunks if c.similarity >= min_similarity][:k]

    async def publish_document(self, doc: KBDocument, chunks: list[KBChunk]) -> None:
        self.published.append((doc, chunks))

    async def get_document_hash(
        self, pack_id: str, slug: str, language: str, version: int
    ) -> str | None:
        for doc, _ in reversed(self.published):
            if (doc.pack_id, doc.slug, doc.language, doc.version) == (
                pack_id,
                slug,
                language,
                version,
            ):
                return doc.content_hash
        return None
