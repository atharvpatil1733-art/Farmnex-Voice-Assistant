from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal

from voice_core.agent.confirmation import ConfirmationGate
from voice_core.agent.prompt import build_prompt, tool_result_to_message, wrap_tool_result
from voice_core.i18n.strings import (
    ACTION_ALREADY_DONE,
    ACTION_IN_PROGRESS,
    CANCELLATION_ACK,
    CONFIRM_UNRESOLVED,
    LLM_FAILURE,
    NOTHING_TO_CONFIRM,
    PENDING_EXPIRED,
    TOOL_ROUND_CUTOFF,
    WRITE_FAILED,
)
from voice_core.i18n.strings import get as i18n_get
from voice_core.packs.loader import LoadedPack
from voice_core.ports.embeddings import EmbeddingProvider
from voice_core.ports.host import HostToolHandler
from voice_core.ports.knowledge import KnowledgeStore
from voice_core.ports.llm import LLMProvider
from voice_core.ports.store import ConversationStore
from voice_core.ports.types import (
    ChatMessage,
    Chunk,
    Done,
    LLMError,
    PendingAction,
    TextDelta,
    ToolCall,
    ToolContext,
    ToolInvocation,
    ToolResult,
)
from voice_core.tools.registry import ToolRegistry, render_confirm_template

MAX_TOOL_ROUNDS = 4

_YES_WORDS = {
    "हाँ",
    "हां",
    "हा",
    "हो",
    "होय",
    "हो ना",
    "बरोबर",
    "ठीक है",
    "कर दो",
    "कर दीजिए",
    "चालेल",
    "yes",
    "ok",
    "okay",
    "confirm",
    "sure",
}
_NO_WORDS = {
    "नहीं",
    "नही",
    "नाही",
    "नको",
    "रहने दो",
    "मत करो",
    "cancel",
    "no",
    "don't",
    "stop",
}


def _normalize(text: str) -> str:
    # Strip punctuation/symbols by Unicode category rather than `[^\w\s]`: Python's `\w`
    # excludes combining marks, so the old form deleted every Devanagari vowel sign and
    # anusvara — collapsing "हाँ"/"हो"/"हूँ" all onto bare "ह" and letting unrelated words
    # match the yes/no lexicon (a write could execute without a real confirmation).
    text = unicodedata.normalize("NFC", text).strip().lower()
    text = "".join(ch for ch in text if not unicodedata.category(ch).startswith(("P", "S")))
    return re.sub(r"\s+", " ", text).strip()


def _match_lexicon(text: str, extra: dict[str, list[str]]) -> Literal["yes", "no"] | None:
    normalized = _normalize(text)
    if not normalized:
        return None
    # Drop entries that normalize to "" (e.g. an emoji-only pack word), so they can't match.
    yes_words = {_normalize(w) for w in (_YES_WORDS | set(extra.get("yes", [])))} - {""}
    no_words = {_normalize(w) for w in (_NO_WORDS | set(extra.get("no", [])))} - {""}
    if normalized in yes_words:
        return "yes"
    if normalized in no_words:
        return "no"
    return None


PendingOutcome = Literal["pending", "cancelled", "expired", "executed_ok", "executed_error"]
# Called around each read-tool call, e.g. to show "checking…" or play a filler (SPEC §6 step 5).
ToolActivityHook = Callable[[str, Literal["started", "finished"]], Awaitable[None]]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnResult:
    reply_text: str
    reply_language: str
    tools_called: list[str]
    tool_results: list[dict[str, Any]]
    knowledge_used: list[str]
    pending_action: str | None
    pending_write_args: dict[str, Any] | None
    executed: bool
    executed_tool: str | None
    confirmed_via: Literal["voice", "button"] | None
    pending_status: PendingOutcome | None
    prompt_hash: str
    pending_action_id: str | None = None


def _empty_result(
    reply_text: str,
    language: str,
    prompt_hash: str = "",
    tools_called: list[str] | None = None,
    tool_results: list[dict[str, Any]] | None = None,
    knowledge_used: list[str] | None = None,
    *,
    pending_status: PendingOutcome | None = None,
) -> TurnResult:
    return TurnResult(
        reply_text=reply_text,
        reply_language=language,
        tools_called=tools_called or [],
        tool_results=tool_results or [],
        knowledge_used=knowledge_used or [],
        pending_action=None,
        pending_write_args=None,
        executed=False,
        executed_tool=None,
        confirmed_via=None,
        pending_status=pending_status,
        prompt_hash=prompt_hash,
    )


def _parse_args(call: ToolCall) -> dict[str, Any] | None:
    try:
        args = json.loads(call.args_json or "{}")
    except json.JSONDecodeError:
        return None
    return args if isinstance(args, dict) else None


