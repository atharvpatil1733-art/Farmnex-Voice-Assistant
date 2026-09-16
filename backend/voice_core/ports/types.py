"""Shared value types used across ports. No vendor imports here."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

AudioFormat = Literal["pcm_s16le_16k", "wav_22050"]


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args_json: str


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class Done:
    usage: Usage


@dataclass(frozen=True)
class LLMError:
    code: str
    message: str
    retryable: bool = False


LLMEvent = TextDelta | ToolCall | Done | LLMError


@dataclass(frozen=True)
class Transcript:
    text: str
    language_code: str
    language_confidence: float
    duration_ms: int


@dataclass(frozen=True)
class AudioSegment:
    data: bytes
    fmt: AudioFormat
    duration_ms: int


@dataclass(frozen=True)
class Chunk:
    doc_slug: str
    doc_version: int
    heading: str
    text: str
    similarity: float


@dataclass(frozen=True)
class KBDocument:
    slug: str
    title: str
    domain: str
    version: int
    language: str
    status: Literal["active", "retired"]


@dataclass(frozen=True)
class KBChunk:
    heading: str
    text: str
    embedding: list[float]


@dataclass(frozen=True)
class Principal:
    user_ref: str
    roles: tuple[str, ...] = ()
    claims: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolDef:
    name: str
    kind: Literal["read", "write"]
    handler_type: Literal["mock", "http", "graphql", "python"]
    config: dict[str, Any]


@dataclass(frozen=True)
class ToolContext:
    user_ref: str
    language: str


@dataclass(frozen=True)
class ToolResult:
    status: Literal["ok", "error", "not_found", "forbidden", "invalid"]
    data: dict[str, Any] | None = None
    error_code: str | None = None
    user_message_key: str | None = None
    client_actions: tuple[dict[str, Any], ...] = ()
