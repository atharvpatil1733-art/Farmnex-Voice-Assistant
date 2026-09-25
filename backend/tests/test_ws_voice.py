from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from voice_core.adapters.fakes.auth import FakeAuthVerifier
from voice_core.adapters.fakes.conversation import FakeConversationStore
from voice_core.adapters.fakes.llm import FakeLLM
from voice_core.packs.loader import load_pack
from voice_core.ports.errors import ProviderUnavailable
from voice_core.ports.types import (
    AudioSegment,
    Done,
    Principal,
    TextDelta,
    ToolCall,
    Transcript,
    Usage,
)
from voice_core.tools.handlers.mock import MockToolHandler
from voice_core.tools.registry import ToolRegistry
from voice_core.transport import ws as ws_module
from voice_core.transport.ws import VoiceDeps
from voice_core.transport.ws import router as voice_router

PACKS_ROOT = Path(__file__).resolve().parents[2] / "domain_packs"
ONE_SECOND = b"\x01\x00" * 16_000


class ScriptedSTT:
    def __init__(self, *transcripts: Transcript) -> None:
        self._transcripts = list(transcripts)
        self.calls = 0

    async def transcribe(self, audio: bytes, fmt: Any, language_hint: str | None) -> Transcript:
        self.calls += 1
        return self._transcripts.pop(0)

    def open_stream(self, fmt: Any, language_hint: str | None) -> Any:
        raise NotImplementedError


class RecordingTTS:
    def __init__(self, delay_s: float = 0.0, fail: bool = False) -> None:
        self.texts: list[str] = []
        self.delay_s = delay_s
        self.fail = fail

    async def synthesize(self, text: str, language: str, speaker: str, pace: float) -> AudioSegment:
        self.texts.append(text)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.fail:
            raise ProviderUnavailable("tts down")
        return AudioSegment(data=f"wav:{text}".encode(), fmt="wav_22050", duration_ms=100)


class SequencedLLM:
    name = "seq"

    def __init__(self, *scripts: list[Any]) -> None:
        self._scripts = list(scripts)

    async def stream(self, messages, tools, *, temperature, max_tokens, timeout_s):  # type: ignore[no-untyped-def]
        script = self._scripts.pop(0) if len(self._scripts) > 1 else self._scripts[0]
        for event in script:
            yield event


def _t(text: str, language: str = "hi-IN") -> Transcript:
    return Transcript(text=text, language_code=language, language_confidence=0.95, duration_ms=1000)


def _reply(text: str) -> list[Any]:
    return [TextDelta(text=text), Done(usage=Usage(0, 0))]


def _accept_call() -> list[Any]:
    return [
        ToolCall(id="w", name="accept_bid", args_json='{"listing_ref":"L-102","bid_ref":"B-9"}'),
        Done(usage=Usage(0, 0)),
    ]


def _make(
    *,
    stt: Any,
    llm: Any,
    tts: RecordingTTS | None = None,
    max_utterance_ms: int = 30_000,
    handler_delay_s: dict[str, float] | None = None,
    principal: Principal | None = None,
) -> tuple[TestClient, FakeConversationStore, RecordingTTS]:
    pack = load_pack("farm_marketplace", PACKS_ROOT)
    store = FakeConversationStore()
    tts = tts or RecordingTTS()
    app = FastAPI()
    app.include_router(voice_router, prefix="/v1")
    delays = handler_delay_s or {}

    class SlowHandler(MockToolHandler):
        async def call(self, tool, args, ctx):  # type: ignore[no-untyped-def]
            await asyncio.sleep(delays.get(tool.kind, 0.0))
            return await super().call(tool, args, ctx)

    app.state.voice_deps = VoiceDeps(
        pack=pack,
        registry=ToolRegistry(pack),
        handler=SlowHandler(pack.pack_dir),
        llm=llm,
        store=store,
        auth_verifier=FakeAuthVerifier({"tok": principal or Principal(user_ref="u-1")}),
        stt=stt,
        tts=tts,
        speaker="test-voice",
        pace=1.0,
        max_utterance_ms=max_utterance_ms,
    )
    return TestClient(app), store, tts


