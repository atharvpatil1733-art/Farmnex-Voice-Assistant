from __future__ import annotations

from typing import Literal

from google import genai
from google.genai import types

_TASK_TYPE = {
    "query": "RETRIEVAL_QUERY",
    "document": "RETRIEVAL_DOCUMENT",
}


class GeminiEmbedding:
    model_id = "gemini-embedding-001"

    def __init__(self, api_key: str, dim: int = 1024) -> None:
        self._client = genai.Client(api_key=api_key)
        self.dim = dim

    async def embed(
        self, texts: list[str], kind: Literal["query", "document"]
    ) -> list[list[float]]:
        if not texts:
            return []
        response = await self._client.aio.models.embed_content(
            model=self.model_id,
            contents=texts,
            config=types.EmbedContentConfig(
                task_type=_TASK_TYPE[kind], output_dimensionality=self.dim
            ),
        )
        return [list(e.values or []) for e in (response.embeddings or [])]
