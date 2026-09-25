from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from voice_core.adapters.fakes.conversation import FakeConversationStore
from voice_core.adapters.fakes.llm import FakeLLM
from voice_core.agent.loop import TurnResult, resolve_pending_action, run_text_turn
from voice_core.packs.loader import load_pack
from voice_core.ports.types import (
    Done,
    LLMEvent,
    TextDelta,
    ToolCall,
    ToolContext,
    ToolDef,
    ToolResult,
    Usage,
)
from voice_core.tools.handlers.mock import MockToolHandler
from voice_core.tools.registry import ToolRegistry

PACKS_ROOT = Path(__file__).resolve().parents[2] / "domain_packs"
ACCEPT_B9 = '{"listing_ref":"L-102","bid_ref":"B-9"}'


class SequencedFakeLLM:
    """Replays a different scripted event list on each successive call to stream()."""

    name = "sequenced-fake"

    def __init__(self, scripts: list[list[LLMEvent]]) -> None:
        self._scripts = scripts
        self.call_count = 0

    async def stream(
        self, messages, tools, *, temperature, max_tokens, timeout_s
    ) -> AsyncIterator[LLMEvent]:
        index = min(self.call_count, len(self._scripts) - 1)
        self.call_count += 1
        for event in self._scripts[index]:
            yield event


class SpyMockToolHandler(MockToolHandler):
    def __init__(self, pack_dir: Path) -> None:
        super().__init__(pack_dir)
        self.calls: list[str] = []
        self.contexts: list[ToolContext] = []

    async def call(self, tool, args, ctx):
        self.calls.append(tool.name)
        self.contexts.append(ctx)
        return await super().call(tool, args, ctx)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def _write_call(name: str = "accept_bid", args_json: str = ACCEPT_B9) -> list[LLMEvent]:
    return [ToolCall(id="w", name=name, args_json=args_json), Done(usage=Usage(0, 0))]


def _text(text: str) -> list[LLMEvent]:
    return [TextDelta(text=text), Done(usage=Usage(0, 0))]


@pytest.fixture
def pack():
    return load_pack("farm_marketplace", PACKS_ROOT)


@pytest.fixture
def registry(pack) -> ToolRegistry:
    return ToolRegistry(pack)


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(user_ref="u-1", language="hi-IN")


@pytest.fixture
def handler(pack) -> SpyMockToolHandler:
    return SpyMockToolHandler(pack.pack_dir)


@pytest.fixture
async def conv() -> tuple[FakeConversationStore, str]:
    store = FakeConversationStore()
    conversation_id = await store.create_conversation("u-1", "farm_marketplace", "eval", "hi-IN")
    return store, conversation_id


@pytest.fixture
def run(pack, registry, ctx, handler, conv):
    store, conversation_id = conv

    async def _run(llm: Any, user_text: str, language: str = "hi-IN", **kwargs: Any) -> TurnResult:
        return await run_text_turn(
            pack=pack,
            registry=registry,
            handler=kwargs.pop("handler", handler),
            llm=llm,
            store=store,
            conversation_id=conversation_id,
            ctx=ctx,
            language=language,
            history=[],
            user_text=user_text,
            **kwargs,
        )

    return _run


async def test_read_tool_round_trip(run, handler, conv) -> None:
    store, _ = conv
    llm = SequencedFakeLLM(
        [
            [
                ToolCall(id="1", name="get_bids_for_listing", args_json='{"listing_ref":"latest"}'),
                Done(usage=Usage(0, 0)),
            ],
            _text("आपकी बोली मिल गई"),
        ]
    )

    result = await run(llm, "bids?")

    assert result.tools_called == ["get_bids_for_listing"]
    assert result.pending_action is None
    assert "get_bids_for_listing" in handler.calls
    assert [i.tool_name for i in store.invocations] == ["get_bids_for_listing"]
    assert store.invocations[0].kind == "read"


