from __future__ import annotations

from voice_core.adapters.fakes.embeddings import FakeEmbedding
from voice_core.adapters.fakes.knowledge import FakeKnowledgeStore
from voice_core.kb.retriever import auto_retrieve, search_knowledge
from voice_core.ports.types import Chunk


async def test_search_knowledge_returns_matches_above_threshold() -> None:
    store = FakeKnowledgeStore(
        [Chunk(doc_slug="pre-bidding-basics", doc_version=1, heading="h", text="t", similarity=0.8)]
    )
    embeddings = FakeEmbedding(dim=4)

    chunks = await search_knowledge(
        query="how long is bidding open",
        pack_id="p",
        language="en-IN",
        embeddings=embeddings,
        store=store,
        min_similarity=0.3,
    )

    assert len(chunks) == 1
    assert chunks[0].doc_slug == "pre-bidding-basics"


async def test_auto_retrieve_returns_nothing_below_threshold() -> None:
    store = FakeKnowledgeStore(
        [Chunk(doc_slug="pre-bidding-basics", doc_version=1, heading="h", text="t", similarity=0.1)]
    )
    embeddings = FakeEmbedding(dim=4)

    chunks = await auto_retrieve(
        query="anything",
        pack_id="p",
        language="en-IN",
        embeddings=embeddings,
        store=store,
        min_similarity=0.45,
    )

    assert chunks == []


async def test_auto_retrieve_returns_chunks_above_threshold() -> None:
    store = FakeKnowledgeStore(
        [Chunk(doc_slug="crop-rescue-basics", doc_version=2, heading="h", text="t", similarity=0.9)]
    )
    embeddings = FakeEmbedding(dim=4)

    chunks = await auto_retrieve(
        query="my produce will spoil",
        pack_id="p",
        language="en-IN",
        embeddings=embeddings,
        store=store,
        min_similarity=0.45,
    )

    assert len(chunks) == 1
    assert chunks[0].doc_slug == "crop-rescue-basics"
