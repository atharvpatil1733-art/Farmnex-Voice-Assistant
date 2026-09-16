from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from voice_core.adapters.fakes.llm import FakeLLM
from voice_core.agent.loop import PendingWrite, TurnResult, run_text_turn
from voice_core.config import Settings, get_settings
from voice_core.evals.metrics import CaseOutcome, evaluate_case, tool_selection_accuracy
from voice_core.packs.loader import LoadedPack, load_pack
from voice_core.ports.llm import LLMProvider
from voice_core.ports.types import ChatMessage, ToolContext
from voice_core.tools.handlers.mock import MockToolHandler
from voice_core.tools.registry import ToolRegistry

_SUITE_FILENAMES = {
    "text": "golden.jsonl",
    "redteam": "redteam.jsonl",
    "retrieval": "retrieval.jsonl",
}


def _resolve_llm(spec: str, settings: Settings) -> tuple[LLMProvider, str]:
    provider, _, model = spec.partition(":")
    if provider == "fake":
        return FakeLLM(), "mechanism-smoke-test"
    if provider == "gemini":
        from voice_core.adapters.gemini.llm import GeminiLLM

        return GeminiLLM(api_key=settings.llm_api_key, model=model or settings.llm_model), "gate"
    raise ValueError(f"unknown --llm spec: {spec!r} (use fake or gemini:<model>)")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    cases = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


async def run_case(
    case: dict[str, Any],
    pack: LoadedPack,
    registry: ToolRegistry,
    llm: LLMProvider,
) -> list[TurnResult]:
    handler = MockToolHandler(pack.pack_dir, fixture_overrides=case.get("fixture_overrides"))
    ctx = ToolContext(user_ref="eval-user", language=case["language"])
    history: list[ChatMessage] = []
    pending_write: PendingWrite | None = None
    language = case["language"]
    results: list[TurnResult] = []

    for turn in case["turns"]:
        user_text = turn["user"]
        result = await run_text_turn(
            pack=pack,
            registry=registry,
            handler=handler,
            llm=llm,
            ctx=ctx,
            language=language,
            history=history,
            user_text=user_text,
            pending_write=pending_write,
        )
        results.append(result)

        history.append(ChatMessage(role="user", content=user_text))
        history.append(ChatMessage(role="assistant", content=result.reply_text))
        if result.pending_action is not None:
            marker = json.dumps(
                {"status": "awaiting_confirmation", "args": result.pending_write_args}
            )
            history.append(
                ChatMessage(
                    role="tool",
                    content=f'<tool_result tool="{result.pending_action}">{marker}</tool_result>',
                )
            )
            pending_write = PendingWrite(
                tool=result.pending_action, args=result.pending_write_args or {}
            )
        else:
            pending_write = None

        language = result.reply_language

    return results


def _render_report(
    pack_id: str,
    suite: str,
    llm_spec: str,
    mode: str,
    outcomes: list[CaseOutcome],
    accuracy: float,
) -> str:
    lines = [
        f"# Eval report: {pack_id} / {suite}",
        "",
        f"- mode: {mode}",
        f"- llm: {llm_spec}",
        f"- tool-selection accuracy: {accuracy:.1f}%",
        f"- cases: {len(outcomes)}",
        "",
    ]
    excluded = [o.case_id for o in outcomes if o.excluded]
    if excluded:
        lines.append(f"Excluded from the accuracy denominator (no KB until M2): {excluded}")
        lines.append("")

    lines.append("| case | structural | content | failing checks |")
    lines.append("|---|---|---|---|")
    for outcome in outcomes:
        structural = "PASS" if outcome.structural_passed else "FAIL"
        content_pass = all(c.passed for c in outcome.content_checks)
        content_label = "PASS" if content_pass else "FAIL"
        all_checks = outcome.structural_checks + outcome.content_checks
        failing = [c.key for c in all_checks if not c.passed]
        row = f"| {outcome.case_id} | {structural} | {content_label} | {', '.join(failing)} |"
        lines.append(row)
    return "\n".join(lines) + "\n"


async def _main_async(args: argparse.Namespace) -> int:
    if args.suite == "retrieval":
        print("retrieval suite needs a KnowledgeStore (M2); not runnable yet.", file=sys.stderr)
        return 1

    settings = get_settings()
    backend_dir = Path(__file__).resolve().parents[2]
    packs_root = (backend_dir / settings.domain_packs_dir).resolve()
    pack = load_pack(args.pack, packs_root)
    registry = ToolRegistry(pack)

    llm_spec = args.llm or f"{settings.llm_provider}:{settings.llm_model}"
    llm, mode = _resolve_llm(llm_spec, settings)

    cases = _load_jsonl(pack.pack_dir / "evals" / _SUITE_FILENAMES[args.suite])

    outcomes: list[CaseOutcome] = []
    for _ in range(args.repeat):
        for case in cases:
            turns = await run_case(case, pack, registry, llm)
            outcomes.append(evaluate_case(case, turns))

    accuracy = tool_selection_accuracy(outcomes)
    report = _render_report(args.pack, args.suite, llm_spec, mode, outcomes, accuracy)

    reports_dir = backend_dir / "evals" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = reports_dir / f"{timestamp}-{args.suite}.md"
    report_path.write_text(report, encoding="utf-8")

    print(f"mode={mode} suite={args.suite} cases={len(cases)} repeat={args.repeat}")
    print(f"tool_selection_accuracy={accuracy:.1f}%")
    print(f"report: {report_path}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the voice assistant eval suite")
    parser.add_argument("--pack", required=True)
    parser.add_argument("--suite", choices=["text", "redteam", "retrieval"], default="text")
    parser.add_argument("--llm", default=None, help="fake | gemini:<model>")
    parser.add_argument("--repeat", type=int, default=1)
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    sys.exit(asyncio.run(_main_async(args)))


if __name__ == "__main__":
    main()
