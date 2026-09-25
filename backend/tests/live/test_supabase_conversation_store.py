from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from tests.store_contract import ALL_CHECKS
from voice_core.config import get_settings
from voice_core.ports.store import ConversationStore

pytestmark = pytest.mark.live

_settings = get_settings()


@pytest.mark.skipif(not _settings.database_url, reason="DATABASE_URL not set (backend/.env)")
@pytest.mark.parametrize("check", ALL_CHECKS, ids=lambda c: c.__name__)
async def test_supabase_store_contract(
    check: Callable[[ConversationStore], Awaitable[None]],
) -> None:
    """Runs the same contract as the fake against the real `voice` schema. Rows are written to
    conversations with channel='eval' / pack_id='contract_pack' and removed afterwards."""
    from voice_core.adapters.supabase.conversation import SupabaseConversationStore

    store = SupabaseConversationStore(
        _settings.database_url, statement_cache_size=_settings.db_statement_cache_size
    )
    try:
        await check(store)
    finally:
        pool = await store._get_pool()
        await pool.execute(
            "delete from voice.tool_invocations where user_ref = 'u-contract'; "
            "delete from voice.conversations where pack_id = 'contract_pack'"
        )
        await store.close()
