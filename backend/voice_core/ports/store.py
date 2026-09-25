from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from voice_core.ports.types import PendingAction, PendingStatus, ToolInvocation


@runtime_checkable
class ConversationStore(Protocol):
    """Durable conversation state: conversations, the confirmation gate's pending actions,
    and the tool audit trail (tables in supabase/migrations/0001_voice_core.sql)."""

    async def create_conversation(
        self,
        user_ref: str,
        pack_id: str,
        channel: Literal["app", "text_api", "eval"],
        language: str,
    ) -> str: ...

    async def get_conversation_owner(self, conversation_id: str) -> str | None:
        """user_ref that owns the conversation, or None if it doesn't exist."""
        ...

    async def get_open_pending(self, conversation_id: str) -> PendingAction | None:
        """The conversation's action in status pending/executing, if any (expired or not)."""
        ...

    async def upsert_pending(self, action: PendingAction) -> PendingAction:
        """Store `action` as the conversation's only open action, cancelling any other open one.
        If a row with the same idempotency key exists: a cancelled/expired/executed_error row is
        reopened as pending with the new summary/expiry; any other row is returned unchanged
        (so an already-executed identical write is detected, never repeated)."""
        ...

    async def transition(
        self,
        action_id: str,
        from_status: PendingStatus,
        to_status: PendingStatus,
        *,
        confirmed_via: Literal["voice", "button"] | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Atomically move an action from `from_status` to `to_status`. Returns False (and
        changes nothing) if it was not in `from_status`, which stops a double 'yes' executing
        twice."""
        ...

    async def record_invocation(self, invocation: ToolInvocation) -> None: ...

    async def add_message(
        self,
        conversation_id: str,
        *,
        turn_id: str,
        role: Literal["user", "assistant"],
        content: str,
        language: str | None,
        input_mode: Literal["voice", "text"] | None = None,
        stt_confidence: float | None = None,
        interrupted: bool = False,
        latency_ms: dict[str, int] | None = None,
    ) -> None:
        """Transcript row in voice.messages. Never raw audio (SPEC §16)."""
        ...

    async def get_preferred_language(self, user_ref: str) -> str | None: ...

    async def set_preferred_language(self, user_ref: str, language: str) -> None: ...