def _start(ws: Any, language: str = "hi-IN") -> dict[str, Any]:
    ws.send_text(json.dumps({"type": "session.start", "token": "tok", "language": language}))
    ready = json.loads(ws.receive_text())
    assert ready["type"] == "session.ready"
    return ready


def _collect_turn(ws: Any) -> tuple[list[dict[str, Any]], list[bytes]]:
    """Read frames until turn.end and the state message after it."""
    messages: list[dict[str, Any]] = []
    audio: list[bytes] = []
    while True:
        frame = ws.receive()
        if frame.get("bytes") is not None:
            audio.append(frame["bytes"])
            continue
        message = json.loads(frame["text"])
        messages.append(message)
        if message["type"] == "state" and any(m["type"] == "turn.end" for m in messages):
            return messages, audio


def _speak(ws: Any, utterance_id: str = "u-1", pcm: bytes = ONE_SECOND) -> None:
    ws.send_text(json.dumps({"type": "audio.start", "utterance_id": utterance_id}))
    ws.send_bytes(pcm)
    ws.send_text(json.dumps({"type": "audio.end", "utterance_id": utterance_id}))


def _types(messages: list[dict[str, Any]]) -> list[str]:
    return [m["type"] if m["type"] != "state" else f"state:{m['value']}" for m in messages]


def test_bad_token_closes_with_4401() -> None:
    client, _, _ = _make(stt=ScriptedSTT(), llm=FakeLLM())
    with client.websocket_connect("/v1/voice") as ws:
        ws.send_text(json.dumps({"type": "session.start", "token": "nope"}))
        with pytest.raises(WebSocketDisconnect) as info:
            ws.receive_text()
    assert info.value.code == 4401


def test_first_message_must_be_session_start() -> None:
    client, _, _ = _make(stt=ScriptedSTT(), llm=FakeLLM())
    with client.websocket_connect("/v1/voice") as ws:
        ws.send_text(json.dumps({"type": "ping"}))
        with pytest.raises(WebSocketDisconnect) as info:
            ws.receive_text()
    assert info.value.code == 4400


def test_voice_read_turn_runs_the_full_pipeline_in_order() -> None:
    llm = SequencedLLM(
        [
            ToolCall(id="1", name="get_bids_for_listing", args_json='{"listing_ref":"latest"}'),
            Done(usage=Usage(0, 0)),
        ],
        _reply("सबसे ऊँची बोली ₹27/kg है। पुणे के खरीदार की है।"),
    )
    client, store, tts = _make(stt=ScriptedSTT(_t("मेरी बोली कितनी आई")), llm=llm)
    with client.websocket_connect("/v1/voice") as ws:
        ready = _start(ws)
        _speak(ws)
        messages, audio = _collect_turn(ws)

    types = _types(messages)
    expected_order = [
        "state:listening",
        "state:transcribing",
        "transcript.final",
        "state:thinking",
        "tool.activity",
        "tool.activity",
        "assistant.text.final",
        "state:speaking",
        "audio.segment",
        "audio.segment",
        "turn.end",
        "state:idle",
    ]
    assert types == expected_order
    final = next(m for m in messages if m["type"] == "assistant.text.final")
    assert final["text"] == "सबसे ऊँची बोली ₹27/kg है। पुणे के खरीदार की है।"  # captions keep symbols
    assert tts.texts == ["सबसे ऊँची बोली 27 रुपये किलो है।", "पुणे के खरीदार की है।"]  # TTS doesn't
    segments = [m for m in messages if m["type"] == "audio.segment"]
    assert [s["seq"] for s in segments] == [0, 1]
    assert [s["is_last"] for s in segments] == [False, True]
    assert [s["byte_length"] for s in segments] == [len(a) for a in audio]
    turn_end = next(m for m in messages if m["type"] == "turn.end")
    assert {"stt", "llm", "tts_first_audio", "first_audio_total", "turn_total"} <= set(
        turn_end["latency_ms"]
    )
    roles = [m["role"] for m in store.messages]
    assert roles == ["user", "assistant"]
    assert store.messages[0]["input_mode"] == "voice"
    assert ready["conversation_id"] == store.messages[0]["conversation_id"]


