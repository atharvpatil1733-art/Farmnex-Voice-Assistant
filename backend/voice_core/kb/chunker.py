from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml

_FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)
_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_SENTENCE_RE = re.compile(r"(?<=[.!?।])\s+")


class DocParseError(Exception):
    pass


@dataclass(frozen=True)
class ParsedDoc:
    front_matter: dict[str, Any]
    sections: tuple[tuple[str, str], ...]  # (heading, body)


@dataclass(frozen=True)
class TextChunk:
    chunk_index: int
    heading: str
    text: str
    token_count: int


def parse_document(raw: str) -> ParsedDoc:
    match = _FRONT_MATTER_RE.match(raw)
    if not match:
        raise DocParseError("missing YAML front matter (expected leading --- block)")

    front_matter = yaml.safe_load(match.group(1)) or {}
    if not isinstance(front_matter, dict):
        raise DocParseError("front matter must be a YAML mapping")

    body = match.group(2)
    parts = _HEADING_RE.split(body)
    headings = parts[1::2]
    contents = [c.strip() for c in parts[2::2]]
    sections = tuple(zip(headings, contents, strict=True))

    return ParsedDoc(front_matter=front_matter, sections=sections)


def _split_long_section(text: str, max_words: int) -> list[str]:
    words = text.split()
    if len(words) <= max_words:
        return [text]

    sentences = _SENTENCE_RE.split(text)
    pieces: list[str] = []
    current: list[str] = []
    current_words = 0
    for sentence in sentences:
        sentence_words = len(sentence.split())
        if current and current_words + sentence_words > max_words:
            pieces.append(" ".join(current))
            current, current_words = [], 0
        current.append(sentence)
        current_words += sentence_words
    if current:
        pieces.append(" ".join(current))
    return pieces


def build_chunks(
    title: str, sections: tuple[tuple[str, str], ...], max_tokens: int = 350
) -> list[TextChunk]:
    max_words = int(max_tokens * 0.75)
    chunks: list[TextChunk] = []
    for heading, body in sections:
        for piece in _split_long_section(body, max_words):
            prefixed = f"{title} › {heading}\n\n{piece}"
            chunks.append(
                TextChunk(
                    chunk_index=len(chunks),
                    heading=heading,
                    text=prefixed,
                    token_count=len(prefixed.split()),
                )
            )
    return chunks
