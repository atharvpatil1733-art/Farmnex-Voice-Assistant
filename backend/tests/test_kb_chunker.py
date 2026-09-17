from __future__ import annotations

import pytest

from voice_core.kb.chunker import DocParseError, build_chunks, parse_document

SAMPLE = """---
slug: sample-basics
title: Sample
domain: sample_domain
language: en-IN
version: 1
status: active
---

## First question?
Short answer one.

## Second question?
Short answer two.
"""


def test_parse_document_extracts_front_matter_and_sections() -> None:
    parsed = parse_document(SAMPLE)
    assert parsed.front_matter["slug"] == "sample-basics"
    assert parsed.front_matter["version"] == 1
    assert parsed.sections == (
        ("First question?", "Short answer one."),
        ("Second question?", "Short answer two."),
    )


def test_parse_document_without_front_matter_raises() -> None:
    with pytest.raises(DocParseError):
        parse_document("## Just a heading\nbody")


def test_build_chunks_prefixes_title_and_heading() -> None:
    parsed = parse_document(SAMPLE)
    chunks = build_chunks(parsed.front_matter["title"], parsed.sections)

    assert len(chunks) == 2
    assert chunks[0].text.startswith("Sample › First question?")
    assert chunks[0].heading == "First question?"
    assert chunks[0].chunk_index == 0
    assert chunks[1].chunk_index == 1


def test_build_chunks_splits_a_long_section_on_sentence_boundaries() -> None:
    long_body = " ".join(f"Sentence number {i}." for i in range(200))
    sections = (("Long heading", long_body),)

    chunks = build_chunks("Title", sections, max_tokens=40)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.heading == "Long heading"
        assert chunk.token_count <= 40  # approx budget honored per piece
