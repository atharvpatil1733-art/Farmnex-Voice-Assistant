from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Literal

import asyncpg

from voice_core.ports.types import PendingAction, PendingStatus, ToolInvocation

_TERMINAL: tuple[PendingStatus, ...] = ("executed_ok", "executed_error", "cancelled", "expired")
_REOPENABLE: tuple[PendingStatus, ...] = ("cancelled", "expired", "executed_error")
_PENDING_COLUMNS = (
    "id, conversation_id, user_ref, tool_name, args, summary, language, status, "
    "idempotency_key, expires_at, confirmed_via"
)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def _row_to_action(row: asyncpg.Record) -> PendingAction:
    return PendingAction(
        id=str(row["id"]),
        conversation_id=str(row["conversation_id"]),
        user_ref=row["user_ref"],
        tool_name=row["tool_name"],
        args=dict(row["args"]),
        summary=row["summary"],
        language=row["language"],
        status=row["status"],
        idempotency_key=row["idempotency_key"],
        expires_at=row["expires_at"],
        confirmed_via=row["confirmed_via"],
    )


class SupabaseConversationStore:
    """ConversationStore over the `voice` schema (Supabase or plain Postgres)."""

    def __init__(self, database_url: str, statement_cache_size: int = 0) -> None:
        self._database_url = database_url
        self._statement_cache_size = statement_cache_size
        self._pool: asyncpg.Pool | None = None

    async def _init_connection(self, conn: asyncpg.Connection) -> None:
        await conn.set_type_codec(
            "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                dsn=self._database_url,
                statement_cache_size=self._statement_cache_size,
                init=self._init_connection,
            )
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def create_conversation(
        self,
        user_ref: str,
        pack_id: str,
        channel: Literal["app", "text_api", "eval"],
        language: str,
    ) -> str:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            "insert into voice.conversations (user_ref, pack_id, channel, language_start) "
            "values ($1, $2, $3, $4) returning id",
            user_ref,
            pack_id,
            channel,
            language,
        )
        return str(row["id"])

    async def get_conversation_owner(self, conversation_id: str) -> str | None:
        if not _is_uuid(conversation_id):
            return None
        pool = await self._get_pool()
        owner: str | None = await pool.fetchval(
            "select user_ref from voice.conversations where id = $1", uuid.UUID(conversation_id)
        )
        return owner

    async def get_open_pending(self, conversation_id: str) -> PendingAction | None:
        if not _is_uuid(conversation_id):
            return None
        pool = await self._get_pool()
        row = await pool.fetchrow(
            f"select {_PENDING_COLUMNS} from voice.pending_actions "  # nosec B608 - constant columns
            "where conversation_id = $1 and status in ('pending', 'executing')",
            uuid.UUID(conversation_id),
        )
        return _row_to_action(row) if row else None

    async def upsert_pending(self, action: PendingAction) -> PendingAction:
        try:
            return await self._upsert_pending(action)
        except asyncpg.UniqueViolationError:
            # Lost a race with another open action (pending_actions_one_open_idx): report the
            # row that won, so the caller answers "in progress" instead of failing the turn.
            current = await self.get_open_pending(action.conversation_id)
            if current is None:
                raise
            return current

    async def _upsert_pending(self, action: PendingAction) -> PendingAction:
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            existing = await conn.fetchrow(
                f"select {_PENDING_COLUMNS} from voice.pending_actions "  # nosec B608
                "where idempotency_key = $1 for update",
                action.idempotency_key,
            )
            if existing is not None and existing["status"] not in _REOPENABLE:
                return _row_to_action(existing)

            executing = await conn.fetchrow(
                f"select {_PENDING_COLUMNS} from voice.pending_actions "  # nosec B608
                "where conversation_id = $1 and status = 'executing'",
                uuid.UUID(action.conversation_id),
            )
            if executing is not None:
                return _row_to_action(executing)

            await conn.execute(
                "update voice.pending_actions set status = 'cancelled', resolved_at = now() "
                "where conversation_id = $1 and status = 'pending' and idempotency_key <> $2",
                uuid.UUID(action.conversation_id),
                action.idempotency_key,
            )

            if existing is not None:
                row = await conn.fetchrow(
                    "update voice.pending_actions set status = 'pending', summary = $2, "
                    "language = $3, expires_at = $4, confirmed_via = null, resolved_at = null "
                    f"where id = $1 returning {_PENDING_COLUMNS}",  # nosec B608
                    existing["id"],
                    action.summary,
                    action.language,
                    action.expires_at,
                )
                return _row_to_action(row)

            row = await conn.fetchrow(
                "insert into voice.pending_actions (id, conversation_id, user_ref, tool_name, "
                "args, summary, language, status, idempotency_key, expires_at) "
                "values ($1, $2, $3, $4, $5, $6, $7, 'pending', $8, $9) "
                f"returning {_PENDING_COLUMNS}",  # nosec B608
                uuid.UUID(action.id),
                uuid.UUID(action.conversation_id),
                action.user_ref,
                action.tool_name,
                action.args,
                action.summary,
                action.language,
                action.idempotency_key,
                action.expires_at,
            )
            return _row_to_action(row)

    async def transition(
        self,
        action_id: str,
        from_status: PendingStatus,
        to_status: PendingStatus,
        *,
        confirmed_via: Literal["voice", "button"] | None = None,
        now: datetime | None = None,
    ) -> bool:
        pool = await self._get_pool()
        result: str = await pool.execute(
            "update voice.pending_actions set status = $3, "
            "confirmed_via = coalesce($4, confirmed_via), "
            "resolved_at = case when $5 then now() else resolved_at end "
            "where id = $1 and status = $2",
            uuid.UUID(action_id),
            from_status,
            to_status,
            confirmed_via,
            to_status in _TERMINAL,
        )
        return result == "UPDATE 1"

    async def record_invocation(self, invocation: ToolInvocation) -> None:
        pool = await self._get_pool()
        params: list[Any] = [
            uuid.UUID(invocation.conversation_id) if invocation.conversation_id else None,
            uuid.UUID(invocation.pending_action_id) if invocation.pending_action_id else None,
            invocation.user_ref,
            invocation.turn_id,
            invocation.tool_name,
            invocation.kind,
            invocation.args,
            invocation.status,
            invocation.error_code,
            invocation.duration_ms,
        ]
        await pool.execute(
            "insert into voice.tool_invocations (conversation_id, pending_action_id, user_ref, "
            "turn_id, tool_name, kind, args, status, error_code, duration_ms) "
            "values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
            *params,
        )
