from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from voice_core.agent.loop import PendingWrite, run_text_turn
from voice_core.packs.loader import LoadedPack
from voice_core.ports.auth import AuthVerifier
from voice_core.ports.host import HostToolHandler
from voice_core.ports.llm import LLMProvider
from voice_core.ports.types import ChatMessage, Principal, ToolContext
from voice_core.tools.registry import ToolRegistry

router = APIRouter()
_bearer = HTTPBearer(auto_error=True)


class ChatTurn(BaseModel):
    role: Literal["user", "assistant", "tool"]
    content: str


class ChatRequest(BaseModel):
    text: str
    language: str
    history: list[ChatTurn] = []


class ChatResponse(BaseModel):
    reply: str
    reply_language: str
    tools_called: list[str]
    pending_action: str | None
    executed: bool
    executed_tool: str | None
    confirmed_via: Literal["voice", "button"] | None
    pending_status: str | None
    prompt_hash: str
    pending_action_turn: ChatTurn | None


def _extract_pending_write(history: list[ChatTurn]) -> PendingWrite | None:
    if not history or history[-1].role != "tool":
        return None
    try:
        payload = json.loads(history[-1].content)
    except json.JSONDecodeError:
        return None
    if payload.get("type") != "pending_write":
        return None
    return PendingWrite(tool=payload["tool"], args=payload["args"])


def _to_chat_messages(history: list[ChatTurn]) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    for turn in history:
        if turn.role != "tool":
            messages.append(ChatMessage(role=turn.role, content=turn.content))
            continue
        try:
            payload = json.loads(turn.content)
        except json.JSONDecodeError:
            continue
        body = json.dumps({"status": "awaiting_confirmation", "args": payload.get("args")})
        content = f'<tool_result tool="{payload.get("tool")}">{body}</tool_result>'
        messages.append(ChatMessage(role="tool", content=content))
    return messages


async def get_current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),  # noqa: B008
) -> Principal:
    verifier: AuthVerifier = request.app.state.auth_verifier
    try:
        return await verifier.verify(credentials.credentials)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="invalid or expired token") from exc


@router.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),  # noqa: B008
) -> ChatResponse:
    pack: LoadedPack = request.app.state.pack
    registry: ToolRegistry = request.app.state.registry
    handler: HostToolHandler = request.app.state.tool_handler
    llm: LLMProvider = request.app.state.llm

    ctx = ToolContext(user_ref=principal.user_ref, language=req.language)
    pending_write = _extract_pending_write(req.history)
    history_messages = _to_chat_messages(req.history)

    result = await run_text_turn(
        pack=pack,
        registry=registry,
        handler=handler,
        llm=llm,
        ctx=ctx,
        language=req.language,
        history=history_messages,
        user_text=req.text,
        pending_write=pending_write,
        embeddings=request.app.state.embeddings,
        knowledge_store=request.app.state.knowledge_store,
        auto_rag_min_sim=request.app.state.auto_rag_min_sim,
    )

    pending_turn = None
    if result.pending_action is not None:
        pending_turn = ChatTurn(
            role="tool",
            content=json.dumps(
                {
                    "type": "pending_write",
                    "tool": result.pending_action,
                    "args": result.pending_write_args,
                }
            ),
        )

    return ChatResponse(
        reply=result.reply_text,
        reply_language=result.reply_language,
        tools_called=result.tools_called,
        pending_action=result.pending_action,
        executed=result.executed,
        executed_tool=result.executed_tool,
        confirmed_via=result.confirmed_via,
        pending_status=result.pending_status,
        prompt_hash=result.prompt_hash,
        pending_action_turn=pending_turn,
    )
