"""M4 gate: median time-to-first-audio for push-to-talk turns (≤ 2.5 s for a 5 s utterance on a
4G-like network, SPEC §6).

Drives a *running* server over the real WebSocket protocol with 16 kHz mono WAV clips. The clock
starts when `audio.end` is sent and stops at the first `audio.segment`. Run from this machine,
that's the server-side pipeline plus loopback; the 4G figure adds a modelled network cost (one
round trip plus downloading the first audio segment), reported separately so both are visible.

    uv run uvicorn app.main:app --port 8000        # with STT/TTS_PROVIDER=sarvam in .env
    uv run python -m voice_core.evals.latency --synthesize
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import statistics
import sys
import time
import wave
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

PCM_RATE = 16_000
CHUNK_BYTES = 3_200  # 100 ms of 16 kHz PCM16
GATE_MS = 2_500
TARGET_UTTERANCE_MS = 5_000


@dataclass(frozen=True)
class NetworkProfile:
    """Modelled mobile network: added to the loopback measurement."""

    rtt_ms: float = 120.0
    downlink_kbps: float = 2_000.0

    def cost_ms(self, first_segment_bytes: int) -> float:
        return self.rtt_ms + first_segment_bytes * 8 / self.downlink_kbps


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


def pad_pcm(pcm: bytes, min_ms: int = TARGET_UTTERANCE_MS) -> bytes:
    """Pad with trailing silence to at least `min_ms` (the gate is defined for 5 s utterances)."""
    needed = min_ms * PCM_RATE * 2 // 1000 - len(pcm)
    return pcm + b"\x00" * max(0, needed)


def read_wav_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (PCM_RATE, 1, 2):
            raise ValueError(f"{path}: need 16 kHz mono PCM16 WAV")
        return wav.readframes(wav.getnframes())


def load_samples(pack_id: str | None) -> dict[str, Any]:
    """Domain sentences live in the pack: domain_packs/<pack>/evals/voice_samples.yaml."""
    from voice_core.config import get_settings

    settings = get_settings()
    pack_id = pack_id or settings.domain_pack
    root = Path(__file__).resolve().parents[2] / settings.domain_packs_dir
    path = (root / pack_id / "evals" / "voice_samples.yaml").resolve()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a mapping")
    return data


async def synthesize_clips(out_dir: Path, pack_id: str | None) -> None:
    from voice_core.adapters.sarvam.client import SarvamClient
    from voice_core.adapters.sarvam.tts import SarvamTTS
    from voice_core.config import get_settings

    settings = get_settings()
    utterances = load_samples(pack_id).get("latency") or []
    client = SarvamClient(settings.sarvam_api_key)
    tts = SarvamTTS(client, sample_rate=PCM_RATE)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        for index, item in enumerate(utterances):
            language, text = item["language"], item["text"]
            segment = await tts.synthesize(text, language, settings.tts_speaker or "priya", 0.85)
            (out_dir / f"{index:02d}-{language}.wav").write_bytes(segment.data)
    finally:
        await client.aclose()


async def measure_clip(
    url: str, token: str, language: str, pcm: bytes, network: NetworkProfile
) -> dict[str, Any]:
    from websockets.asyncio.client import connect

    async with connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "session.start", "token": token, "language": language}))
        ready = json.loads(await ws.recv())
        if ready.get("type") != "session.ready":
            raise RuntimeError(f"session not ready: {ready}")
        await ws.send(json.dumps({"type": "audio.start", "utterance_id": "u-1"}))
        for start in range(0, len(pcm), CHUNK_BYTES):
            await ws.send(pcm[start : start + CHUNK_BYTES])

        started = time.perf_counter()
        await ws.send(json.dumps({"type": "audio.end", "utterance_id": "u-1"}))
        first_audio_ms: float | None = None
        first_bytes = 0
        transcript = ""
        server: dict[str, int] = {}
        errors: list[str] = []
        while True:
            frame = await asyncio.wait_for(ws.recv(), timeout=60)
            if isinstance(frame, bytes):
                if first_audio_ms is None:
                    first_audio_ms = (time.perf_counter() - started) * 1000
                    first_bytes = len(frame)
                continue
            message = json.loads(frame)
            kind = message.get("type")
            if kind == "transcript.final":
                transcript = message.get("text", "")
            elif kind == "error":
                errors.append(message.get("code", "?"))
            elif kind == "turn.end":
                server = message.get("latency_ms", {})
                break
        await ws.send(json.dumps({"type": "session.end"}))

    return {
        "language": language,
        "transcript": transcript,
        "first_audio_ms": first_audio_ms,
        "first_audio_4g_ms": (
            first_audio_ms + network.cost_ms(first_bytes) if first_audio_ms is not None else None
        ),
        "server": server,
        "errors": errors,
    }


def write_report(results: list[dict[str, Any]], network: NetworkProfile, reports_dir: Path) -> Path:
    loop_values = [r["first_audio_ms"] for r in results if r["first_audio_ms"] is not None]
    g4_values = [r["first_audio_4g_ms"] for r in results if r["first_audio_4g_ms"] is not None]
    median_4g = statistics.median(g4_values) if g4_values else float("inf")
    lines = [
        "# Latency report: push-to-talk time to first audio",
        "",
        f"- clips: {len(results)} ({len(g4_values)} produced audio)",
        f"- network model: RTT {network.rtt_ms:.0f} ms, downlink {network.downlink_kbps:.0f} kbps",
        f"- loopback median / p90: {statistics.median(loop_values) if loop_values else 0:.0f} / "
        f"{percentile(loop_values, 90):.0f} ms",
        f"- **4G-modelled median / p90: {median_4g:.0f} / {percentile(g4_values, 90):.0f} ms "
        f"— gate ≤ {GATE_MS} ms: {'PASS' if median_4g <= GATE_MS else 'FAIL'}**",
        "",
        "| # | lang | first audio (loopback) | 4G model | stt | llm | tts_first | errors "
        "| transcript |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for index, r in enumerate(results):
        s = r["server"]
        lines.append(
            f"| {index} | {r['language']} | {r['first_audio_ms'] or 0:.0f} | "
            f"{r['first_audio_4g_ms'] or 0:.0f} | {s.get('stt', '')} | {s.get('llm', '')} | "
            f"{s.get('tts_first_audio', '')} | {','.join(r['errors'])} | {r['transcript'][:60]} |"
        )
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{datetime.now(tz=UTC):%Y%m%dT%H%M%SZ}-latency.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


async def _main(args: argparse.Namespace) -> int:
    clips_dir = Path(args.clips)
    if args.synthesize:
        await synthesize_clips(clips_dir, args.pack)
    clips = sorted(clips_dir.glob("*.wav"))
    if not clips:
        print(f"no clips in {clips_dir}; pass --synthesize (needs SARVAM_API_KEY)", file=sys.stderr)
        return 2
    network = NetworkProfile(rtt_ms=args.rtt_ms, downlink_kbps=args.downlink_kbps)
    token = os.environ.get(args.token_env, "dev-token")  # dev server accepts "dev-token"
    results = []
    for repeat in range(args.repeat):
        for clip in clips:
            language = clip.stem.split("-", 1)[1] if "-" in clip.stem else "hi-IN"
            pcm = pad_pcm(read_wav_pcm(clip))
            result = await measure_clip(args.url, token, language, pcm, network)
            results.append(result)
            print(f"[{repeat}] {clip.name}: {result['first_audio_ms'] or 0:.0f} ms loopback")
    path = write_report(results, network, Path(args.reports))
    print(f"report: {path}")
    g4 = [r["first_audio_4g_ms"] for r in results if r["first_audio_4g_ms"] is not None]
    return 0 if g4 and statistics.median(g4) <= GATE_MS else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Push-to-talk latency gate (M4)")
    parser.add_argument("--pack", default=None, help="default: DOMAIN_PACK setting")
    parser.add_argument("--url", default="ws://127.0.0.1:8000/v1/voice")
    # Token from the env, not argv (a real JWT would land in shell history / ps).
    parser.add_argument("--token-env", default="VOICE_TOKEN", help="env var with the JWT")
    parser.add_argument("--clips", default="evals/latency_clips")
    parser.add_argument("--synthesize", action="store_true", help="make clips with Sarvam TTS")
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--rtt-ms", type=float, default=120.0)
    parser.add_argument("--downlink-kbps", type=float, default=2_000.0)
    parser.add_argument("--reports", default="evals/reports")
    sys.exit(asyncio.run(_main(parser.parse_args())))


if __name__ == "__main__":
    main()


def _wav_bytes(pcm: bytes) -> bytes:  # used by tests
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(PCM_RATE)
        wav.writeframes(pcm)
    return buffer.getvalue()
