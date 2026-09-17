from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from voice_core.agent.prompt import build_prompt, tool_result_to_message
from voice_core.i18n.strings import CANCELLATION_ACK, LLM_FAILURE, TOOL_ROUND_CUTOFF
from voice_core.i18n.strings import get as i18n_get
from voice_core.packs.loader import LoadedPack
from voice_core.ports.embeddings import EmbeddingProvider
from voice_core.ports.host import HostToolHandler
from voice_core.ports.knowledge import KnowledgeStore
from voice_core.ports.llm import LLMProvider
from voice_core.ports.types import (
    ChatMessage,
    Chunk,
    Done,
    LLMError,
    TextDelta,
    ToolCall,
    ToolContext,
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
    text = unicodedata.normalize("NFC", text).strip().lower()
    text = re.sub(r"[^\w\s]", "", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _match_lexicon(text: str, extra: dict[str, list[str]]) -> Literal["yes", "no"] | None:
    normalized = _normalize(text)
    yes_words = {_normalize(w) for w in (_YES_WORDS | set(extra.get("yes", [])))}
    no_words = {_normalize(w) for w in (_NO_WORDS | set(extra.get("no", [])))}
    if normalized in yes_words:
        return "yes"
    if normalized in no_words:
        return "no"
    return None


@dataclass(frozen=True)
class PendingWrite:
    tool: str
    args: dict[str, Any]


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
    pending_status: Literal["pending", "cancelled", "executed_ok", "executed_error"] | None
    prompt_hash: str


def _empty_result(
    reply_text: str,
    language: str,
    prompt_hash: str,
    tools_called: list[str],
    tool_results: list[dict[str, Any]],
    knowledge_used: list[str],
) -> TurnResult:
    return TurnResult(
        reply_text=reply_text,
        reply_language=language,
        tools_called=tools_called,
        tool_results=tool_results,
        knowledge_used=knowledge_used,
        pending_action=None,
        pending_write_args=None,
        executed=False,
        executed_tool=None,
        confirmed_via=None,
        pending_status=None,
        prompt_hash=prompt_hash,
    )


async def run_text_turn(
    *,
    pack: LoadedPack,
    registry: ToolRegistry,
    handler: HostToolHandler,
    llm: LLMProvider,
    ctx: ToolContext,
    language: str,
    history: list[ChatMessage],
    user_text: str,
    pending_write: PendingWrite | None = None,
    embeddings: EmbeddingProvider | None = None,
    knowledge_store: KnowledgeStore | None = None,
    auto_rag_min_sim: float = 0.45,
    llm_temperature: float = 0.2,
    llm_max_tokens: int = 800,
    llm_timeout_s: float = 20.0,
) -> TurnResult:
    if pending_write is not None:
        decision = _match_lexicon(user_text, pack.confirmation_lexicon_extra)
        if decision == "no":
            return TurnResult(
                reply_text=i18n_get(CANCELLATION_ACK, language),
                reply_language=language,
                tools_called=[],
                tool_results=[],
                knowledge_used=[],
                pending_action=None,
                pending_write_args=None,
                executed=False,
                executed_tool=None,
                confirmed_via=None,
                pending_status="cancelled",
                prompt_hash="",
            )
        if decision == "yes":
            result = await registry.dispatch(pending_write.tool, pending_write.args, ctx, handler)
            pack_tool = registry.get(pending_write.tool)
            if result.status == "ok" and pack_tool.success_message:
                reply = i18n_get(pack_tool.success_message, language)
            elif result.status != "ok":
                reply = i18n_get(LLM_FAILURE, language)
            else:
                reply = ""
            return TurnResult(
                reply_text=reply,
                reply_language=language,
                tools_called=[pending_write.tool],
                tool_results=[
                    {"tool": pending_write.tool, "data": result.data, "args": pending_write.args}
                ],
                knowledge_used=[],
                pending_action=None,
                pending_write_args=None,
                executed=(result.status == "ok"),
                executed_tool=pending_write.tool,
                confirmed_via="voice",
                pending_status="executed_ok" if result.status == "ok" else "executed_error",
                prompt_hash="",
            )
        # Neither yes nor no: fall through to the LLM with the pending stub still in `history`.

    knowledge_chunks: list[Chunk] = []
    if embeddings is not None and knowledge_store is not None:
        from voice_core.kb.retriever import auto_retrieve

        knowledge_chunks = await auto_retrieve(
            query=user_text,
            pack_id=pack.id,
            language=language,
            embeddings=embeddings,
            store=knowledge_store,
            min_similarity=auto_rag_min_sim,
        )
    knowledge_used = [f"{c.doc_slug}" for c in knowledge_chunks]

    rendered = build_prompt(
        pack, language, history, user_text, datetime.now(tz=UTC), knowledge_chunks
    )
    messages = rendered.messages
    tools_called: list[str] = []
    tool_results: list[dict[str, Any]] = []
    current_language = language

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
            return _empty_result(
                i18n_get(LLM_FAILURE, current_language),
                current_language,
                rendered.prompt_hash,
                tools_called,
                tool_results,
                knowledge_used,
            )

        if not tool_calls:
            return _empty_result(
                text_buffer.strip(),
                current_language,
                rendered.prompt_hash,
                tools_called,
                tool_results,
                knowledge_used,
            )

        messages.append(ChatMessage(role="assistant", content=text_buffer))

        write_call = next((c for c in tool_calls if registry.is_write(c.name)), None)
        if write_call is not None:
            args = json.loads(write_call.args_json)
            pack_tool = registry.get(write_call.name)
            resolved_fields = await registry.resolve_confirm_fields(
                write_call.name, args, ctx, handler
            )
            if pack_tool.resolve_for_confirm:
                tools_called.append(pack_tool.resolve_for_confirm)
            tools_called.append(write_call.name)
            tool_results.append({"tool": write_call.name, "data": resolved_fields, "args": args})

            confirm_template = (pack_tool.confirm or {}).get(current_language, "")
            summary = render_confirm_template(confirm_template, resolved_fields)
            return TurnResult(
                reply_text=summary,
                reply_language=current_language,
                tools_called=tools_called,
                tool_results=tool_results,
                knowledge_used=knowledge_used,
                pending_action=write_call.name,
                pending_write_args=args,
                executed=False,
                executed_tool=None,
                confirmed_via=None,
                pending_status="pending",
                prompt_hash=rendered.prompt_hash,
            )

        for call in tool_calls:
            args = json.loads(call.args_json)
            tools_called.append(call.name)
            result = await registry.dispatch(call.name, args, ctx, handler)
            tool_results.append({"tool": call.name, "data": result.data, "args": args})

            if call.name == "set_preferred_language" and result.status == "ok":
                current_language = args["language"]

            if call.name == "end_conversation":
                return _empty_result(
                    text_buffer.strip(),
                    current_language,
                    rendered.prompt_hash,
                    tools_called,
                    tool_results,
                    knowledge_used,
                )

            messages.append(tool_result_to_message(call.name, result))

    return _empty_result(
        i18n_get(TOOL_ROUND_CUTOFF, current_language),
        current_language,
        rendered.prompt_hash,
        tools_called,
        tool_results,
        knowledge_used,
    )
