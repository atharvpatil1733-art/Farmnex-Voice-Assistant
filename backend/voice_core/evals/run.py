from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from voice_core.adapters.embeddings_factory import build_embeddings
from voice_core.adapters.fakes.conversation import FakeConversationStore
from voice_core.adapters.fakes.llm import FakeLLM
from voice_core.agent.confirmation import ConfirmationGate
from voice_core.agent.loop import TurnResult, resolve_pending_action, run_text_turn
from voice_core.config import Settings, get_settings
from voice_core.evals.metrics import CaseOutcome, evaluate_case, tool_selection_accuracy
from voice_core.kb.retriever import search_knowledge
from voice_core.packs.loader import LoadedPack, load_pack
from voice_core.ports.embeddings import EmbeddingProvider
from voice_core.ports.knowledge import KnowledgeStore
from voice_core.ports.llm import LLMProvider
from voice_core.ports.types import ChatMessage, ToolContext
from voice_core.tools.handlers.mock import MockToolHandler
from voice_core.tools.registry import ToolRegistry

_SUITE_FILENAMES = {
    "text": "golden.jsonl",
    "redteam": "redteam.jsonl",
    "retrieval": "retrieval.jsonl",
}


def _build_chain_provider(entry_provider: str, model: str, settings: Settings) -> LLMProvider:
    if not model:
        raise ValueError(f"LLM_FALLBACK_CHAIN entry {entry_provider!r} is missing a model")
    if entry_provider == "gemini":
        from voice_core.adapters.gemini.llm import GeminiLLM

        return GeminiLLM(api_key=settings.gemini_api_key, model=model, max_attempts=1)
    if entry_provider == "groq":
        from voice_core.adapters.openai_compat.llm import OpenAICompatLLM

        return OpenAICompatLLM(
            api_key=settings.groq_api_key,
            model=model,
            base_url=settings.groq_base_url,
            max_attempts=1,
        )
    raise ValueError(f"unknown provider {entry_provider!r} in LLM_FALLBACK_CHAIN")


def _resolve_llm(spec: str, settings: Settings) -> tuple[LLMProvider, str]:
    provider, _, model = spec.partition(":")
    if provider == "fake":
        return FakeLLM(), "mechanism-smoke-test"
    if provider == "fallback":
        from voice_core.adapters.fallback.llm import FallbackLLM

        chain = model or settings.llm_fallback_chain
        providers = []
        for entry in chain.split(","):
            entry_provider, _, entry_model = entry.strip().partition(":")
            providers.append((entry, _build_chain_provider(entry_provider, entry_model, settings)))
        return FallbackLLM(providers), "gate"
    if provider == "gemini":
        from voice_core.adapters.gemini.llm import GeminiLLM

        return GeminiLLM(api_key=settings.llm_api_key, model=model or settings.llm_model), "gate"
    if provider == "openai_compat":
        from voice_core.adapters.openai_compat.llm import OpenAICompatLLM

        return (
            OpenAICompatLLM(
                api_key=settings.llm_api_key,
                model=model or settings.llm_model,
                base_url=settings.llm_base_url,
            ),
            "gate",
        )
    raise ValueError(
        f"unknown --llm spec: {spec!r} "
        "(use fake, gemini:<model>, openai_compat:<model>, or fallback[:<chain>])"
    )


def _build_knowledge_store(settings: Settings) -> KnowledgeStore | None:
    if not settings.database_url:
        return None
    from voice_core.adapters.supabase.store import SupabaseKnowledgeStore

    return SupabaseKnowledgeStore(
        database_url=settings.database_url,
        statement_cache_size=settings.db_statement_cache_size,
    )


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    cases = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


class _EvalClock:
    def __init__(self) -> None:
        self.now = datetime.now(tz=UTC)

    def __call__(self) -> datetime:
        return self.now


