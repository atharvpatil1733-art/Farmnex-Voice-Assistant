"""Pydantic models of docs/PROTOCOL.md (WebSocket protocol v1). The contract test parses every
JSON example in that document against these models, so the two cannot drift silently."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

PROTOCOL_VERSION = 1


class _Msg(BaseModel):
    # Additive fields are allowed within a protocol version (PROTOCOL.md "Version bump rules").
    model_config = ConfigDict(extra="allow")
    id: str | None = None


# ---------------------------------------------------------------- client -> server


class AudioIn(BaseModel):
    encoding: Literal["pcm_s16le"]
    sample_rate: Literal[16000]
    channels: Literal[1]


class ClientInfo(BaseModel):
    platform: str
    app_version: str
    pkg_version: str


class SessionStart(_Msg):
    type: Literal["session.start"]
    token: str = Field(repr=False)
    language: str | None = None
    input_mode: Literal["push_to_talk", "tap_to_toggle", "streaming"] = "push_to_talk"
    client: ClientInfo | None = None
    audio_in: AudioIn | None = None


class AuthRefresh(_Msg):
    type: Literal["auth.refresh"]
    token: str = Field(repr=False)


class AudioStart(_Msg):
    type: Literal["audio.start"]
    utterance_id: str


class AudioEnd(_Msg):
    type: Literal["audio.end"]
    utterance_id: str


class AudioCancel(_Msg):
    type: Literal["audio.cancel"]
    utterance_id: str


class TextInput(_Msg):
    type: Literal["text.input"]
    utterance_id: str
    text: str = Field(min_length=1, max_length=2000)


class ConfirmResponse(_Msg):
    type: Literal["confirm.response"]
    action_id: str
    decision: Literal["confirm", "cancel"]


class Interrupt(_Msg):
    type: Literal["interrupt"]
    reason: Literal["user_tap", "barge_in"] = "user_tap"


class LanguageSet(_Msg):
    type: Literal["language.set"]
    language: str


class ClientActionResult(_Msg):
    type: Literal["client_action.result"]
    action_id: str
    status: Literal["ok", "failed"]


class Ping(_Msg):
    type: Literal["ping"]


class SessionEnd(_Msg):
    type: Literal["session.end"]


ClientMessage = Annotated[
    SessionStart
    | AuthRefresh
    | AudioStart
    | AudioEnd
    | AudioCancel
    | TextInput
    | ConfirmResponse
    | Interrupt
    | LanguageSet
    | ClientActionResult
    | Ping
    | SessionEnd,
    Field(discriminator="type"),
]
CLIENT_MESSAGE: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


# ---------------------------------------------------------------- server -> client


class AudioOut(BaseModel):
    encoding: Literal["wav"]
    sample_rate: int


class SessionReady(_Msg):
    type: Literal["session.ready"] = "session.ready"
    session_id: str
    conversation_id: str
    language: str
    speaker: str
    limits: dict[str, int]
    audio_out: AudioOut
    protocol_version: int = PROTOCOL_VERSION


StateValue = Literal[
    "listening", "transcribing", "thinking", "speaking", "awaiting_confirmation", "idle"
]


class State(_Msg):
    type: Literal["state"] = "state"
    value: StateValue


class TranscriptPartial(_Msg):
    type: Literal["transcript.partial"] = "transcript.partial"
    utterance_id: str
    text: str


class TranscriptFinal(_Msg):
    type: Literal["transcript.final"] = "transcript.final"
    utterance_id: str
    text: str
    language: str
    confidence: float


class ToolActivity(_Msg):
    type: Literal["tool.activity"] = "tool.activity"
    turn_id: str
    phase: Literal["started", "finished"]
    label: str | None = None


class AssistantTextDelta(_Msg):
    type: Literal["assistant.text.delta"] = "assistant.text.delta"
    turn_id: str
    text: str


class AssistantTextFinal(_Msg):
    type: Literal["assistant.text.final"] = "assistant.text.final"
    turn_id: str
    text: str
    language: str


class AudioSegmentHeader(_Msg):
    """Always immediately followed by exactly one binary frame with `byte_length` bytes."""

    type: Literal["audio.segment"] = "audio.segment"
    turn_id: str
    seq: int = Field(ge=0)
    encoding: Literal["wav"] = "wav"
    sample_rate: int
    byte_length: int = Field(ge=0)
    is_last: bool


class ConfirmRequest(_Msg):
    type: Literal["confirm.request"] = "confirm.request"
    action_id: str
    tool: str
    summary: str
    expires_at: str
    labels: dict[str, str]


class ActionResult(_Msg):
    type: Literal["action.result"] = "action.result"
    action_id: str
    status: Literal["executed_ok", "executed_error", "cancelled", "expired"]
    message: str | None = None


class ClientAction(_Msg):
    type: Literal["client_action"] = "client_action"
    action_id: str
    name: str
    params: dict[str, Any] = Field(default_factory=dict)


class TurnEnd(_Msg):
    type: Literal["turn.end"] = "turn.end"
    turn_id: str
    interrupted: bool
    latency_ms: dict[str, int] = Field(default_factory=dict)


ErrorCode = Literal[
    "AUTH_FAILED",
    "CONSENT_REQUIRED",
    "PROTOCOL_ERROR",
    "UTTERANCE_TOO_LONG",
    "STT_EMPTY",
    "STT_FAILED",
    "LLM_TIMEOUT",
    "LLM_FAILED",
    "TOOL_FAILED",
    "TTS_FAILED",
    "RATE_LIMITED",
    "INTERNAL",
]


class Error(_Msg):
    type: Literal["error"] = "error"
    code: ErrorCode
    message: str
    retryable: bool


class Pong(_Msg):
    type: Literal["pong"] = "pong"


ServerMessage = Annotated[
    SessionReady
    | State
    | TranscriptPartial
    | TranscriptFinal
    | ToolActivity
    | AssistantTextDelta
    | AssistantTextFinal
    | AudioSegmentHeader
    | ConfirmRequest
    | ActionResult
    | ClientAction
    | TurnEnd
    | Error
    | Pong,
    Field(discriminator="type"),
]
SERVER_MESSAGE: TypeAdapter[ServerMessage] = TypeAdapter(ServerMessage)

# Close codes (PROTOCOL.md "Connection lifecycle")
CLOSE_PROTOCOL_ERROR = 4400
CLOSE_AUTH_FAILED = 4401
CLOSE_NO_CONSENT = 4403
CLOSE_RATE_LIMITED = 4429
CLOSE_SERVER_ERROR = 4500