async def test_write_tool_becomes_a_stored_pending_action(run, handler, conv) -> None:
    store, conversation_id = conv
    result = await run(SequencedFakeLLM([_write_call()]), "accept the highest bid")

    assert result.pending_action == "accept_bid"
    assert result.pending_status == "pending"
    assert result.executed is False
    assert "accept_bid" not in handler.calls  # only the confirm resolver may run
    assert result.reply_text  # the rendered confirm template
    stored = await store.get_open_pending(conversation_id)
    assert stored is not None and stored.id == result.pending_action_id
    assert stored.args == {"listing_ref": "L-102", "bid_ref": "B-9"}
    assert stored.summary == result.reply_text


async def test_latest_is_pinned_to_the_concrete_listing_in_stored_args(run, conv) -> None:
    store, conversation_id = conv
    await run(
        SequencedFakeLLM([_write_call(args_json='{"listing_ref":"latest","bid_ref":"B-9"}')]),
        "accept the highest bid",
    )
    stored = await store.get_open_pending(conversation_id)
    assert stored is not None
    assert stored.args["listing_ref"] == "L-102"


async def test_yes_executes_stored_args_once_without_calling_llm(run, handler, conv) -> None:
    store, _ = conv
    proposed = await run(SequencedFakeLLM([_write_call()]), "accept the highest bid")
    llm = FakeLLM()  # must not be called on a clear "yes"

    result = await run(llm, "हाँ")

    assert result.executed is True
    assert result.executed_tool == "accept_bid"
    assert result.confirmed_via == "voice"
    assert result.pending_status == "executed_ok"
    assert handler.calls.count("accept_bid") == 1
    assert (
        handler.contexts[-1].idempotency_key
        == store.actions[proposed.pending_action_id].idempotency_key
    )
    assert llm.calls == []
    write_rows = [i for i in store.invocations if i.kind == "write"]
    assert len(write_rows) == 1 and write_rows[0].pending_action_id == proposed.pending_action_id

    again = await run(FakeLLM([TextDelta(text="?"), Done(usage=Usage(0, 0))]), "हाँ")
    assert again.executed is False
    assert handler.calls.count("accept_bid") == 1


async def test_no_cancels_without_calling_llm_or_executing(run, handler, conv) -> None:
    store, conversation_id = conv
    await run(SequencedFakeLLM([_write_call()]), "accept the highest bid")
    llm = FakeLLM()

    result = await run(llm, "नहीं")

    assert result.executed is False
    assert result.pending_status == "cancelled"
    assert "accept_bid" not in handler.calls
    assert llm.calls == []
    assert await store.get_open_pending(conversation_id) is None


async def test_yes_after_expiry_never_executes(pack, registry, ctx, handler, conv) -> None:
    store, conversation_id = conv
    clock = Clock()
    common: dict[str, Any] = dict(
        pack=pack,
        registry=registry,
        handler=handler,
        store=store,
        conversation_id=conversation_id,
        ctx=ctx,
        language="hi-IN",
        history=[],
        clock=clock,
    )
    await run_text_turn(llm=SequencedFakeLLM([_write_call()]), user_text="accept", **common)
    clock.now += timedelta(seconds=121)

    result = await run_text_turn(llm=FakeLLM(), user_text="हाँ", **common)

    assert result.executed is False
    assert result.pending_status == "expired"
    assert "accept_bid" not in handler.calls


async def test_unrelated_turn_cancels_the_open_pending_action(run, handler, conv) -> None:
    """A later 'yes' to some other question must never execute an old proposal."""
    store, conversation_id = conv
    await run(SequencedFakeLLM([_write_call()]), "accept the highest bid")

    result = await run(
        SequencedFakeLLM([_text("Pre-bidding stays open 7 days.")]), "what is pre-bidding?"
    )

    assert result.pending_status == "cancelled"
    assert await store.get_open_pending(conversation_id) is None
    after = await run(FakeLLM([TextDelta(text="ok"), Done(usage=Usage(0, 0))]), "हाँ")
    assert after.executed is False
    assert "accept_bid" not in handler.calls