async def run_case(
    case: dict[str, Any],
    pack: LoadedPack,
    registry: ToolRegistry,
    llm: LLMProvider,
    embeddings: EmbeddingProvider | None = None,
    knowledge_store: KnowledgeStore | None = None,
    auto_rag_min_sim: float = 0.45,
) -> list[TurnResult]:
    """Run one case against a fresh in-memory ConversationStore (evals never touch the real
    conversation tables). Turns are {"user": text}, {"button": "yes"|"no"} for the confirm
    card, and may carry "advance_seconds" to move the clock first (expiry cases)."""
    handler = MockToolHandler(pack.pack_dir, fixture_overrides=case.get("fixture_overrides"))
    ctx = ToolContext(user_ref="eval-user", language=case["language"])
    store = FakeConversationStore()
    clock = _EvalClock()
    conversation_id = await store.create_conversation(
        "eval-user", pack.id, "eval", case["language"]
    )
    history: list[ChatMessage] = []
    language = case["language"]
    results: list[TurnResult] = []

    for turn in case["turns"]:
        clock.now += timedelta(seconds=turn.get("advance_seconds", 0))
        if "button" in turn:
            action, _ = await ConfirmationGate(store, clock=clock).current(conversation_id)
            if action is None or action.status != "pending":
                # The model never proposed the write: the case fails on its checks, not here.
                results.append(
                    TurnResult(
                        reply_text="",
                        reply_language=language,
                        tools_called=[],
                        tool_results=[],
                        knowledge_used=[],
                        pending_action=None,
                        pending_write_args=None,
                        executed=False,
                        executed_tool=None,
                        confirmed_via=None,
                        pending_status=None,
                        prompt_hash="",
                    )
                )
                continue
            result = await resolve_pending_action(
                registry=registry,
                handler=handler,
                store=store,
                ctx=ctx,
                action=action,
                decision=turn["button"],
                via="button",
                language=language,
                clock=clock,
            )
            results.append(result)
            continue

        user_text = turn["user"]
        result = await run_text_turn(
            pack=pack,
            registry=registry,
            handler=handler,
            llm=llm,
            store=store,
            conversation_id=conversation_id,
            ctx=ctx,
            language=language,
            history=history,
            user_text=user_text,
            embeddings=embeddings,
            knowledge_store=knowledge_store,
            auto_rag_min_sim=auto_rag_min_sim,
            clock=clock,
        )
        results.append(result)
        history.append(ChatMessage(role="user", content=user_text))
        history.append(ChatMessage(role="assistant", content=result.reply_text))
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
        lines.append(f"Excluded from the accuracy denominator: {excluded}")
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

    # Actual-vs-expected for every failing check, so a run is diagnosable without re-running
    # it (re-runs cost provider quota and, on constrained hosts, get killed part-way).
    failing_details = [
        (o.case_id, c)
        for o in outcomes
        for c in o.structural_checks + o.content_checks
        if not c.passed
    ]
    if failing_details:
        lines.append("")
        lines.append("## Failure details")
        lines.append("")
        for case_id, check in failing_details:
            suffix = f" — {check.detail}" if check.detail else ""
            lines.append(f"- **{case_id}** `{check.key}`{suffix}")
        lines.append("")
        lines.append("## Tools called per case")
        lines.append("")
        for outcome in outcomes:
            called = [name for turn in outcome.turns for name in turn.tools_called]
            lines.append(f"- **{outcome.case_id}**: {called or 'none'}")
        lines.append("")
        lines.append("## Final reply per case")
        lines.append("")
        for outcome in outcomes:
            reply = outcome.turns[-1].reply_text if outcome.turns else ""
            reply = " ".join(reply.split())
            if len(reply) > 300:
                reply = reply[:300] + "…"
            lines.append(f"- **{outcome.case_id}**: {reply or '(empty)'}")
    return "\n".join(lines) + "\n"


async def run_retrieval_suite(
    cases: list[dict[str, Any]],
    pack_id: str,
    embeddings: EmbeddingProvider,
    store: KnowledgeStore,
    k: int = 3,
) -> tuple[float, float, float, list[dict[str, Any]]]:
    hits_at_1 = 0
    hits_at_3 = 0
    mrr_total = 0.0
    rows: list[dict[str, Any]] = []

    for case in cases:
        chunks = await search_knowledge(
            query=case["question"],
            pack_id=pack_id,
            language=case["language"],
            embeddings=embeddings,
            store=store,
            k=k,
            min_similarity=0.0,
        )
        retrieved = [c.doc_slug for c in chunks]
        expected = set(case["expected_slugs"])

        hit1 = bool(retrieved[:1]) and retrieved[0] in expected
        hit3 = any(slug in expected for slug in retrieved[:3])
        rank = next((i + 1 for i, slug in enumerate(retrieved) if slug in expected), None)

        hits_at_1 += int(hit1)
        hits_at_3 += int(hit3)
        mrr_total += 1.0 / rank if rank else 0.0
        rows.append(
            {
                "id": case["id"],
                "expected": sorted(expected),
                "retrieved": retrieved,
                "hit@3": hit3,
            }
        )

    n = len(cases) or 1
    return (100.0 * hits_at_1 / n, 100.0 * hits_at_3 / n, mrr_total / n, rows)


def _render_retrieval_report(
    pack_id: str, hit1: float, hit3: float, mrr: float, rows: list[dict[str, Any]]
) -> str:
    lines = [
        f"# Retrieval eval report: {pack_id}",
        "",
        f"- hit@1: {hit1:.1f}%",
        f"- hit@3: {hit3:.1f}%",
        f"- MRR: {mrr:.3f}",
        f"- cases: {len(rows)}",
        "",
        "| case | expected | retrieved | hit@3 |",
        "|---|---|---|---|",
    ]
    for row in rows:
        status = "PASS" if row["hit@3"] else "FAIL"
        lines.append(f"| {row['id']} | {row['expected']} | {row['retrieved']} | {status} |")
    return "\n".join(lines) + "\n"