def test_write_is_confirmed_by_voice_yes_and_reported() -> None:
    llm = SequencedLLM(_accept_call())
    client, store, _ = _make(stt=ScriptedSTT(_t("सबसे ऊँची बोली स्वीकार कर दो"), _t("हाँ")), llm=llm)
    with client.websocket_connect("/v1/voice") as ws:
        _start(ws)
        _speak(ws, "u-1")
        first, _ = _collect_turn(ws)
        _speak(ws, "u-2")
        second, _ = _collect_turn(ws)

    request = next(m for m in first if m["type"] == "confirm.request")
    assert request["tool"] == "accept_bid"
    assert request["labels"] == {"confirm": "हाँ", "cancel": "नहीं"}
    assert first[-1] == {"type": "state", "value": "awaiting_confirmation"}
    result = next(m for m in second if m["type"] == "action.result")
    assert result["action_id"] == request["action_id"]
    assert result["status"] == "executed_ok"
    assert store.actions[request["action_id"]].confirmed_via == "voice"


def test_button_confirm_executes_via_button() -> None:
    client, store, _ = _make(
        stt=ScriptedSTT(_t("सबसे ऊँची बोली स्वीकार कर दो")), llm=SequencedLLM(_accept_call())
    )
    with client.websocket_connect("/v1/voice") as ws:
        _start(ws)
        _speak(ws)
        first, _ = _collect_turn(ws)
        action_id = next(m for m in first if m["type"] == "confirm.request")["action_id"]
        ws.send_text(
            json.dumps({"type": "confirm.response", "action_id": action_id, "decision": "confirm"})
        )
        second, _ = _collect_turn(ws)

    result = next(m for m in second if m["type"] == "action.result")
    assert result["status"] == "executed_ok"
    assert store.actions[action_id].confirmed_via == "button"


def test_confirm_for_unknown_action_reports_expired() -> None:
    client, _, _ = _make(stt=ScriptedSTT(), llm=FakeLLM())
    with client.websocket_connect("/v1/voice") as ws:
        _start(ws)
        ws.send_text(
            json.dumps({"type": "confirm.response", "action_id": "pa-x", "decision": "confirm"})
        )
        messages, _ = _collect_turn(ws)
    assert {"type": "action.result", "action_id": "pa-x", "status": "expired"} in messages


def test_empty_transcript_is_a_localized_retryable_error() -> None:
    client, _, _ = _make(stt=ScriptedSTT(_t("")), llm=FakeLLM())
    with client.websocket_connect("/v1/voice") as ws:
        _start(ws)
        _speak(ws)
        messages, _ = _collect_turn(ws)
    error = next(m for m in messages if m["type"] == "error")
    assert error["code"] == "STT_EMPTY"
    assert error["retryable"] is True
    assert "सुन" in error["message"]


def test_too_long_utterance_is_rejected_without_calling_stt() -> None:
    stt = ScriptedSTT(_t("x"))
    client, _, _ = _make(stt=stt, llm=FakeLLM(), max_utterance_ms=500)
    with client.websocket_connect("/v1/voice") as ws:
        _start(ws)
        _speak(ws)
        ws.send_text(json.dumps({"type": "ping"}))
        frames = [json.loads(ws.receive_text()) for _ in range(3)]
    assert any(f.get("code") == "UTTERANCE_TOO_LONG" for f in frames)
    assert stt.calls == 0


def test_tts_failure_still_sends_captions_and_one_error() -> None:
    client, _, _ = _make(
        stt=ScriptedSTT(_t("नमस्ते")),
        llm=SequencedLLM(_reply("पहला। दूसरा।")),
        tts=RecordingTTS(fail=True),
    )
    with client.websocket_connect("/v1/voice") as ws:
        _start(ws)
        _speak(ws)
        messages, audio = _collect_turn(ws)
    assert any(m["type"] == "assistant.text.final" for m in messages)
    assert [m["code"] for m in messages if m["type"] == "error"] == ["TTS_FAILED"]
    assert audio == []