async def test_changed_args_create_a_new_pending_action(run, conv) -> None:
    store, conversation_id = conv
    first = await run(SequencedFakeLLM([_write_call()]), "accept the highest bid")

    second = await run(
        SequencedFakeLLM([_write_call(args_json='{"listing_ref":"L-102","bid_ref":"B-7"}')]),
        "No wait, the second one",
    )

    assert second.pending_action_id != first.pending_action_id
    assert store.actions[first.pending_action_id].status == "cancelled"
    current = await store.get_open_pending(conversation_id)
    assert current is not None and current.args["bid_ref"] == "B-7"


async def test_invalid_write_args_go_back_to_the_llm_not_to_the_user(run, conv) -> None:
    store, conversation_id = conv
    llm = SequencedFakeLLM(
        [
            _write_call(args_json='{"listing_ref":"L-102"}'),  # missing bid_ref
            _write_call(),
        ]
    )

    result = await run(llm, "accept the highest bid")

    assert llm.call_count == 2
    assert result.pending_action == "accept_bid"
    assert (await store.get_open_pending(conversation_id)) is not None


async def test_unresolvable_confirm_fields_do_not_crash_or_create_pending_action(
    run, handler, conv
) -> None:
    """A write whose args match no record can't fill its confirm template. The turn must
    neither raise nor propose a write the user can't verify (golden rule 4)."""
    store, conversation_id = conv
    llm = SequencedFakeLLM(
        [_write_call("request_crop_rescue", '{"listing_ref":"L-does-not-exist","days_left":2}')]
    )

    result = await run(llm, "my crop is spoiling")

    assert result.pending_action is None
    assert result.pending_write_args is None
    assert result.executed is False
    assert "request_crop_rescue" not in handler.calls
    assert result.reply_text
    assert await store.get_open_pending(conversation_id) is None


async def test_button_confirmation_executes_via_button(run, registry, ctx, handler, conv) -> None:
    store, conversation_id = conv
    await run(SequencedFakeLLM([_write_call()]), "accept the highest bid")
    action = await store.get_open_pending(conversation_id)
    assert action is not None

    result = await resolve_pending_action(
        registry=registry,
        handler=handler,
        store=store,
        ctx=ctx,
        action=action,
        decision="yes",
        via="button",
        language="hi-IN",
    )

    assert result.executed is True
    assert result.confirmed_via == "button"
    assert store.actions[action.id].confirmed_via == "button"


async def test_failed_write_is_reported_as_failure_not_success(run, pack, conv) -> None:
    store, _ = conv

    class FailingWrites(SpyMockToolHandler):
        async def call(self, tool: ToolDef, args, ctx) -> ToolResult:
            if tool.kind == "write":
                raise ConnectionError("host down")
            return await super().call(tool, args, ctx)

    failing = FailingWrites(pack.pack_dir)
    proposed = await run(SequencedFakeLLM([_write_call()]), "accept", handler=failing)

    result = await run(FakeLLM(), "हाँ", handler=failing)

    assert result.executed is False
    assert result.pending_status == "executed_error"
    assert store.actions[proposed.pending_action_id].status == "executed_error"
    assert store.invocations[-1].status == "error"


async def test_max_tool_rounds_cutoff_returns_fallback(run) -> None:
    llm = FakeLLM(
        [
            ToolCall(id="1", name="get_demand_forecast", args_json='{"crop":"onion"}'),
            Done(usage=Usage(0, 0)),
        ]
    )

    result = await run(llm, "sell now or wait?", language="en-IN")

    assert result.pending_action is None
    assert result.reply_text  # fallback text, not a crash/hang
    assert len(llm.calls) == 4  # MAX_TOOL_ROUNDS


