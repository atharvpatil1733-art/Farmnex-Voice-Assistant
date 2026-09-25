from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from voice_core.agent.confirmation import ConfirmationGate
from voice_core.agent.loop import TurnResult, resolve_pending_action, run_text_turn
from voice_core.i18n.strings import ACTION_IN_PROGRESS, NOTHING_TO_CONFIRM
from voice_core.i18n.strings import get as i18n_get
from voice_core.packs.loader import LoadedPack
from voice_core.ports.auth import AuthVerifier
from voice_core.ports.store import ConversationStore
from voice_core.ports.types import ChatMessage, Principal, ToolContext

router = APIRouter()
_bearer = HTTPBearer(auto_error=True)

MAX_TEXT_CHARS = 2000
MAX_HISTORY_TURNS = 16


class ChatTurn(BaseModel):
    # Pending actions live server-side; clients can no longer inject tool turns.
    role: Literal["user", "assistant"]
    content: str = Field(max_length=MAX_TEXT_CHARS)


class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    language: str
    conversation_id: str | None = None
    history: list[ChatTurn] = Field(default_factory=list, max_length=MAX_HISTORY_TURNS)


class ConfirmRequest(BaseModel):
    conversation_id: str
    action_id: str
    decision: Literal["yes", "no"]
    language: str


class ChatResponse(BaseModel):
    conversation_id: str
    reply: str
    reply_language: str
    tools_called: list[str]
    pending_action: str | None
    pending_action_id: str | None
    executed: bool
    executed_tool: str | None
    confirmed_via: Literal["voice", "button"] | None
    pending_status: str | None
    prompt_hash: str


@dataclass(frozen=True)
class Caller:
    principal: Principal
    token: str = field(repr=False)  # the caller's own JWT, for forward_user_jwt handlers


async def get_caller(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),  # noqa: B008
) -> Caller:
    verifier: AuthVerifier = request.app.state.auth_verifier
    try:
        principal = await verifier.verify(credentials.credentials)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="invalid or expired token") from exc
    return Caller(principal=principal, token=credentials.credentials)


async def _owned_conversation(
    store: ConversationStore, conversation_id: str, principal: Principal
) -> None:
    # Same 404 for "missing" and "someone else's", so ids can't be probed.
    if await store.get_conversation_owner(conversation_id) != principal.user_ref:
        raise HTTPException(status_code=404, detail="conversation not found")


def _require_supported_language(pack: LoadedPack, language: str) -> None:
    # The language picks the confirm template and the prompt's reply language, so an
    # unsupported one (e.g. a raw device locale) is refused before any turn runs.
    if language not in pack.languages:
        raise HTTPException(status_code=422, detail=f"unsupported language: {language[:16]!r}")


def _response(conversation_id: str, result: TurnResult) -> ChatResponse:
    return ChatResponse(
        conversation_id=conversation_id,
        reply=result.reply_text,
        reply_language=result.reply_language,
        tools_called=result.tools_called,
        pending_action=result.pending_action,
        pending_action_id=result.pending_action_id,
        executed=result.executed,
        executed_tool=result.executed_tool,
        confirmed_via=result.confirmed_via,
        pending_status=result.pending_status,
        prompt_hash=result.prompt_hash,
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    request: Request,
    caller: Caller = Depends(get_caller),  # noqa: B008
) -> ChatResponse:
    state = request.app.state
    pack: LoadedPack = state.pack
    store: ConversationStore = state.conversation_store
    _require_supported_language(pack, req.language)

    if req.conversation_id is None:
        conversation_id = await store.create_conversation(
            caller.principal.user_ref, pack.id, "text_api", req.language
        )
    else:
        conversation_id = req.conversation_id
        await _owned_conversation(store, conversation_id, caller.principal)

    result = await run_text_turn(
        pack=pack,
        registry=state.registry,
        handler=state.tool_handler,
        llm=state.llm,
        store=store,
        conversation_id=conversation_id,
        ctx=ToolContext(
            user_ref=caller.principal.user_ref, language=req.language, auth_token=caller.token
        ),
        language=req.language,
        history=[ChatMessage(role=t.role, content=t.content) for t in req.history],
        user_text=req.text,
        embeddings=state.embeddings,
        knowledge_store=state.knowledge_store,
        auto_rag_min_sim=state.auto_rag_min_sim,
    )
    return _response(conversation_id, result)


@router.post("/confirm", response_model=ChatResponse)
async def confirm(
    req: ConfirmRequest,
    request: Request,
    caller: Caller = Depends(get_caller),  # noqa: B008
) -> ChatResponse:
    """The ✓ / ✗ buttons on the confirmation card. Only the conversation's *current* open
    action can be resolved, and only with the args stored when it was proposed."""
    state = request.app.state
    store: ConversationStore = state.conversation_store
    _require_supported_language(state.pack, req.language)
    await _owned_conversation(store, req.conversation_id, caller.principal)

    action, _ = await ConfirmationGate(store).current(req.conversation_id)
    if action is None or action.id != req.action_id or action.status != "pending":
        in_progress = action is not None and action.id == req.action_id
        return ChatResponse(
            conversation_id=req.conversation_id,
            reply=i18n_get(ACTION_IN_PROGRESS if in_progress else NOTHING_TO_CONFIRM, req.language),
            reply_language=req.language,
            tools_called=[],
            pending_action=None,
            pending_action_id=None,
            executed=False,
            executed_tool=None,
            confirmed_via=None,
            pending_status=None,
            prompt_hash="",
        )

    result = await resolve_pending_action(
        registry=state.registry,
        handler=state.tool_handler,
        store=store,
        ctx=ToolContext(
            user_ref=caller.principal.user_ref, language=req.language, auth_token=caller.token
        ),
        action=action,
        decision=req.decision,
        via="button",
        language=req.language,
    )
    return _response(req.conversation_id, result)