def test_interrupt_cancels_the_turn_and_marks_it_interrupted() -> None:
    client, store, _ = _make(
        stt=ScriptedSTT(_t("नमस्ते")),
        llm=SequencedLLM(_reply("एक। दो। तीन। चार।")),
        tts=RecordingTTS(delay_s=0.3),
    )
    with client.websocket_connect("/v1/voice") as ws:
        _start(ws)
        _speak(ws)
        while True:  # wait until speaking starts
            frame = ws.receive()
            if frame.get("text") and json.loads(frame["text"]).get("value") == "speaking":
                break
        ws.send_text(json.dumps({"type": "interrupt", "reason": "user_tap"}))
        messages, _ = _collect_turn(ws)
    turn_end = next(m for m in messages if m["type"] == "turn.end")
    assert turn_end["interrupted"] is True
    assert store.messages[-1]["interrupted"] is True


def test_language_set_is_persisted_and_used_next_session() -> None:
    client, store, _ = _make(stt=ScriptedSTT(), llm=FakeLLM())
    with client.websocket_connect("/v1/voice") as ws:
        _start(ws)
        ws.send_text(json.dumps({"type": "language.set", "language": "mr-IN"}))
        ws.send_text(json.dumps({"type": "ping"}))
        assert json.loads(ws.receive_text())["type"] == "pong"
    assert store.preferred_languages["u-1"] == "mr-IN"
    with client.websocket_connect("/v1/voice") as ws:
        assert _start(ws, language="hi-IN")["language"] == "mr-IN"


def test_malformed_frame_is_a_protocol_error_not_a_disconnect() -> None:
    client, _, _ = _make(stt=ScriptedSTT(), llm=FakeLLM())
    with client.websocket_connect("/v1/voice") as ws:
        _start(ws)
        ws.send_text(json.dumps({"type": "tool.call", "name": "accept_bid"}))
        assert json.loads(ws.receive_text())["code"] == "PROTOCOL_ERROR"
        ws.send_text(json.dumps({"type": "ping"}))
        assert json.loads(ws.receive_text())["type"] == "pong"


def test_ws_module_never_imports_vendor_sdks() -> None:
    source = Path(ws_module.__file__).read_text(encoding="utf-8")
    assert "sarvam" not in source.lower()


def test_double_tap_during_a_running_write_reports_the_real_result_once() -> None:
    """Review repro: the 2nd confirm.response cancelled the running confirm turn, and the
    user was told 'expired' for a write that actually succeeded."""
    client, store, _ = _make(
        stt=ScriptedSTT(_t("सबसे ऊँची बोली स्वीकार कर दो")),
        llm=SequencedLLM(_accept_call()),
        handler_delay_s={"write": 0.4},
    )
    with client.websocket_connect("/v1/voice") as ws:
        _start(ws)
        _speak(ws)
        first, _ = _collect_turn(ws)
        action_id = next(m for m in first if m["type"] == "confirm.request")["action_id"]
        tap = json.dumps(
            {"type": "confirm.response", "action_id": action_id, "decision": "confirm"}
        )
        ws.send_text(tap)
        ws.send_text(tap)
        seen, _ = _collect_turn(ws)
        # The duplicate tap is ignored: no second turn, nothing else queued behind the ping.
        ws.send_text(json.dumps({"type": "ping"}))
        while True:
            frame = ws.receive()
            if frame.get("text"):
                message = json.loads(frame["text"])
                if message["type"] == "pong":
                    break
                seen.append(message)
    results = [m for m in seen if m["type"] == "action.result"]
    assert [r["status"] for r in results if r["action_id"] == action_id] == ["executed_ok"]
    assert store.actions[action_id].status == "executed_ok"
    assert [m["interrupted"] for m in seen if m["type"] == "turn.end"] == [False]


