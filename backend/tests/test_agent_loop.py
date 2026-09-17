from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from voice_core.adapters.fakes.llm import FakeLLM
from voice_core.agent.loop import PendingWrite, run_text_turn
from voice_core.packs.loader import load_pack
from voice_core.ports.types import (
    Done,
    LLMEvent,
    TextDelta,
    ToolCall,
    ToolContext,
    Usage,
)
from voice_core.tools.handlers.mock import MockToolHandler
from voice_core.tools.registry import ToolRegistry

PACKS_ROOT = Path(__file__).resolve().parents[2] / "domain_packs"


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

    async def call(self, tool, args, ctx):
        self.calls.append(tool.name)
        return await super().call(tool, args, ctx)


@pytest.fixture
def pack():
    return load_pack("farm_marketplace", PACKS_ROOT)


@pytest.fixture
def registry(pack) -> ToolRegistry:
    return ToolRegistry(pack)


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(user_ref="u-1", language="hi-IN")


async def test_read_tool_round_trip(pack, registry, ctx) -> None:
    handler = SpyMockToolHandler(pack.pack_dir)
    llm = SequencedFakeLLM(
        [
            [
                ToolCall(id="1", name="get_bids_for_listing", args_json='{"listing_ref":"latest"}'),
                Done(usage=Usage(0, 0)),
            ],
            [TextDelta(text="आपकी बोली मिल गई"), Done(usage=Usage(0, 0))],
        ]
    )

    result = await run_text_turn(
        pack=pack,
        registry=registry,
        handler=handler,
        llm=llm,
        ctx=ctx,
        language="hi-IN",
        history=[],
        user_text="bids?",
    )

    assert result.tools_called == ["get_bids_for_listing"]
    assert result.pending_action is None
    assert "get_bids_for_listing" in handler.calls


async def test_write_tool_stub_is_not_executed(pack, registry, ctx) -> None:
    handler = SpyMockToolHandler(pack.pack_dir)
    llm = SequencedFakeLLM(
        [
            [
                ToolCall(
                    id="1",
                    name="accept_bid",
                    args_json='{"listing_ref":"L-102","bid_ref":"B-9"}',
                ),
                Done(usage=Usage(0, 0)),
            ],
        ]
    )

    result = await run_text_turn(
        pack=pack,
        registry=registry,
        handler=handler,
        llm=llm,
        ctx=ctx,
        language="hi-IN",
        history=[],
        user_text="accept the highest bid",
    )

    assert result.pending_action == "accept_bid"
    assert result.executed is False
    assert "accept_bid" not in handler.calls  # confirm resolution may call get_bids_for_listing
    assert result.reply_text  # a rendered confirm question


async def test_yes_confirmation_executes_stored_args(pack, registry, ctx) -> None:
    handler = SpyMockToolHandler(pack.pack_dir)
    pending = PendingWrite(tool="accept_bid", args={"listing_ref": "L-102", "bid_ref": "B-9"})
    llm = FakeLLM()  # must not be called on a clear "yes"

    result = await run_text_turn(
        pack=pack,
        registry=registry,
        handler=handler,
        llm=llm,
        ctx=ctx,
        language="hi-IN",
        history=[],
        user_text="हाँ",
        pending_write=pending,
    )

    assert result.executed is True
    assert result.executed_tool == "accept_bid"
    assert result.confirmed_via == "voice"
    assert "accept_bid" in handler.calls
    assert llm.calls == []


async def test_no_cancels_without_calling_llm(pack, registry, ctx) -> None:
    handler = SpyMockToolHandler(pack.pack_dir)
    pending = PendingWrite(tool="accept_bid", args={"listing_ref": "L-102", "bid_ref": "B-9"})
    llm = FakeLLM()

    result = await run_text_turn(
        pack=pack,
        registry=registry,
        handler=handler,
        llm=llm,
        ctx=ctx,
        language="hi-IN",
        history=[],
        user_text="नहीं",
        pending_write=pending,
    )

    assert result.executed is False
    assert result.pending_status == "cancelled"
    assert handler.calls == []
    assert llm.calls == []


async def test_max_tool_rounds_cutoff_returns_fallback(pack, registry, ctx) -> None:
    handler = SpyMockToolHandler(pack.pack_dir)
    llm = FakeLLM(
        [
            ToolCall(id="1", name="get_demand_forecast", args_json='{"crop":"onion"}'),
            Done(usage=Usage(0, 0)),
        ]
    )

    result = await run_text_turn(
        pack=pack,
        registry=registry,
        handler=handler,
        llm=llm,
        ctx=ctx,
        language="en-IN",
        history=[],
        user_text="sell now or wait?",
    )

    assert result.pending_action is None
    assert result.reply_text  # fallback text, not a crash/hang
    assert len(llm.calls) == 4  # MAX_TOOL_ROUNDS


async def test_auto_retrieve_injects_knowledge_and_records_it_used(pack, registry, ctx) -> None:
    from voice_core.adapters.fakes.embeddings import FakeEmbedding
    from voice_core.adapters.fakes.knowledge import FakeKnowledgeStore
    from voice_core.ports.types import Chunk

    handler = SpyMockToolHandler(pack.pack_dir)
    store = FakeKnowledgeStore(
        [Chunk(doc_slug="pre-bidding-basics", doc_version=1, heading="h", text="t", similarity=0.9)]
    )
    llm = FakeLLM([TextDelta(text="Pre-bidding stays open 7 days."), Done(usage=Usage(0, 0))])

    result = await run_text_turn(
        pack=pack,
        registry=registry,
        handler=handler,
        llm=llm,
        ctx=ctx,
        language="en-IN",
        history=[],
        user_text="how long is pre-bidding open?",
        embeddings=FakeEmbedding(dim=4),
        knowledge_store=store,
        auto_rag_min_sim=0.45,
    )

    assert result.knowledge_used == ["pre-bidding-basics"]


async def test_no_knowledge_used_without_wiring(pack, registry, ctx) -> None:
    handler = SpyMockToolHandler(pack.pack_dir)
    llm = FakeLLM([TextDelta(text="ok"), Done(usage=Usage(0, 0))])

    result = await run_text_turn(
        pack=pack,
        registry=registry,
        handler=handler,
        llm=llm,
        ctx=ctx,
        language="en-IN",
        history=[],
        user_text="hello",
    )

    assert result.knowledge_used == []


async def test_set_preferred_language_switches_reply_language(pack, registry, ctx) -> None:
    handler = SpyMockToolHandler(pack.pack_dir)
    llm = SequencedFakeLLM(
        [
            [
                ToolCall(id="1", name="set_preferred_language", args_json='{"language":"mr-IN"}'),
                Done(usage=Usage(0, 0)),
            ],
            [TextDelta(text="ठीक आहे"), Done(usage=Usage(0, 0))],
        ]
    )

    result = await run_text_turn(
        pack=pack,
        registry=registry,
        handler=handler,
        llm=llm,
        ctx=ctx,
        language="hi-IN",
        history=[],
        user_text="मराठीत बोला",
    )

    assert result.reply_language == "mr-IN"
