from __future__ import annotations

import pytest

from voice_core.adapters.fakes.auth import AuthError, FakeAuthVerifier
from voice_core.adapters.fakes.embeddings import FakeEmbedding
from voice_core.adapters.fakes.host import FakeHostToolHandler
from voice_core.adapters.fakes.knowledge import FakeKnowledgeStore
from voice_core.adapters.fakes.llm import FakeLLM
from voice_core.adapters.fakes.stt import FakeSTT
from voice_core.adapters.fakes.tts import FakeTTS
from voice_core.ports.auth import AuthVerifier
from voice_core.ports.embeddings import EmbeddingProvider
from voice_core.ports.host import HostToolHandler
from voice_core.ports.knowledge import KnowledgeStore
from voice_core.ports.llm import LLMProvider
from voice_core.ports.stt import STTProvider
from voice_core.ports.tts import TTSProvider
from voice_core.ports.types import (
    ChatMessage,
    Chunk,
    Done,
    Principal,
    ToolCall,
    ToolContext,
    ToolDef,
    ToolResult,
    Usage,
)


def test_fakes_satisfy_their_protocols() -> None:
    assert isinstance(FakeLLM(), LLMProvider)
    assert isinstance(FakeSTT(), STTProvider)
    assert isinstance(FakeTTS(), TTSProvider)
    assert isinstance(FakeEmbedding(), EmbeddingProvider)
    assert isinstance(FakeKnowledgeStore(), KnowledgeStore)
    assert isinstance(FakeAuthVerifier(), AuthVerifier)
    assert isinstance(FakeHostToolHandler(), HostToolHandler)


async def test_fake_llm_replays_scripted_events() -> None:
    llm = FakeLLM([ToolCall(id="1", name="do_thing", args_json="{}"), Done(Usage(1, 1))])
    events = [
        e
        async for e in llm.stream(
            [ChatMessage(role="user", content="hi")],
            [],
            temperature=0.0,
            max_tokens=10,
            timeout_s=1.0,
        )
    ]
    assert events[0] == ToolCall(id="1", name="do_thing", args_json="{}")
    assert isinstance(events[1], Done)


async def test_fake_stt_returns_configured_transcript() -> None:
    from voice_core.ports.types import Transcript

    stt = FakeSTT(
        Transcript(text="hello", language_code="en-IN", language_confidence=0.9, duration_ms=500)
    )
    result = await stt.transcribe(b"", "pcm_s16le_16k", None)
    assert result.text == "hello"


async def test_fake_tts_returns_audio_segment() -> None:
    tts = FakeTTS()
    segment = await tts.synthesize("hello", "en-IN", "voice-a", 1.0)
    assert segment.data
    assert segment.fmt == "wav_22050"


async def test_fake_embedding_is_deterministic() -> None:
    embed = FakeEmbedding(dim=4)
    v1 = await embed.embed(["same text"], "query")
    v2 = await embed.embed(["same text"], "query")
    assert v1 == v2
    assert len(v1[0]) == 4


async def test_fake_knowledge_store_filters_by_similarity() -> None:
    store = FakeKnowledgeStore(
        [
            Chunk(doc_slug="a", doc_version=1, heading="h", text="t", similarity=0.9),
            Chunk(doc_slug="b", doc_version=1, heading="h", text="t", similarity=0.1),
        ]
    )
    matches = await store.match("pack", [0.0], k=5, min_similarity=0.5, domains=None)
    assert [c.doc_slug for c in matches] == ["a"]


async def test_fake_auth_verifier_rejects_unknown_token() -> None:
    verifier = FakeAuthVerifier({"tok-1": Principal(user_ref="u-1")})
    principal = await verifier.verify("tok-1")
    assert principal.user_ref == "u-1"
    with pytest.raises(AuthError):
        await verifier.verify("unknown")


async def test_fake_host_tool_handler_returns_not_found_without_fixture() -> None:
    handler = FakeHostToolHandler({"known_tool": ToolResult(status="ok", data={"x": 1})})
    tool = ToolDef(name="known_tool", kind="read", handler_type="mock", config={})
    ctx = ToolContext(user_ref="u-1", language="en-IN")

    ok_result = await handler.call(tool, {}, ctx)
    assert ok_result.status == "ok"

    unknown_tool = ToolDef(name="missing", kind="read", handler_type="mock", config={})
    missing_result = await handler.call(unknown_tool, {}, ctx)
    assert missing_result.status == "not_found"
