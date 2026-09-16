from __future__ import annotations

from pathlib import Path

from voice_core.ports.types import ToolContext, ToolDef
from voice_core.tools.handlers.mock import MockToolHandler

PACK_DIR = Path(__file__).resolve().parents[2] / "domain_packs" / "farm_marketplace"


async def test_placeholder_substitution() -> None:
    handler = MockToolHandler(PACK_DIR)
    tool = ToolDef(
        name="accept_bid",
        kind="write",
        handler_type="mock",
        config={"fixture": "fixtures/accept_bid_ok.json"},
    )
    ctx = ToolContext(user_ref="u-1", language="en-IN")

    result = await handler.call(tool, {"listing_ref": "L-102", "bid_ref": "B-9"}, ctx)

    assert result.status == "ok"
    assert result.data == {"order_ref": "O-77", "listing_ref": "L-102", "bid_ref": "B-9"}


async def test_error_fixture_maps_to_error_status() -> None:
    handler = MockToolHandler(PACK_DIR)
    tool = ToolDef(
        name="get_order_status",
        kind="read",
        handler_type="mock",
        config={"fixture": "fixtures/tool_error.json"},
    )
    ctx = ToolContext(user_ref="u-1", language="en-IN")

    result = await handler.call(tool, {}, ctx)

    assert result.status == "error"
    assert result.error_code == "HOST_UNAVAILABLE"
    assert result.data is None


async def test_fixture_overrides_swap_the_path_for_one_tool() -> None:
    handler = MockToolHandler(
        PACK_DIR, fixture_overrides={"get_order_status": "fixtures/tool_error.json"}
    )
    tool = ToolDef(
        name="get_order_status",
        kind="read",
        handler_type="mock",
        config={"fixture": "fixtures/orders.json"},
    )
    ctx = ToolContext(user_ref="u-1", language="en-IN")

    result = await handler.call(tool, {}, ctx)

    assert result.status == "error"


async def test_crops_json_is_never_read_by_the_mock_handler() -> None:
    # crops.json has no {status,data} envelope — it's a workflow slot-validator fixture, not a
    # tool fixture. No tool in tools.yaml points its handler.fixture at it, so MockToolHandler
    # never has a reason to load it. This test documents that boundary explicitly.
    import yaml

    tools = yaml.safe_load((PACK_DIR / "tools.yaml").read_text(encoding="utf-8"))
    fixture_paths = {t["handler"]["fixture"] for t in tools}
    assert "fixtures/crops.json" not in fixture_paths
