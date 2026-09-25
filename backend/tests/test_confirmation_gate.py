from __future__ import annotations

from datetime import UTC, datetime, timedelta

from voice_core.adapters.fakes.conversation import FakeConversationStore
from voice_core.agent.confirmation import ConfirmationGate, idempotency_key


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


async def _setup() -> tuple[FakeConversationStore, ConfirmationGate, Clock, str]:
    store = FakeConversationStore()
    clock = Clock()
    gate = ConfirmationGate(store, clock=clock)
    conversation_id = await store.create_conversation("u-1", "p", "eval", "en-IN")
    return store, gate, clock, conversation_id


async def _propose(gate: ConfirmationGate, conversation_id: str, args: dict | None = None):
    return await gate.propose(
        conversation_id=conversation_id,
        user_ref="u-1",
        tool="write_x",
        args=args or {"ref": "R-1"},
        summary="Do it?",
        language="en-IN",
    )


def test_idempotency_key_ignores_arg_order_but_not_values() -> None:
    assert idempotency_key("c", "t", {"a": 1, "b": 2}) == idempotency_key(
        "c", "t", {"b": 2, "a": 1}
    )
    assert idempotency_key("c", "t", {"a": 1}) != idempotency_key("c", "t", {"a": 2})
    assert idempotency_key("c1", "t", {"a": 1}) != idempotency_key("c2", "t", {"a": 1})


async def test_yes_can_start_execution_only_once() -> None:
    _, gate, _, conversation_id = await _setup()
    action = await _propose(gate, conversation_id)
    assert await gate.begin(action, "voice") == "started"
    assert await gate.begin(action, "button") == "not_pending"


async def test_expired_action_is_not_returned_and_cannot_start() -> None:
    _, gate, clock, conversation_id = await _setup()
    action = await _propose(gate, conversation_id)
    clock.now += timedelta(seconds=121)
    assert await gate.current(conversation_id) == (None, True)
    assert await gate.begin(action, "voice") != "started"


async def test_begin_rechecks_expiry_even_without_current() -> None:
    _, gate, clock, conversation_id = await _setup()
    action = await _propose(gate, conversation_id)
    clock.now += timedelta(seconds=121)
    assert await gate.begin(action, "button") == "expired"


async def test_changed_args_create_a_new_action_and_cancel_the_old() -> None:
    store, gate, _, conversation_id = await _setup()
    old = await _propose(gate, conversation_id, {"ref": "R-1"})
    new = await _propose(gate, conversation_id, {"ref": "R-2"})
    assert new.id != old.id
    assert store.actions[old.id].status == "cancelled"
    current, _ = await gate.current(conversation_id)
    assert current is not None and current.id == new.id


async def test_identical_write_after_execution_is_reported_not_repeated() -> None:
    _, gate, _, conversation_id = await _setup()
    action = await _propose(gate, conversation_id)
    await gate.begin(action, "voice")
    await gate.finish(action, ok=True)
    again = await _propose(gate, conversation_id)
    assert again.status == "executed_ok"


async def test_action_stuck_in_executing_is_released_after_its_lease() -> None:
    """If the process dies between begin() and finish(), the row must not lock the
    conversation forever: past the lease it is closed as executed_error."""
    store, gate, clock, conversation_id = await _setup()
    action = await _propose(gate, conversation_id)
    await gate.begin(action, "voice")
    clock.now += timedelta(seconds=30)
    current, _ = await gate.current(conversation_id)
    assert current is not None and current.status == "executing"

    clock.now += timedelta(minutes=5)
    current, _ = await gate.current(conversation_id)
    assert current is None
    assert store.actions[action.id].status == "executed_error"
