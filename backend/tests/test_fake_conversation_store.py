from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from tests.store_contract import ALL_CHECKS
from voice_core.adapters.fakes.conversation import FakeConversationStore
from voice_core.ports.store import ConversationStore


def test_fake_satisfies_protocol() -> None:
    assert isinstance(FakeConversationStore(), ConversationStore)


@pytest.mark.parametrize("check", ALL_CHECKS, ids=lambda c: c.__name__)
async def test_fake_store_contract(
    check: Callable[[ConversationStore], Awaitable[None]],
) -> None:
    await check(FakeConversationStore())
