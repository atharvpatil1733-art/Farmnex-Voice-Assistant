from __future__ import annotations

from pathlib import Path

from voice_core.adapters.fakes.embeddings import FakeEmbedding
from voice_core.adapters.fakes.knowledge import FakeKnowledgeStore
from voice_core.kb.ingest import ingest_file

DOC = """---
slug: sample-basics
title: Sample
domain: sample_domain
language: en-IN
version: 1
status: active
effective_from: 2026-09-01
audience: farmer
---

## First question?
Short answer one.
"""


async def test_ingest_file_publishes_document_and_chunks(tmp_path: Path) -> None:
    path = tmp_path / "sample-basics.md"
    path.write_text(DOC, encoding="utf-8")

    store = FakeKnowledgeStore()
    embeddings = FakeEmbedding(dim=8)

    result = await ingest_file(path, "test_pack", store, embeddings)

    assert "ingested" in result
    assert len(store.published) == 1
    doc, chunks = store.published[0]
    assert doc.slug == "sample-basics"
    assert doc.pack_id == "test_pack"
    assert doc.status == "active"
    assert len(chunks) == 1
    assert len(chunks[0].embedding) == 8


async def test_ingest_file_skips_unchanged_content(tmp_path: Path) -> None:
    path = tmp_path / "sample-basics.md"
    path.write_text(DOC, encoding="utf-8")

    store = FakeKnowledgeStore()
    embeddings = FakeEmbedding(dim=8)

    await ingest_file(path, "test_pack", store, embeddings)
    result = await ingest_file(path, "test_pack", store, embeddings)

    assert "unchanged" in result
    assert len(store.published) == 1  # not re-published


async def test_ingest_file_dry_run_does_not_publish(tmp_path: Path) -> None:
    path = tmp_path / "sample-basics.md"
    path.write_text(DOC, encoding="utf-8")

    store = FakeKnowledgeStore()
    embeddings = FakeEmbedding(dim=8)

    result = await ingest_file(path, "test_pack", store, embeddings, dry_run=True)

    assert "would ingest" in result
    assert store.published == []