def _invalid(errors: list[str]) -> ToolResult:
    return ToolResult(status="invalid", error_code="SCHEMA_INVALID", data={"errors": errors})


async def _dispatch_audited(
    *,
    registry: ToolRegistry,
    handler: HostToolHandler,
    store: ConversationStore,
    ctx: ToolContext,
    conversation_id: str,
    name: str,
    args: dict[str, Any],
    pending_action_id: str | None = None,
) -> ToolResult:
    """Run a tool and write its audit row. A handler exception becomes an error result (the
    user hears a localized failure), never a crash and never a success."""
    started = time.perf_counter()
    try:
        result = await registry.dispatch(name, args, ctx, handler)
    except Exception:
        logger.warning("tool_dispatch_failed", extra={"tool": name}, exc_info=True)
        result = ToolResult(status="error", error_code="HANDLER_EXCEPTION")
    if registry.has_pack_tool(name):
        try:
            await store.record_invocation(
                ToolInvocation(
                    user_ref=ctx.user_ref,
                    tool_name=name,
                    kind=registry.get(name).tool_def.kind,
                    args=args,
                    status=result.status,
                    conversation_id=conversation_id,
                    pending_action_id=pending_action_id,
                    error_code=result.error_code,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            )
        except Exception:
            logger.error("audit_write_failed", extra={"tool": name}, exc_info=True)
    return result


def _pending_context(action: PendingAction) -> ChatMessage:
    """Tell the LLM what is awaiting confirmation, as untrusted-data-tagged tool output."""
    return wrap_tool_result(
        action.tool_name,
        {"status": "awaiting_confirmation", "args": action.args, "summary": action.summary},
    )


async def resolve_pending_action(
    *,
    registry: ToolRegistry,
    handler: HostToolHandler,
    store: ConversationStore,
    ctx: ToolContext,
    action: PendingAction,
    decision: Literal["yes", "no"],
    via: Literal["voice", "button"],
    language: str,
    clock: Callable[[], datetime] | None = None,
) -> TurnResult:
    """Apply an explicit yes/no to a stored action. Executes the *stored* args at most once."""
    gate = ConfirmationGate(store, clock=clock) if clock else ConfirmationGate(store)
    if decision == "no":
        cancelled = await gate.cancel(action)
        text = CANCELLATION_ACK if cancelled else NOTHING_TO_CONFIRM
        return _empty_result(
            i18n_get(text, language), language, pending_status="cancelled" if cancelled else None
        )

    started = await gate.begin(action, via)
    if started == "expired":
        return _empty_result(
            i18n_get(PENDING_EXPIRED, language), language, pending_status="expired"
        )
    if started == "not_pending":
        current, _ = await gate.current(action.conversation_id)
        in_progress = current is not None and current.id == action.id
        text = ACTION_IN_PROGRESS if in_progress else NOTHING_TO_CONFIRM
        return _empty_result(i18n_get(text, language), language)

    async def execute() -> ToolResult:
        result = await _dispatch_audited(
            registry=registry,
            handler=handler,
            store=store,
            ctx=replace(ctx, idempotency_key=action.idempotency_key),
            conversation_id=action.conversation_id,
            name=action.tool_name,
            args=action.args,
            pending_action_id=action.id,
        )
        await gate.finish(action, ok=result.status == "ok")
        return result

    # SPEC §6: an interrupt never cancels a write that is already executing. Shielding lets the
    # host call and its status/audit writes complete even if this turn is cancelled.
    result = await asyncio.shield(execute())
    ok = result.status == "ok"

    pack_tool = registry.get(action.tool_name)
    if not ok:
        reply = i18n_get(WRITE_FAILED, language)
    elif pack_tool.success_message:
        reply = i18n_get(pack_tool.success_message, language)
    else:
        reply = ""
    return TurnResult(
        reply_text=reply,
        reply_language=language,
        tools_called=[action.tool_name],
        tool_results=[{"tool": action.tool_name, "data": result.data, "args": action.args}],
        knowledge_used=[],
        pending_action=None,
        pending_write_args=None,
        executed=ok,
        executed_tool=action.tool_name,
        confirmed_via=via,
        pending_status="executed_ok" if ok else "executed_error",
        prompt_hash="",
        pending_action_id=action.id,
    )


async def run_text_turn(
    *,
    pack: LoadedPack,
    registry: ToolRegistry,
    handler: HostToolHandler,
    llm: LLMProvider,
    store: ConversationStore,
    conversation_id: str,
    ctx: ToolContext,
    language: str,
    history: list[ChatMessage],
    user_text: str,
    embeddings: EmbeddingProvider | None = None,
    knowledge_store: KnowledgeStore | None = None,
    auto_rag_min_sim: float = 0.45,
    llm_temperature: float = 0.2,
    llm_max_tokens: int = 800,
    llm_timeout_s: float = 20.0,
    clock: Callable[[], datetime] | None = None,
    on_tool: ToolActivityHook | None = None,
) -> TurnResult:
    gate = ConfirmationGate(store, clock=clock) if clock else ConfirmationGate(store)
    pending, just_expired = await gate.current(conversation_id)
    decision = _match_lexicon(user_text, pack.confirmation_lexicon_extra)

    if decision is not None:
        if pending is not None and pending.status == "pending":
            return await resolve_pending_action(
                registry=registry,
                handler=handler,
                store=store,
                ctx=ctx,
                action=pending,
                decision=decision,
                via="voice",
                language=language,
                clock=clock,
            )
        if pending is not None:  # executing
            return _empty_result(i18n_get(ACTION_IN_PROGRESS, language), language)
        if just_expired:
            return _empty_result(
                i18n_get(PENDING_EXPIRED, language), language, pending_status="expired"
            )

    open_pending = pending if pending is not None and pending.status == "pending" else None
    if open_pending is not None:
        # Not a clear yes/no: the LLM sees what is waiting and may re-propose with changes.
        history = [*history, _pending_context(open_pending)]

    result = await _run_llm_rounds(
        pack=pack,
        registry=registry,
        handler=handler,
        llm=llm,
        store=store,
        gate=gate,
        conversation_id=conversation_id,
        ctx=ctx,
        language=language,
        history=history,
        user_text=user_text,
        embeddings=embeddings,
        knowledge_store=knowledge_store,
        auto_rag_min_sim=auto_rag_min_sim,
        llm_temperature=llm_temperature,
        llm_max_tokens=llm_max_tokens,
        llm_timeout_s=llm_timeout_s,
        on_tool=on_tool,
    )

    # A pending action survives only while its confirmation question is the last thing the
    # assistant said. Any other outcome cancels it, so a later "yes" to an unrelated question
    # can never execute it, and clients drop any stale confirm card.
    if open_pending is not None and result.pending_action_id != open_pending.id:
        if await gate.cancel(open_pending) and result.pending_status is None:
            result = replace(result, pending_status="cancelled")
    return result


async def _run_llm_rounds(
    *,
    pack: LoadedPack,
    registry: ToolRegistry,
    handler: HostToolHandler,
    llm: LLMProvider,
    store: ConversationStore,
    gate: ConfirmationGate,
    conversation_id: str,
    ctx: ToolContext,
    language: str,
    history: list[ChatMessage],
    user_text: str,
    embeddings: EmbeddingProvider | None,
    knowledge_store: KnowledgeStore | None,
    auto_rag_min_sim: float,
    llm_temperature: float,
    llm_max_tokens: int,
    llm_timeout_s: float,
    on_tool: ToolActivityHook | None = None,
) -> TurnResult:
    knowledge_chunks: list[Chunk] = []
    if embeddings is not None and knowledge_store is not None:
        from voice_core.kb.retriever import auto_retrieve

        try:
            knowledge_chunks = await auto_retrieve(
                query=user_text,
                pack_id=pack.id,
                language=language,
                embeddings=embeddings,
                store=knowledge_store,
                min_similarity=auto_rag_min_sim,
            )
        except Exception:
            # Auto-retrieval is advisory (SPEC §6 step 4): the LLM can still call
            # search_knowledge explicitly, so a transient backend failure here should
            # degrade to "no extra knowledge this turn", not crash the whole turn.
            logger.warning(
                "auto_retrieve failed; continuing without knowledge chunks", exc_info=True
            )
    knowledge_used = [f"{c.doc_slug}" for c in knowledge_chunks]

    rendered = build_prompt(
        pack, language, history, user_text, datetime.now(tz=UTC), knowledge_chunks
    )
    messages = rendered.messages
    tools_called: list[str] = []
    tool_results: list[dict[str, Any]] = []
    current_language = language

    def done(reply: str, **kwargs: Any) -> TurnResult:
        return _empty_result(
            reply,
            current_language,
            rendered.prompt_hash,
            tools_called,
            tool_results,
            knowledge_used,
            **kwargs,
        )

    for _round in range(MAX_TOOL_ROUNDS):
        text_buffer = ""
        tool_calls: list[ToolCall] = []
        had_error = False

        async for event in llm.stream(
            messages,
            registry.tool_specs(),
            temperature=llm_temperature,
            max_tokens=llm_max_tokens,
            timeout_s=llm_timeout_s,
        ):
            match event:
                case TextDelta(text=text):
                    text_buffer += text
                case ToolCall():
                    tool_calls.append(event)
                case LLMError():
                    had_error = True
                case Done():
                    pass

        if had_error and not text_buffer and not tool_calls:
            return done(i18n_get(LLM_FAILURE, current_language))

        if not tool_calls:
            return done(text_buffer.strip())

        messages.append(ChatMessage(role="assistant", content=text_buffer))

        write_call = next((c for c in tool_calls if registry.is_write(c.name)), None)
        if write_call is not None:
            proposal = await _propose_write(
                registry=registry,
                handler=handler,
                gate=gate,
                conversation_id=conversation_id,
                ctx=ctx,
                call=write_call,
                language=current_language,
                tools_called=tools_called,
                tool_results=tool_results,
            )
            if isinstance(proposal, ChatMessage):
                # Invalid args: hand the errors back to the LLM for another round.
                messages.append(proposal)
                continue
            return replace(
                proposal, prompt_hash=rendered.prompt_hash, knowledge_used=knowledge_used
            )

        for call in tool_calls:
            parsed = _parse_args(call)
            tools_called.append(call.name)
            if parsed is None:
                result = _invalid(["arguments are not a JSON object"])
                args: dict[str, Any] = {}
            else:
                args = parsed
                if on_tool is not None:
                    await on_tool(call.name, "started")
                try:
                    result = await _dispatch_audited(
                        registry=registry,
                        handler=handler,
                        store=store,
                        ctx=ctx,
                        conversation_id=conversation_id,
                        name=call.name,
                        args=args,
                    )
                finally:
                    if on_tool is not None:
                        await on_tool(call.name, "finished")
            tool_results.append({"tool": call.name, "data": result.data, "args": args})

            if call.name == "set_preferred_language" and result.status == "ok":
                current_language = args["language"]

            if call.name == "end_conversation":
                return done(text_buffer.strip())

            messages.append(tool_result_to_message(call.name, result))

    return done(i18n_get(TOOL_ROUND_CUTOFF, current_language))


async def _propose_write(
    *,
    registry: ToolRegistry,
    handler: HostToolHandler,
    gate: ConfirmationGate,
    conversation_id: str,
    ctx: ToolContext,
    call: ToolCall,
    language: str,
    tools_called: list[str],
    tool_results: list[dict[str, Any]],
) -> TurnResult | ChatMessage:
    """Turn a write tool call into a stored PendingAction plus its templated question.
    Returns a tool-result message instead when the args are invalid (for the LLM to fix)."""
    args = _parse_args(call)
    errors = (
        ["arguments are not a JSON object"] if args is None else registry.validate(call.name, args)
    )
    if args is None or errors:
        tool_results.append({"tool": call.name, "data": {"errors": errors}, "args": args or {}})
        return tool_result_to_message(call.name, _invalid(errors))

    pack_tool = registry.get(call.name)
    resolution = await registry.resolve_confirm_fields(call.name, args, ctx, handler)
    if pack_tool.resolve_for_confirm:
        tools_called.append(pack_tool.resolve_for_confirm)
    tools_called.append(call.name)
    stored_args = resolution.pinned_args
    tool_results.append({"tool": call.name, "data": resolution.fields, "args": args})

    def result(reply: str, **kwargs: Any) -> TurnResult:
        return _empty_result(reply, language, "", tools_called, tool_results, None, **kwargs)

    if resolution.ambiguous or registry.validate(call.name, stored_args):
        logger.warning("confirm_unresolvable_args", extra={"tool": call.name})
        return result(i18n_get(CONFIRM_UNRESOLVED, language))

    confirm_template = (pack_tool.confirm or {}).get(language, "")
    try:
        summary = render_confirm_template(confirm_template, resolution.fields)
        if not summary.strip():
            # No template for this language: the user would confirm something unheard.
            raise ValueError(f"no confirm template for language {language!r}")
    except ValueError as exc:
        # The args matched no record, so the confirmation can't name what would change.
        # Never propose a write the user can't verify — ask instead.
        logger.warning("confirm_unresolved", extra={"tool": call.name, "error": str(exc)})
        return result(i18n_get(CONFIRM_UNRESOLVED, language))

    action = await gate.propose(
        conversation_id=conversation_id,
        user_ref=ctx.user_ref,
        tool=call.name,
        args=stored_args,
        summary=summary,
        language=language,
    )
    if action.status == "executed_ok":
        return result(i18n_get(ACTION_ALREADY_DONE, language))
    if action.status == "executing":
        return result(i18n_get(ACTION_IN_PROGRESS, language))
    return replace(
        result(summary, pending_status="pending"),
        pending_action=call.name,
        pending_write_args=stored_args,
        pending_action_id=action.id,
    )
