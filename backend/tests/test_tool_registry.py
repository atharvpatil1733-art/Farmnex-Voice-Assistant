from __future__ import annotations

from pathlib import Path

import pytest

from voice_core.packs.loader import load_pack
from voice_core.ports.types import ToolContext
from voice_core.tools.handlers.mock import MockToolHandler
from voice_core.tools.registry import ToolRegistry
from voice_core.tools.schema import assert_no_identity_fields

PACKS_ROOT = Path(__file__).resolve().parents[2] / "domain_packs"


@pytest.fixture
def pack():
    return load_pack("farm_marketplace", PACKS_ROOT)


@pytest.fixture
def registry(pack) -> ToolRegistry:
    return ToolRegistry(pack)


@pytest.fixture
def handler(pack) -> MockToolHandler:
    return MockToolHandler(pack.pack_dir)


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(user_ref="u-1", language="hi-IN")


def test_tool_specs_merge_core_and_pack_tools_without_collision(registry: ToolRegistry) -> None:
    specs = registry.tool_specs()
    names = [s.name for s in specs]

    assert len(names) == len(set(names))
    assert "search_knowledge" in names
    assert "set_preferred_language" in names
    assert "end_conversation" in names
    assert len(names) == 11  # 3 core + 8 pack


def test_assert_no_identity_fields_rejects_user_id() -> None:
    schema = {
        "type": "object",
        "properties": {"user_id": {"type": "string"}},
    }
    with pytest.raises(ValueError, match="identity"):
        assert_no_identity_fields(schema, "some_tool")


def test_assert_no_identity_fields_allows_domain_refs() -> None:
    schema = {
        "type": "object",
        "properties": {"listing_ref": {"type": "string"}, "quantity_kg": {"type": "number"}},
    }
    assert_no_identity_fields(schema, "some_tool")  # should not raise


async def test_dispatch_rejects_unknown_arg_without_calling_handler(
    registry: ToolRegistry, handler: MockToolHandler, ctx: ToolContext
) -> None:
    result = await registry.dispatch(
        "get_bids_for_listing", {"listing_ref": "latest", "extra": "nope"}, ctx, handler
    )
    assert result.status == "invalid"
    assert result.error_code == "SCHEMA_INVALID"


async def test_dispatch_rejects_missing_required_arg(
    registry: ToolRegistry, handler: MockToolHandler, ctx: ToolContext
) -> None:
    result = await registry.dispatch("get_bids_for_listing", {}, ctx, handler)
    assert result.status == "invalid"


async def test_dispatch_trims_result_to_result_fields(
    registry: ToolRegistry, handler: MockToolHandler, ctx: ToolContext
) -> None:
    result = await registry.dispatch("get_order_status", {"order_ref": "latest"}, ctx, handler)
    assert result.status == "ok"
    assert result.data is not None
    assert set(result.data.keys()) <= set(registry.get("get_order_status").result_fields)


async def test_dispatch_get_my_listings_returns_nested_listings(
    registry: ToolRegistry, handler: MockToolHandler, ctx: ToolContext
) -> None:
    """Regression test: get_my_listings' fixture nests every listing under a top-level
    `listings` array, so result_fields must whitelist `listings` itself, not the per-item
    field names — those don't exist as top-level keys and would trim the result to {}."""
    result = await registry.dispatch("get_my_listings", {}, ctx, handler)
    assert result.status == "ok"
    assert result.data
    assert result.data["listings"]
    assert result.data["listings"][0]["listing_ref"]


async def test_resolve_confirm_fields_merges_matching_record(
    registry: ToolRegistry, handler: MockToolHandler, ctx: ToolContext
) -> None:
    fields = await registry.resolve_confirm_fields(
        "accept_bid", {"listing_ref": "L-102", "bid_ref": "B-9"}, ctx, handler
    )
    assert fields["price_per_kg"] == 27
    assert fields["crop"] == "onion"
    assert fields["quantity_kg"] == 500


def test_is_write_distinguishes_read_and_write_tools(registry: ToolRegistry) -> None:
    assert registry.is_write("accept_bid") is True
    assert registry.is_write("get_bids_for_listing") is False


async def test_search_knowledge_stub_without_embeddings_or_store(
    registry: ToolRegistry, handler: MockToolHandler, ctx: ToolContext
) -> None:
    result = await registry.dispatch("search_knowledge", {"query": "anything"}, ctx, handler)
    assert result.status == "ok"
    assert result.data == {"chunks": []}


async def test_search_knowledge_returns_real_chunks_when_wired(
    pack, handler: MockToolHandler, ctx: ToolContext
) -> None:
    from voice_core.adapters.fakes.embeddings import FakeEmbedding
    from voice_core.adapters.fakes.knowledge import FakeKnowledgeStore
    from voice_core.ports.types import Chunk

    store = FakeKnowledgeStore(
        [Chunk(doc_slug="pre-bidding-basics", doc_version=1, heading="h", text="t", similarity=0.9)]
    )
    wired_registry = ToolRegistry(pack, embeddings=FakeEmbedding(dim=4), knowledge_store=store)

    result = await wired_registry.dispatch(
        "search_knowledge", {"query": "how long is bidding open"}, ctx, handler
    )

    assert result.status == "ok"
    assert result.data is not None
    assert result.data["chunks"][0]["source"] == "pre-bidding-basics@v1"
