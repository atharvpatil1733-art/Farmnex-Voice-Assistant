from __future__ import annotations

from typing import Literal


class FakeEmbedding:
    """Deterministic, non-semantic embeddings: hash-based, fixed dimension."""

    model_id = "fake-embed-v1"

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim

    async def embed(
        self, texts: list[str], kind: Literal["query", "document"]
    ) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        seed = sum(text.encode("utf-8")) or 1
        return [((seed * (i + 1)) % 97) / 97 for i in range(self.dim)]
