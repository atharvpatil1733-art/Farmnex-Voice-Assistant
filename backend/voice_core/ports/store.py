from __future__ import annotations

from typing import Protocol

# Deliberately left as a stub. Backs conversations, messages, tool_invocations,
# pending_actions, user_prefs, eval_runs (see supabase/migrations/0001_voice_core.sql).
# Method contract is designed in M1 (session/history) and M3 (confirmation gate),
# not fixed at M0 scaffold time.


class ConversationStore(Protocol): ...
