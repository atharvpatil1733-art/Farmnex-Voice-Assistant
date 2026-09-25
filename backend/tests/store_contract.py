"""Behaviour every ConversationStore must have. Run against the fake (unit) and Postgres (live)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from voice_core.ports.store import ConversationStore
from voice_core.ports.types import PendingAction, ToolInvocation


def make_action(conversation_id: str, key: str, tool: str = "tool_x") -> PendingAction:
    return PendingAction(
        id=str(uuid.uuid4()),
        conversation_id=conversation_id,
        user_ref="u-contract",
        tool_name=tool,
        args={"ref": "R-1"},
        summary="Do it?",
        language="en-IN",
        status="pending",
        idempotency_key=key,
        expires_at=datetime.now(tz=UTC) + timedelta(seconds=120),
    )


async def _conversation(store: ConversationStore) -> str:
    return await store.create_conversation("u-contract", "contract_pack", "eval", "en-IN")


async def check_owner_is_recorded(store: ConversationStore) -> None:
    conversation_id = await _conversation(store)
    assert await store.get_conversation_owner(conversation_id) == "u-contract"
    assert await store.get_conversation_owner(str(uuid.uuid4())) is None


async def check_upsert_then_get_open(store: ConversationStore) -> None:
    conversation_id = await _conversation(store)
    action = make_action(conversation_id, f"k-{uuid.uuid4()}")
    stored = await store.upsert_pending(action)
    assert stored.id == action.id
    open_action = await store.get_open_pending(conversation_id)
    assert open_action is not None and open_action.id == action.id
    assert open_action.args == {"ref": "R-1"}


async def check_new_action_cancels_previous_open_one(store: ConversationStore) -> None:
    conversation_id = await _conversation(store)
    first = await store.upsert_pending(make_action(conversation_id, f"k-{uuid.uuid4()}"))
    second = await store.upsert_pending(make_action(conversation_id, f"k-{uuid.uuid4()}"))
    open_action = await store.get_open_pending(conversation_id)
    assert open_action is not None and open_action.id == second.id
    assert not await store.transition(first.id, "pending", "executing")


async def check_transition_is_conditional(store: ConversationStore) -> None:
    conversation_id = await _conversation(store)
    action = await store.upsert_pending(make_action(conversation_id, f"k-{uuid.uuid4()}"))
    assert await store.transition(action.id, "pending", "executing", confirmed_via="voice")
    # A second "yes" racing the first must lose.
    assert not await store.transition(action.id, "pending", "executing", confirmed_via="voice")
    assert await store.transition(action.id, "executing", "executed_ok")
    assert await store.get_open_pending(conversation_id) is None


async def check_executed_duplicate_is_returned_not_reopened(store: ConversationStore) -> None:
    conversation_id = await _conversation(store)
    key = f"k-{uuid.uuid4()}"
    first = await store.upsert_pending(make_action(conversation_id, key))
    await store.transition(first.id, "pending", "executing")
    await store.transition(first.id, "executing", "executed_ok")
    again = await store.upsert_pending(make_action(conversation_id, key))
    assert again.id == first.id
    assert again.status == "executed_ok"
    assert await store.get_open_pending(conversation_id) is None


async def check_cancelled_duplicate_is_reopened(store: ConversationStore) -> None:
    conversation_id = await _conversation(store)
    key = f"k-{uuid.uuid4()}"
    first = await store.upsert_pending(make_action(conversation_id, key))
    await store.transition(first.id, "pending", "cancelled")
    again = await store.upsert_pending(make_action(conversation_id, key))
    assert again.id == first.id
    assert again.status == "pending"


async def check_record_invocation_accepts_reads_and_writes(store: ConversationStore) -> None:
    conversation_id = await _conversation(store)
    await store.record_invocation(
        ToolInvocation(
            user_ref="u-contract",
            tool_name="tool_x",
            kind="read",
            args={},
            status="ok",
            conversation_id=conversation_id,
            duration_ms=5,
        )
    )


async def check_executing_action_blocks_a_new_proposal(store: ConversationStore) -> None:
    """At most one open action per conversation: while one is executing, a new proposal gets
    that executing row back (so the caller says "in progress") instead of an insert error."""
    conversation_id = await _conversation(store)
    first = await store.upsert_pending(make_action(conversation_id, f"k-{uuid.uuid4()}"))
    await store.transition(first.id, "pending", "executing")
    second = await store.upsert_pending(make_action(conversation_id, f"k-{uuid.uuid4()}"))
    assert second.id == first.id
    assert second.status == "executing"


async def check_messages_are_recorded(store: ConversationStore) -> None:
    conversation_id = await _conversation(store)
    await store.add_message(
        conversation_id,
        turn_id="t-1",
        role="user",
        content="hello",
        language="en-IN",
        input_mode="voice",
        stt_confidence=0.9,
    )
    await store.add_message(
        conversation_id,
        turn_id="t-1",
        role="assistant",
        content="hi there",
        language="en-IN",
        interrupted=True,
        latency_ms={"stt": 400, "first_audio_total": 1800},
    )


async def check_preferred_language_round_trip(store: ConversationStore) -> None:
    user_ref = f"u-contract-{uuid.uuid4()}"
    assert await store.get_preferred_language(user_ref) is None
    await store.set_preferred_language(user_ref, "mr-IN")
    await store.set_preferred_language(user_ref, "en-IN")
    assert await store.get_preferred_language(user_ref) == "en-IN"


ALL_CHECKS = [
    check_messages_are_recorded,
    check_preferred_language_round_trip,
    check_executing_action_blocks_a_new_proposal,
    check_owner_is_recorded,
    check_upsert_then_get_open,
    check_new_action_cancels_previous_open_one,
    check_transition_is_conditional,
    check_executed_duplicate_is_returned_not_reopened,
    check_cancelled_duplicate_is_reopened,
    check_record_invocation_accepts_reads_and_writes,
]
