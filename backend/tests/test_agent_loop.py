from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from voice_core.adapters.fakes.conversation import FakeConversationStore
from voice_core.adapters.fakes.llm import FakeLLM
from voice_core.agent.loop import TurnResult, resolve_pending_action, run_text_turn
from voice_core.packs.loader import load_pack
from voice_core.ports.types import (
    Done,
    LLMError,
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


async def test_auto_retrieval_can_be_switched_off(run) -> None:
    """Knowledge search only when the AI asks for it: no per-turn lookup (saves ~1.2 s)."""
    from voice_core.adapters.fakes.embeddings import FakeEmbedding
    from voice_core.adapters.fakes.knowledge import FakeKnowledgeStore
    from voice_core.ports.types import Chunk

    class Spy(FakeKnowledgeStore):
        matches = 0

        async def match(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            Spy.matches += 1
            return await super().match(*args, **kwargs)

    knowledge = Spy(
        [Chunk(doc_slug="pre-bidding-basics", doc_version=1, heading="h", text="t", similarity=0.9)]
    )
    llm = FakeLLM([TextDelta(text="ok"), Done(usage=Usage(0, 0))])

    result = await run(
        llm,
        "when will I get paid?",
        language="en-IN",
        embeddings=FakeEmbedding(dim=4),
        knowledge_store=knowledge,
        auto_rag_min_sim=None,
    )

    assert Spy.matches == 0
    assert result.knowledge_used == []


async def test_search_knowledge_tool_records_knowledge_used(pack, handler, conv) -> None:
    from voice_core.adapters.fakes.embeddings import FakeEmbedding
    from voice_core.adapters.fakes.knowledge import FakeKnowledgeStore
    from voice_core.ports.types import Chunk

    store, conversation_id = conv
    knowledge = FakeKnowledgeStore(
        [Chunk(doc_slug="crop-rescue-basics", doc_version=2, heading="h", text="t", similarity=0.9)]
    )
    registry = ToolRegistry(pack, embeddings=FakeEmbedding(dim=4), knowledge_store=knowledge)
    llm = SequencedFakeLLM(
        [
            [
                ToolCall(id="k", name="search_knowledge", args_json='{"query":"crop rescue"}'),
                Done(usage=Usage(0, 0)),
            ],
            _text("It sells produce fast."),
        ]
    )

    result = await run_text_turn(
        pack=pack,
        registry=registry,
        handler=handler,
        llm=llm,
        store=store,
        conversation_id=conversation_id,
        ctx=ToolContext(user_ref="u-1", language="en-IN"),
        language="en-IN",
        history=[],
        user_text="what is crop rescue?",
        auto_rag_min_sim=None,
    )

    assert result.knowledge_used == ["crop-rescue-basics"]


class Spoken:
    """Collects early fixed-phrase answers the loop hands to the voice layer."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def __call__(self, text: str) -> None:
        self.texts.append(text)


def _bids_then(second_round: list[LLMEvent]) -> SequencedFakeLLM:
    return SequencedFakeLLM(
        [
            [
                ToolCall(id="1", name="get_bids_for_listing", args_json='{"listing_ref":"latest"}'),
                Done(usage=Usage(0, 0)),
            ],
            second_round,
        ]
    )


BIDS_ANSWER_HI = "आपके 500 किलो प्याज़ पर 3 बोलियाँ आई हैं। सबसे ऊँची बोली 27 रुपये किलो है।"


async def test_template_is_spoken_early_and_llm_adds_only_what_is_missing(run) -> None:
    spoken = Spoken()
    llm = _bids_then(_text("Bidding closes tomorrow evening."))

    result = await run(llm, "मेरी प्याज़ पर कितनी बोली आई है", on_interim_answer=spoken)

    assert spoken.texts == [BIDS_ANSWER_HI]
    assert llm.call_count == 2  # the LLM still gets its round (the user might want more)
    assert result.early_answer == BIDS_ANSWER_HI
    assert result.reply_text == "Bidding closes tomorrow evening."  # spoken as the follow-up


async def test_empty_follow_up_means_the_early_answer_said_it_all(run) -> None:
    spoken = Spoken()
    result = await run(_bids_then(_text("  ")), "bids?", on_interim_answer=spoken)
    assert result.early_answer == BIDS_ANSWER_HI
    assert result.reply_text == ""


async def test_llm_failure_after_early_answer_still_says_the_rest_failed(run) -> None:
    """Re-review S5: if the user also asked for an action, silence would suggest it's
    happening. The localized failure phrase is spoken after the early answer."""
    spoken = Spoken()
    llm = _bids_then([LLMError(code="rate_limited", message="429", retryable=True)])
    result = await run(llm, "bids?", on_interim_answer=spoken)
    assert result.early_answer == BIDS_ANSWER_HI
    assert result.reply_text == "माफ़ कीजिए, अभी जवाब नहीं दे पा रही। कृपया फिर से कोशिश करें।"


async def test_llm_is_told_what_the_user_already_heard(run) -> None:
    seen: list[list[Any]] = []

    class Recording(SequencedFakeLLM):
        async def stream(self, messages, tools, **kwargs):  # type: ignore[no-untyped-def]
            seen.append(list(messages))
            async for event in super().stream(messages, tools, **kwargs):
                yield event

    llm = Recording(_bids_then(_text(""))._scripts)
    await run(llm, "bids?", on_interim_answer=Spoken())
    second_round = seen[1]
    assert any(m.role == "assistant" and m.content == BIDS_ANSWER_HI for m in second_round)
    note = second_round[-1]
    assert note.role == "user" and "already spoken" in note.content  # no trailing model turn
    assert BIDS_ANSWER_HI not in note.content  # host-derived text never gets a note of its own


async def test_action_request_still_reaches_the_write_after_an_early_answer(run, conv) -> None:
    """Regression: "accept the highest bid" = bids lookup THEN accept_bid. The early answer
    must not end the turn before the write is proposed."""
    store, conversation_id = conv
    spoken = Spoken()
    llm = _bids_then(_write_call())

    result = await run(llm, "सबसे ऊँची बोली स्वीकार कर दो", on_interim_answer=spoken)

    assert spoken.texts == [BIDS_ANSWER_HI]
    assert result.pending_action == "accept_bid"
    assert result.early_answer == BIDS_ANSWER_HI
    assert (await store.get_open_pending(conversation_id)) is not None


async def test_without_the_voice_hook_no_template_is_used(run) -> None:
    """Text API / evals keep the plain LLM flow."""
    llm = _bids_then(_text("LLM-WORDED ANSWER"))
    result = await run(llm, "bids?")
    assert result.reply_text == "LLM-WORDED ANSWER"
    assert result.early_answer is None


async def test_template_dates_are_spoken_in_the_reply_language(run) -> None:
    from datetime import timedelta

    spoken = Spoken()
    llm = SequencedFakeLLM(
        [
            [ToolCall(id="1", name="get_order_status", args_json="{}"), Done(usage=Usage(0, 0))],
            _text("unused"),
        ]
    )
    today = date.today()
    await run(
        llm,
        "When will I get paid for my soybean",
        language="en-IN",
        today=today,
        on_interim_answer=spoken,
    )
    expected = today + timedelta(days=3)  # fixture: "{{date:+3}}"
    month = expected.strftime("%B")
    spoken_date = f"{expected.day} {month}" + (
        "" if expected.year == today.year else f" {expected.year}"
    )
    assert spoken.texts == [
        f"Payment for your soybean is being processed. The expected date is {spoken_date}."
    ]


async def test_past_dates_are_never_spoken_as_future_facts(
    pack, registry, ctx, conv, tmp_path
) -> None:
    """Review repro: a pickup dated in the past must not be announced as "will be picked up";
    closed bidding must not be presented as live. Both go to the LLM instead."""
    import json
    import shutil

    store, conversation_id = conv
    pack_copy = tmp_path / "pack"
    shutil.copytree(pack.pack_dir, pack_copy)
    for name, key, value in [
        ("pickups.json", "pickup_date", "2020-01-01"),
        ("bids.json", "bidding_ends_at", "2020-01-01T18:00:00+05:30"),
    ]:
        path = pack_copy / "fixtures" / name
        data = json.loads(path.read_text(encoding="utf-8"))
        data["data"][key] = value
        path.write_text(json.dumps(data), encoding="utf-8")

    for tool, args in [
        ("get_pickup_status", "{}"),
        ("get_bids_for_listing", '{"listing_ref":"latest"}'),
    ]:
        spoken = Spoken()
        llm = SequencedFakeLLM(
            [[ToolCall(id="1", name=tool, args_json=args), Done(usage=Usage(0, 0))], _text("LLM")]
        )
        result = await run_text_turn(
            pack=pack,
            registry=registry,
            handler=MockToolHandler(pack_copy),
            llm=llm,
            store=store,
            conversation_id=conversation_id,
            ctx=ctx,
            language="hi-IN",
            history=[],
            user_text="?",
            on_interim_answer=spoken,
        )
        assert spoken.texts == [], tool
        assert result.reply_text == "LLM"


async def test_unresolvable_template_speaks_nothing_early(pack, registry, ctx, conv) -> None:
    store, conversation_id = conv
    spoken = Spoken()
    handler = MockToolHandler(
        pack.pack_dir, fixture_overrides={"get_bids_for_listing": "fixtures/tool_error.json"}
    )
    result = await run_text_turn(
        pack=pack,
        registry=registry,
        handler=handler,
        llm=_bids_then(_text("LLM-WORDED ANSWER")),
        store=store,
        conversation_id=conversation_id,
        ctx=ctx,
        language="hi-IN",
        history=[],
        user_text="bids?",
        on_interim_answer=spoken,
    )
    assert spoken.texts == []
    assert result.reply_text == "LLM-WORDED ANSWER"


async def test_answer_when_guard_sends_unusual_states_to_the_llm(
    pack, registry, ctx, conv, tmp_path
) -> None:
    import json
    import shutil

    store, conversation_id = conv
    pack_copy = tmp_path / "pack"
    shutil.copytree(pack.pack_dir, pack_copy)
    pickup = json.loads((pack_copy / "fixtures/pickups.json").read_text(encoding="utf-8"))
    pickup["data"]["status"] = "delayed"
    (pack_copy / "fixtures/pickups.json").write_text(json.dumps(pickup), encoding="utf-8")
    spoken = Spoken()
    llm = SequencedFakeLLM(
        [
            [ToolCall(id="1", name="get_pickup_status", args_json="{}"), Done(usage=Usage(0, 0))],
            _text("LLM explains the delay"),
        ]
    )
    result = await run_text_turn(
        pack=pack,
        registry=registry,
        handler=MockToolHandler(pack_copy),
        llm=llm,
        store=store,
        conversation_id=conversation_id,
        ctx=ctx,
        language="hi-IN",
        history=[],
        user_text="माल कब उठेगा",
        on_interim_answer=spoken,
    )
    assert spoken.texts == []
    assert result.reply_text == "LLM explains the delay"


async def test_two_tools_in_one_round_are_left_to_the_llm(run) -> None:
    spoken = Spoken()
    llm = SequencedFakeLLM(
        [
            [
                ToolCall(id="1", name="get_order_status", args_json="{}"),
                ToolCall(id="2", name="get_pickup_status", args_json="{}"),
                Done(usage=Usage(0, 0)),
            ],
            _text("combined answer"),
        ]
    )
    result = await run(llm, "order and pickup?", language="en-IN", on_interim_answer=spoken)
    assert spoken.texts == []
    assert result.reply_text == "combined answer"


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("hi-IN", "आपका माल कल, सुबह नौ बजे से सुबह ग्यारह बजे के बीच उठेगा। ड्राइवर Sunil आएँगे।"),
        (
            "mr-IN",
            "तुमचा माल उद्या, सकाळी नऊ वाजता ते सकाळी अकरा वाजता या वेळेत नेला जाईल. ड्रायव्हर Sunil येतील.",
        ),
        (
            "en-IN",
            "Pickup date: tomorrow, between 9 in the morning and 11 in the morning. "
            "Your driver is Sunil.",
        ),
    ],
)
async def test_pickup_early_answer_reads_naturally_with_relative_dates(
    run, language: str, expected: str
) -> None:
    """Fixture pickup is "{{date:+1}}": the spoken date is "कल"/"उद्या"/"tomorrow", so the
    template must not glue a preposition to it ("कल को", "on tomorrow")."""
    spoken = Spoken()
    llm = SequencedFakeLLM(
        [
            [ToolCall(id="1", name="get_pickup_status", args_json="{}"), Done(usage=Usage(0, 0))],
            _text(""),
        ]
    )
    await run(llm, "?", language=language, on_interim_answer=spoken)
    assert spoken.texts == [expected]


async def test_read_audit_runs_off_the_critical_path_but_always_completes(
    pack, registry, ctx, handler
) -> None:
    """Remote DB round trips (~0.5 s) must not delay the answer; the audit row still lands
    before the turn ends (golden rule: every tool call is audited)."""
    import asyncio
    import time as time_module

    events: list[tuple[str, float]] = []

    class SlowStore(FakeConversationStore):
        async def record_invocation(self, invocation):  # type: ignore[no-untyped-def]
            await asyncio.sleep(0.3)
            events.append(("audit_done", time_module.perf_counter()))
            await super().record_invocation(invocation)

    store = SlowStore()
    conversation_id = await store.create_conversation("u-1", "farm_marketplace", "eval", "hi-IN")

    async def spoken(text: str) -> None:
        events.append(("spoken", time_module.perf_counter()))

    await run_text_turn(
        pack=pack,
        registry=registry,
        handler=handler,
        llm=_bids_then(_text("")),
        store=store,
        conversation_id=conversation_id,
        ctx=ctx,
        language="hi-IN",
        history=[],
        user_text="bids?",
        on_interim_answer=spoken,
    )

    order = [name for name, _ in sorted(events, key=lambda e: e[1])]
    assert order == ["spoken", "audit_done"]
    assert [i.tool_name for i in store.invocations] == ["get_bids_for_listing"]