async def _run_retrieval(args: argparse.Namespace, pack: LoadedPack, settings: Settings) -> int:
    knowledge_store = _build_knowledge_store(settings)
    if knowledge_store is None:
        print("retrieval suite needs DATABASE_URL set (backend/.env)", file=sys.stderr)
        return 1
    embeddings = build_embeddings(settings)

    cases = _load_jsonl(pack.pack_dir / "evals" / _SUITE_FILENAMES["retrieval"])
    cases = cases[args.offset : args.offset + args.limit if args.limit else None]

    hit1, hit3, mrr, rows = await run_retrieval_suite(cases, args.pack, embeddings, knowledge_store)

    backend_dir = Path(__file__).resolve().parents[2]
    reports_dir = backend_dir / "evals" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = reports_dir / f"{timestamp}-retrieval.md"
    report_path.write_text(
        _render_retrieval_report(args.pack, hit1, hit3, mrr, rows), encoding="utf-8"
    )

    if hasattr(knowledge_store, "close"):
        await knowledge_store.close()

    print(f"hit@1={hit1:.1f}% hit@3={hit3:.1f}% mrr={mrr:.3f} cases={len(cases)}")
    print(f"report: {report_path}")
    return 0 if hit3 >= 90.0 else 1


async def _main_async(args: argparse.Namespace) -> int:
    settings = get_settings()
    backend_dir = Path(__file__).resolve().parents[2]
    packs_root = (backend_dir / settings.domain_packs_dir).resolve()
    pack = load_pack(args.pack, packs_root)

    if args.suite == "retrieval":
        return await _run_retrieval(args, pack, settings)

    embeddings = build_embeddings(settings)
    knowledge_store = _build_knowledge_store(settings)
    registry = ToolRegistry(pack, embeddings=embeddings, knowledge_store=knowledge_store)

    default_spec = (
        "fallback"
        if settings.llm_fallback_chain
        else f"{settings.llm_provider}:{settings.llm_model}"
    )
    llm_spec = args.llm or default_spec
    llm, mode = _resolve_llm(llm_spec, settings)

    all_cases = _load_jsonl(pack.pack_dir / "evals" / _SUITE_FILENAMES[args.suite])
    cases = all_cases[args.offset : args.offset + args.limit if args.limit else None]

    pack_tool_names = frozenset(spec.name for spec in registry.tool_specs())

    reports_dir = backend_dir / "evals" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = reports_dir / f"{timestamp}-{args.suite}.md"

    outcomes: list[CaseOutcome] = []
    for _ in range(args.repeat):
        for case in cases:
            turns = await run_case(
                case,
                pack,
                registry,
                llm,
                embeddings=embeddings,
                knowledge_store=knowledge_store,
                auto_rag_min_sim=settings.auto_rag_min_sim,
            )
            outcomes.append(evaluate_case(case, turns, pack_tool_names))
            # Write after every case so a killed/interrupted run still leaves usable partial
            # results instead of nothing (this suite can take 10-20+ minutes on a throttled
            # free-tier API key).
            accuracy = tool_selection_accuracy(outcomes)
            report = _render_report(args.pack, args.suite, llm_spec, mode, outcomes, accuracy)
            report_path.write_text(report, encoding="utf-8")
            print(f"[{len(outcomes)}/{len(cases) * args.repeat}] {case['id']} done", flush=True)

    if knowledge_store is not None and hasattr(knowledge_store, "close"):
        await knowledge_store.close()

    accuracy = tool_selection_accuracy(outcomes)
    print(f"mode={mode} suite={args.suite} cases={len(cases)} repeat={args.repeat}")
    print(f"tool_selection_accuracy={accuracy:.1f}%")
    print(f"report: {report_path}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the voice assistant eval suite")
    parser.add_argument("--pack", required=True)
    parser.add_argument("--suite", choices=["text", "redteam", "retrieval"], default="text")
    parser.add_argument(
        "--llm",
        default=None,
        help="fake | gemini:<model> | openai_compat:<model> | fallback[:<chain>]",
    )
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--offset", type=int, default=0, help="skip the first N cases")
    parser.add_argument("--limit", type=int, default=0, help="run at most N cases (0 = all)")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    # Show which fallback-chain provider answered each LLM call, so failures can be
    # attributed to a model rather than guessed at.
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s provider=%(provider)s"))
    fallback_logger = logging.getLogger("voice_core.adapters.fallback.llm")
    fallback_logger.addHandler(handler)
    fallback_logger.setLevel(logging.INFO)
    sys.exit(asyncio.run(_main_async(args)))


if __name__ == "__main__":
    main()
