from __future__ import annotations

from typing import Protocol, runtime_checkable

from voice_core.ports.types import Chunk, KBChunk, KBDocument


@runtime_checkable
class KnowledgeStore(Protocol):
    async def match(
        self,
        pack_id: str,
        embedding: list[float],
        k: int,
        min_similarity: float,
        domains: list[str] | None,
        prefer_language: str | None = None,
    ) -> list[Chunk]: ...

    async def publish_document(self, doc: KBDocument, chunks: list[KBChunk]) -> None: ...

    async def get_document_hash(
        self, pack_id: str, slug: str, language: str, version: int
    ) -> str | None: ...
