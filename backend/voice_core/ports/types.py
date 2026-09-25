"""Shared value types used across ports. No vendor imports here."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

AudioFormat = Literal["pcm_s16le_16k", "wav_22050", "mp3_24k"]


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
    pack_id: str
    slug: str
    title: str
    domain: str
    version: int
    language: str
    status: Literal["draft", "active", "retired"]
    content_hash: str
    audience: str | None = None
    source_path: str | None = None
    effective_from: str | None = None  # ISO 8601; None means "now"


@dataclass(frozen=True)
class KBChunk:
    chunk_index: int
    heading: str
    text: str
    embedding: list[float]
    embedding_model: str
    token_count: int = 0


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
    # Set only when executing a confirmed write; http/graphql handlers send it to the host.
    idempotency_key: str | None = None
    # The caller's own session JWT, for handlers in `forward_user_jwt` mode. Never logged.
    auth_token: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class ToolResult:
    status: Literal["ok", "error", "not_found", "forbidden", "invalid"]
    data: dict[str, Any] | None = None
    error_code: str | None = None
    user_message_key: str | None = None
    client_actions: tuple[dict[str, Any], ...] = ()


PendingStatus = Literal[
    "pending", "executing", "executed_ok", "executed_error", "cancelled", "expired"
]
OPEN_PENDING_STATUSES: tuple[PendingStatus, ...] = ("pending", "executing")


@dataclass(frozen=True)
class PendingAction:
    """A proposed write awaiting the user's explicit yes (SPEC §7). Stored server-side;
    execution always uses these stored `args`, never anything re-supplied by a client."""

    id: str
    conversation_id: str
    user_ref: str
    tool_name: str
    args: dict[str, Any]
    summary: str
    language: str
    status: PendingStatus
    idempotency_key: str
    expires_at: datetime
    confirmed_via: Literal["voice", "button"] | None = None


@dataclass(frozen=True)
class ToolInvocation:
    """One audit row in voice.tool_invocations."""

    user_ref: str
    tool_name: str
    kind: Literal["read", "write"]
    args: dict[str, Any]
    status: Literal["ok", "error", "not_found", "forbidden", "invalid", "timeout"]
    conversation_id: str | None = None
    pending_action_id: str | None = None
    turn_id: str | None = None
    error_code: str | None = None
    duration_ms: int | None = None