async def test_auto_retrieve_injects_knowledge_and_records_it_used(run) -> None:
    from voice_core.adapters.fakes.embeddings import FakeEmbedding
    from voice_core.adapters.fakes.knowledge import FakeKnowledgeStore
    from voice_core.ports.types import Chunk

    knowledge = FakeKnowledgeStore(
        [Chunk(doc_slug="pre-bidding-basics", doc_version=1, heading="h", text="t", similarity=0.9)]
    )
    llm = FakeLLM([TextDelta(text="Pre-bidding stays open 7 days."), Done(usage=Usage(0, 0))])

    result = await run(
        llm,
        "how long is pre-bidding open?",
        language="en-IN",
        embeddings=FakeEmbedding(dim=4),
        knowledge_store=knowledge,
        auto_rag_min_sim=0.45,
    )

    assert result.knowledge_used == ["pre-bidding-basics"]


async def test_auto_retrieve_failure_does_not_crash_the_turn(run) -> None:
    """A flaky knowledge-store connection (e.g. a transient DB error) must degrade to
    'no extra knowledge this turn', not blow up the whole agent turn."""
    from voice_core.adapters.fakes.embeddings import FakeEmbedding

    class _BrokenKnowledgeStore:
        async def match(self, *args, **kwargs):
            raise ConnectionError("simulated transient DB failure")

    llm = FakeLLM([TextDelta(text="here you go"), Done(usage=Usage(0, 0))])

    result = await run(
        llm,
        "how long is pre-bidding open?",
        language="en-IN",
        embeddings=FakeEmbedding(dim=4),
        knowledge_store=_BrokenKnowledgeStore(),
        auto_rag_min_sim=0.45,
    )

    assert result.knowledge_used == []
    assert result.reply_text == "here you go"


async def test_no_knowledge_used_without_wiring(run) -> None:
    llm = FakeLLM([TextDelta(text="ok"), Done(usage=Usage(0, 0))])
    result = await run(llm, "hello", language="en-IN")
    assert result.knowledge_used == []


async def test_set_preferred_language_switches_reply_language(run) -> None:
    llm = SequencedFakeLLM(
        [
            [
                ToolCall(id="1", name="set_preferred_language", args_json='{"language":"mr-IN"}'),
                Done(usage=Usage(0, 0)),
            ],
            _text("ठीक आहे"),
        ]
    )

    result = await run(llm, "मराठीत बोला")

    assert result.reply_language == "mr-IN"


async def test_write_without_a_template_for_the_language_is_never_proposed(run, conv) -> None:
    """Defence in depth for the same blocker: an empty confirmation must not become an action."""
    store, conversation_id = conv
    result = await run(
        SequencedFakeLLM([_write_call()]), "accept the highest bid", language="ta-IN"
    )
    assert result.pending_action is None
    assert await store.get_open_pending(conversation_id) is None


async def test_cancelling_the_turn_does_not_abort_an_executing_write(
    pack, run, registry, ctx, conv
) -> None:
    """SPEC §6: an interrupt never cancels a write that is already executing."""
    import asyncio

    store, conversation_id = conv
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowWrites(SpyMockToolHandler):
        async def call(self, tool, args, ctx):
            if tool.kind == "write":
                started.set()
                await release.wait()
            return await super().call(tool, args, ctx)

    slow = SlowWrites(pack.pack_dir)
    await run(SequencedFakeLLM([_write_call()]), "accept", handler=slow)
    action = await store.get_open_pending(conversation_id)
    assert action is not None

    turn = asyncio.create_task(
        resolve_pending_action(
            registry=registry,
            handler=slow,
            store=store,
            ctx=ctx,
            action=action,
            decision="yes",
            via="voice",
            language="hi-IN",
        )
    )
    await started.wait()
    turn.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await turn
    for _ in range(20):
        if store.actions[action.id].status == "executed_ok":
            break
        await asyncio.sleep(0)
    assert store.actions[action.id].status == "executed_ok"
    assert any(i.kind == "write" for i in store.invocations)
