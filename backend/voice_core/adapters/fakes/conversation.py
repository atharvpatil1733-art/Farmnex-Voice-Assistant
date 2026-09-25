from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime
from typing import Any, Literal

from voice_core.ports.types import (
    OPEN_PENDING_STATUSES,
    PendingAction,
    PendingStatus,
    ToolInvocation,
)

_REOPENABLE: tuple[PendingStatus, ...] = ("cancelled", "expired", "executed_error")


class FakeConversationStore:
    """In-memory ConversationStore with the same semantics as the Postgres one."""

    def __init__(self) -> None:
        self.conversations: dict[str, str] = {}
        self.actions: dict[str, PendingAction] = {}
        self.invocations: list[ToolInvocation] = []
        self.messages: list[dict[str, Any]] = []
        self.preferred_languages: dict[str, str] = {}

    async def create_conversation(
        self,
        user_ref: str,
        pack_id: str,
        channel: Literal["app", "text_api", "eval"],
        language: str,
    ) -> str:
        conversation_id = str(uuid.uuid4())
        self.conversations[conversation_id] = user_ref
        return conversation_id

    async def get_conversation_owner(self, conversation_id: str) -> str | None:
        return self.conversations.get(conversation_id)

    async def get_open_pending(self, conversation_id: str) -> PendingAction | None:
        for action in self.actions.values():
            if action.conversation_id == conversation_id and action.status in OPEN_PENDING_STATUSES:
                return action
        return None

    async def upsert_pending(self, action: PendingAction) -> PendingAction:
        existing = next(
            (a for a in self.actions.values() if a.idempotency_key == action.idempotency_key), None
        )
        if existing is not None and existing.status not in _REOPENABLE:
            return existing

        executing = next(
            (
                a
                for a in self.actions.values()
                if a.conversation_id == action.conversation_id and a.status == "executing"
            ),
            None,
        )
        if executing is not None:
            return executing

        for other in list(self.actions.values()):
            if (
                other.conversation_id == action.conversation_id
                and other.status == "pending"
                and other.idempotency_key != action.idempotency_key
            ):
                self.actions[other.id] = replace(other, status="cancelled")

        if existing is not None:
            reopened = replace(
                existing,
                status="pending",
                summary=action.summary,
                language=action.language,
                expires_at=action.expires_at,
                confirmed_via=None,
            )
            self.actions[existing.id] = reopened
            return reopened

        self.actions[action.id] = action
        return action

    async def transition(
        self,
        action_id: str,
        from_status: PendingStatus,
        to_status: PendingStatus,
        *,
        confirmed_via: Literal["voice", "button"] | None = None,
        now: datetime | None = None,
    ) -> bool:
        action = self.actions.get(action_id)
        if action is None or action.status != from_status:
            return False
        self.actions[action_id] = replace(
            action, status=to_status, confirmed_via=confirmed_via or action.confirmed_via
        )
        return True

    async def record_invocation(self, invocation: ToolInvocation) -> None:
        self.invocations.append(invocation)

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
        self.messages.append(
            {
                "conversation_id": conversation_id,
                "turn_id": turn_id,
                "role": role,
                "content": content,
                "language": language,
                "input_mode": input_mode,
                "stt_confidence": stt_confidence,
                "interrupted": interrupted,
                "latency_ms": latency_ms,
            }
        )

    async def get_preferred_language(self, user_ref: str) -> str | None:
        return self.preferred_languages.get(user_ref)

    async def set_preferred_language(self, user_ref: str, language: str) -> None:
        self.preferred_languages[user_ref] = language
