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
    ) -> list[Chunk]:
        return [c for c in self._chunks if c.similarity >= min_similarity][:k]

    async def publish_document(self, doc: KBDocument, chunks: list[KBChunk]) -> None:
        self.published.append((doc, chunks))
