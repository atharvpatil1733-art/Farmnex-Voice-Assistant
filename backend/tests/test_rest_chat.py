from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from voice_core.adapters.fakes.auth import FakeAuthVerifier
from voice_core.adapters.fakes.llm import FakeLLM
from voice_core.packs.loader import load_pack
from voice_core.ports.types import Done, Principal, TextDelta, ToolCall, Usage
from voice_core.tools.handlers.mock import MockToolHandler
from voice_core.tools.registry import ToolRegistry
from voice_core.transport.rest import router

PACKS_ROOT = Path(__file__).resolve().parents[2] / "domain_packs"


def _make_client(llm) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    pack = load_pack("farm_marketplace", PACKS_ROOT)
    app.state.pack = pack
    app.state.registry = ToolRegistry(pack)
    app.state.tool_handler = MockToolHandler(pack.pack_dir)
    app.state.llm = llm
    app.state.embeddings = None
    app.state.knowledge_store = None
    app.state.auto_rag_min_sim = 0.45
    app.state.auth_verifier = FakeAuthVerifier({"tok": Principal(user_ref="u-1")})
    return TestClient(app)


def test_chat_requires_auth() -> None:
    client = _make_client(FakeLLM())
    response = client.post("/v1/chat", json={"text": "hi", "language": "en-IN"})
    assert response.status_code == 403 or response.status_code == 401


def test_chat_read_tool_round_trip() -> None:
    llm = FakeLLM(
        [
            ToolCall(id="1", name="get_bids_for_listing", args_json='{"listing_ref":"latest"}'),
            TextDelta(text="ok"),
            Done(usage=Usage(0, 0)),
        ]
    )
    client = _make_client(llm)
    response = client.post(
        "/v1/chat",
        json={"text": "bids?", "language": "en-IN"},
        headers={"Authorization": "Bearer tok"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "get_bids_for_listing" in body["tools_called"]
    assert body["pending_action"] is None


def test_chat_write_tool_returns_pending_action_turn() -> None:
    llm = FakeLLM(
        [ToolCall(id="1", name="accept_bid", args_json='{"listing_ref":"L-102","bid_ref":"B-9"}')]
    )
    client = _make_client(llm)
    response = client.post(
        "/v1/chat",
        json={"text": "accept it", "language": "en-IN"},
        headers={"Authorization": "Bearer tok"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["pending_action"] == "accept_bid"
    assert body["executed"] is False
    assert body["pending_action_turn"]["role"] == "tool"


def test_chat_confirmation_round_trip_executes() -> None:
    llm = FakeLLM(
        [ToolCall(id="1", name="accept_bid", args_json='{"listing_ref":"L-102","bid_ref":"B-9"}')]
    )
    client = _make_client(llm)
    first = client.post(
        "/v1/chat",
        json={"text": "accept it", "language": "en-IN"},
        headers={"Authorization": "Bearer tok"},
    ).json()

    history = [
        {"role": "user", "content": "accept it"},
        {"role": "assistant", "content": first["reply"]},
        first["pending_action_turn"],
    ]
    second = client.post(
        "/v1/chat",
        json={"text": "yes", "language": "en-IN", "history": history},
        headers={"Authorization": "Bearer tok"},
    ).json()

    assert second["executed"] is True
    assert second["executed_tool"] == "accept_bid"
    assert second["confirmed_via"] == "voice"
