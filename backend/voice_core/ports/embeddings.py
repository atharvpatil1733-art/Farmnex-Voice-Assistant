from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    dim: int
    model_id: str

    async def embed(
        self, texts: list[str], kind: Literal["query", "document"]
    ) -> list[list[float]]: ...
