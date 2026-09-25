"""Generate voice samples so a person can choose the assistant's female voice (bulbul:v3).

    uv run python -m voice_core.evals.audition            # needs SARVAM_API_KEY
    -> evals/audition/<speaker>/<lang>.wav  + evals/audition/index.md

Candidates are Sarvam's bulbul:v3 voices believed female from their names in the docs
(docs.sarvam.ai lists 14 female v3 voices, 2026-09-25). *Listen* before choosing — then set
`voice.tts_speaker` in the pack's pack.yaml (or TTS_SPEAKER in .env) and record it in STATUS.md.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

CANDIDATES = (
    "priya",
    "ritu",
    "neha",
    "pooja",
    "simran",
    "kavya",
    "ishita",
    "shreya",
    "roopa",
    "tanya",
    "shruti",
    "suhani",
    "kavitha",
    "rupali",
)


async def _main(args: argparse.Namespace) -> int:
    from voice_core.adapters.sarvam.client import SarvamClient
    from voice_core.adapters.sarvam.tts import SarvamTTS
    from voice_core.config import get_settings
    from voice_core.evals.latency import load_samples

    samples: dict[str, str] = load_samples(args.pack).get("audition") or {}
    out = Path(args.out)
    client = SarvamClient(get_settings().sarvam_api_key)
    tts = SarvamTTS(client)
    rows = [
        "# Voice audition (bulbul:v3)",
        "",
        "| speaker | hi-IN | mr-IN | en-IN |",
        "|---|---|---|---|",
    ]
    try:
        for speaker in args.speakers or CANDIDATES:
            cells = []
            for language, text in samples.items():
                try:
                    segment = await tts.synthesize(text, language, speaker, args.pace)
                except Exception as exc:  # report and continue with the next voice
                    cells.append(f"failed: {type(exc).__name__}")
                    continue
                path = out / speaker / f"{language}.wav"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(segment.data)
                cells.append(f"[{language}]({speaker}/{language}.wav)")
            rows.append(f"| {speaker} | " + " | ".join(cells) + " |")
            print(f"{speaker}: done")
    finally:
        await client.aclose()
    (out / "index.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"samples in {out.resolve()}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate female voice samples to choose from")
    parser.add_argument("--pack", default=None, help="default: DOMAIN_PACK setting")
    parser.add_argument("--out", default="evals/audition")
    parser.add_argument("--pace", type=float, default=0.95)
    parser.add_argument("speakers", nargs="*", help="limit to these speakers")
    sys.exit(asyncio.run(_main(parser.parse_args())))


if __name__ == "__main__":
    main()
