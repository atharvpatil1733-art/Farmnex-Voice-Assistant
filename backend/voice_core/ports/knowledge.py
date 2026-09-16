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
    ) -> list[Chunk]: ...

    async def publish_document(self, doc: KBDocument, chunks: list[KBChunk]) -> None: ...
