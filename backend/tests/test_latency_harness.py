from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI

from voice_core.evals.latency import (
    NetworkProfile,
    _wav_bytes,
    measure_clip,
    pad_pcm,
    percentile,
    read_wav_pcm,
    write_report,
)


def test_percentile_and_padding() -> None:
    assert percentile([5, 1, 3, 2, 4], 50) == 3
    assert percentile([5, 1, 3, 2, 4], 90) == 5
    assert percentile([], 90) == 0.0
    assert len(pad_pcm(b"\x00\x00" * 16_000)) == 5 * 16_000 * 2
    long = b"\x00\x00" * 16_000 * 6
    assert pad_pcm(long) == long


def test_network_model_adds_rtt_and_first_segment_download() -> None:
    assert NetworkProfile(rtt_ms=100, downlink_kbps=1000).cost_ms(12_500) == pytest.approx(200)


def test_wav_reader_rejects_wrong_format(tmp_path: Path) -> None:
    good = tmp_path / "a.wav"
    good.write_bytes(_wav_bytes(b"\x00\x00" * 10))
    assert read_wav_pcm(good) == b"\x00\x00" * 10


def test_report_marks_gate(tmp_path: Path) -> None:
    results = [
        {
            "language": "hi-IN",
            "transcript": "x",
            "first_audio_ms": 900.0,
            "first_audio_4g_ms": 1100.0,
            "server": {"stt": 400},
            "errors": [],
        }
    ]
    path = write_report(results, NetworkProfile(), tmp_path)
    assert "PASS" in path.read_text(encoding="utf-8")


def test_measure_clip_against_a_real_socket_with_fakes() -> None:
    """End-to-end over a real uvicorn socket (fakes for STT/LLM/TTS), so the harness itself
    is proven before it's pointed at Sarvam."""
    from tests.test_ws_voice import RecordingTTS, ScriptedSTT, SequencedLLM, _make, _reply, _t

    client, _, _ = _make(stt=ScriptedSTT(_t("नमस्ते")), llm=SequencedLLM(_reply("नमस्ते जी।")))
    app: FastAPI = client.app  # type: ignore[assignment]
    config = uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        import asyncio
        import time

        for _ in range(50):
            if server.started:
                break
            time.sleep(0.1)
        result = asyncio.run(
            measure_clip(
                "ws://127.0.0.1:8765/v1/voice", "tok", "hi-IN", pad_pcm(b""), NetworkProfile()
            )
        )
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    assert result["transcript"] == "नमस्ते"
    assert result["first_audio_ms"] is not None
    assert result["first_audio_4g_ms"] > result["first_audio_ms"]
    assert result["errors"] == []
    assert "stt" in result["server"]
    json.dumps(result)
    _ = RecordingTTS