def test_text_input_during_an_utterance_never_runs_two_turns() -> None:
    """Review repro: audio.start → text.input → audio.end started a second, orphaned turn."""
    stt = ScriptedSTT(_t("पहला"))
    client, store, _ = _make(stt=stt, llm=SequencedLLM(_reply("ठीक है।")))
    with client.websocket_connect("/v1/voice") as ws:
        _start(ws)
        ws.send_text(json.dumps({"type": "audio.start", "utterance_id": "u-1"}))
        ws.send_bytes(ONE_SECOND)
        ws.send_text(json.dumps({"type": "text.input", "utterance_id": "u-2", "text": "नमस्ते"}))
        ws.send_text(json.dumps({"type": "audio.end", "utterance_id": "u-1"}))
        messages, _ = _collect_turn(ws)
        ws.send_text(json.dumps({"type": "ping"}))
        assert json.loads(ws.receive_text())["type"] == "pong"
    assert stt.calls == 0
    assert [m["role"] for m in store.messages] == ["user", "assistant"]
    assert len([m for m in messages if m["type"] == "turn.end"]) == 1


def test_filler_is_never_marked_last_and_the_reply_ends_with_is_last(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ws_module, "FILLER_AFTER_S", 0.05)
    llm = SequencedLLM(
        [
            ToolCall(id="1", name="get_bids_for_listing", args_json='{"listing_ref":"latest"}'),
            Done(usage=Usage(0, 0)),
        ],
        _reply("सबसे ऊँची बोली 27 रुपये है।"),
    )
    client, _, _ = _make(stt=ScriptedSTT(_t("बोली")), llm=llm, handler_delay_s={"read": 0.3})
    with client.websocket_connect("/v1/voice") as ws:
        _start(ws)
        _speak(ws)
        messages, _ = _collect_turn(ws)
    segments = [m for m in messages if m["type"] == "audio.segment"]
    assert len(segments) == 2  # filler + reply
    assert [s["is_last"] for s in segments] == [False, True]


def test_turn_audio_always_ends_with_is_last_even_if_the_last_tts_fails() -> None:
    class FailLast(RecordingTTS):
        async def synthesize(self, text, language, speaker, pace):  # type: ignore[no-untyped-def]
            if text.startswith("दो"):
                raise ProviderUnavailable("down")
            return await super().synthesize(text, language, speaker, pace)

    client, _, _ = _make(
        stt=ScriptedSTT(_t("x")), llm=SequencedLLM(_reply("एक। दो।")), tts=FailLast()
    )
    with client.websocket_connect("/v1/voice") as ws:
        _start(ws)
        _speak(ws)
        messages, audio = _collect_turn(ws)
    segments = [m for m in messages if m["type"] == "audio.segment"]
    assert segments[-1]["is_last"] is True
    assert sum(1 for s in segments if s["is_last"]) == 1
    assert len(audio) == len(segments)


def test_expired_token_closes_with_4401_before_any_turn() -> None:
    expired = Principal(user_ref="u-1", claims={"exp": 1_000_000})  # 1970
    client, _, _ = _make(stt=ScriptedSTT(_t("x")), llm=FakeLLM(), principal=expired)
    with client.websocket_connect("/v1/voice") as ws:
        ws.send_text(json.dumps({"type": "session.start", "token": "tok"}))
        with pytest.raises(WebSocketDisconnect) as info:
            ws.receive_text()
    assert info.value.code == 4401


def test_token_expiring_mid_session_closes_before_the_next_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time as time_module

    soon = Principal(user_ref="u-1", claims={"exp": int(time_module.time()) + 60})
    client, _, _ = _make(stt=ScriptedSTT(_t("x")), llm=SequencedLLM(_reply("ok")), principal=soon)
    with client.websocket_connect("/v1/voice") as ws:
        _start(ws)
        real_time = time_module.time
        monkeypatch.setattr(ws_module, "time", SimpleNamespace(time=lambda: real_time() + 3600))
        ws.send_text(json.dumps({"type": "text.input", "utterance_id": "u-1", "text": "नमस्ते"}))
        with pytest.raises(WebSocketDisconnect) as info:
            ws.receive_text()  # raises on the close frame; no turn frames come first
    assert info.value.code == 4401
