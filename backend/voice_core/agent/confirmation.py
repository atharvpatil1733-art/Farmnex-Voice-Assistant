"""The write-confirmation gate (SPEC §7, golden rule 4).

A write tool call never executes directly: it becomes a stored PendingAction, the user hears
the pack's templated summary, and only an explicit yes (voice lexicon or button) executes the
*stored* args — exactly once, guarded by a conditional status transition in the store.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from voice_core.ports.store import ConversationStore
from voice_core.ports.types import PendingAction

PENDING_TTL = timedelta(seconds=120)
# An action still "executing" this long after its confirm window closed is treated as a crashed
# execution and closed as executed_error, so it can't lock the conversation forever.
EXECUTING_LEASE = timedelta(seconds=60)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def idempotency_key(conversation_id: str, tool: str, args: dict[str, Any]) -> str:
    """sha256(conversation_id|tool|canonical_args) — the same write proposed twice in one
    conversation maps to the same key, so it can never execute twice."""
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(f"{conversation_id}|{tool}|{canonical}".encode()).hexdigest()


class ConfirmationGate:
    def __init__(
        self,
        store: ConversationStore,
        *,
        ttl: timedelta = PENDING_TTL,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._store = store
        self._ttl = ttl
        self._clock = clock

    async def current(self, conversation_id: str) -> tuple[PendingAction | None, bool]:
        """The open action, or (None, True) if the one that was open has just expired."""
        action = await self._store.get_open_pending(conversation_id)
        if action is None:
            return None, False
        now = self._clock()
        if action.status == "pending" and action.expires_at <= now:
            await self._store.transition(action.id, "pending", "expired")
            return None, True
        if action.status == "executing" and action.expires_at + EXECUTING_LEASE <= now:
            await self._store.transition(action.id, "executing", "executed_error")
            return None, False
        return action, False

    async def propose(
        self,
        *,
        conversation_id: str,
        user_ref: str,
        tool: str,
        args: dict[str, Any],
        summary: str,
        language: str,
    ) -> PendingAction:
        """Store a new pending action (cancelling any other open one). If the identical write was
        already executed in this conversation, the returned action has status executed_ok."""
        return await self._store.upsert_pending(
            PendingAction(
                id=str(uuid.uuid4()),
                conversation_id=conversation_id,
                user_ref=user_ref,
                tool_name=tool,
                args=args,
                summary=summary,
                language=language,
                status="pending",
                idempotency_key=idempotency_key(conversation_id, tool, args),
                expires_at=self._clock() + self._ttl,
            )
        )

    async def cancel(self, action: PendingAction) -> bool:
        return await self._store.transition(action.id, "pending", "cancelled")

    async def begin(
        self, action: PendingAction, via: Literal["voice", "button"]
    ) -> Literal["started", "expired", "not_pending"]:
        """Claim the action for execution. Only one caller can ever get "started"."""
        if action.expires_at <= self._clock():
            await self._store.transition(action.id, "pending", "expired")
            return "expired"
        claimed = await self._store.transition(action.id, "pending", "executing", confirmed_via=via)
        return "started" if claimed else "not_pending"

    async def finish(self, action: PendingAction, *, ok: bool) -> None:
        await self._store.transition(
            action.id, "executing", "executed_ok" if ok else "executed_error"
        )
