"""WebSocket voice session (docs/PROTOCOL.md v1, push-to-talk M4).

One session per socket, one turn at a time. A turn is an asyncio.Task:
audio -> STT -> run_text_turn -> sentences -> normalize -> TTS -> ordered `audio.segment`s.
The reply is spoken only once the LLM has finished it (never streamed mid-generation), so text
that precedes a write-tool call can't be spoken as if it were a result (golden rule 5).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, WebSocket
from pydantic import BaseModel, ValidationError
from starlette.websockets import WebSocketDisconnect, WebSocketState

from voice_core.agent.confirmation import ConfirmationGate
from voice_core.agent.language import LanguageTracker
from voice_core.agent.loop import TurnResult, resolve_pending_action, run_text_turn
from voice_core.i18n import strings as i18n
from voice_core.observability.timing import TurnTimer
from voice_core.packs.loader import LoadedPack
from voice_core.ports.auth import AuthVerifier
from voice_core.ports.embeddings import EmbeddingProvider
from voice_core.ports.errors import ProviderError
from voice_core.ports.host import HostToolHandler
from voice_core.ports.knowledge import KnowledgeStore
from voice_core.ports.llm import LLMProvider
from voice_core.ports.store import ConversationStore
from voice_core.ports.stt import STTProvider
from voice_core.ports.tts import TTSProvider
from voice_core.ports.types import ChatMessage, Principal, ToolContext
from voice_core.speech.normalize import normalize_for_speech
from voice_core.speech.segmenter import split_sentences
from voice_core.tools.registry import ToolRegistry
from voice_core.transport import protocol as p

logger = logging.getLogger(__name__)
router = APIRouter()

SESSION_START_TIMEOUT_S = 5.0
# If no answer is ready this long after thinking starts, play the pack's short filler
# ("one moment..."), pre-synthesized per session so it costs no TTS time (SPEC §6 step 5).
FILLER_AFTER_S = 0.8
HISTORY_MESSAGES = 16  # SPEC §5: last 8 turns verbatim
TTS_CONCURRENCY = 2
PCM_BYTES_PER_MS = 32  # 16 kHz * 2 bytes / 1000
MAX_AUDIO_FRAME_BYTES = 64 * 1024  # also run uvicorn with --ws-max-size 65536


@dataclass(frozen=True)
class VoiceDeps:
    pack: LoadedPack
    registry: ToolRegistry
    handler: HostToolHandler
    llm: LLMProvider
    store: ConversationStore
    auth_verifier: AuthVerifier
    stt: STTProvider
    tts: TTSProvider
    speaker: str
    pace: float
    max_utterance_ms: int
    audio_out_encoding: p.AudioEncoding = "wav"
    audio_out_sample_rate: int = 22_050
    embeddings: EmbeddingProvider | None = None
    knowledge_store: KnowledgeStore | None = None
    auto_rag_min_sim: float = 0.45


@router.websocket("/voice")
async def voice(websocket: WebSocket) -> None:
    await websocket.accept()
    await VoiceSession(websocket, websocket.app.state.voice_deps).run()


class VoiceSession:
    def __init__(self, ws: WebSocket, deps: VoiceDeps) -> None:
        self._ws = ws
        self._d = deps
        self._send_lock = asyncio.Lock()
        self._principal: Principal | None = None
        self._token: str | None = None  # caller's JWT, for forward_user_jwt tool handlers
        self._conversation_id = ""
        self._tracker: LanguageTracker | None = None
        self._history: list[ChatMessage] = []
        self._turn: asyncio.Task[None] | None = None
        self._current: _Turn | None = None
        self._utterance_id: str | None = None
        self._audio = bytearray()
        self._audio_too_long = False
        self._open_action_id: str | None = None
        self._resolved_actions: set[str] = set()  # ids already answered with action.result
        self._filler_audio: dict[str, bytes] = {}  # language -> pre-synthesized filler
        self._background: set[asyncio.Task[None]] = set()

    @property
    def language(self) -> str:
        return self._tracker.session_language if self._tracker else self._d.pack.default_language

    # ------------------------------------------------------------------ lifecycle

    async def run(self) -> None:
        try:
            if not await self._start():
                return
            while True:
                message = await self._ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    if len(message["bytes"]) > MAX_AUDIO_FRAME_BYTES:
                        await self._ws.close(code=p.CLOSE_PROTOCOL_ERROR)
                        break
                    self._on_audio(message["bytes"])
                elif message.get("text") is not None and not await self._on_text(message["text"]):
                    break
        except WebSocketDisconnect:
            pass
        finally:
            await self._interrupt()
            for task in list(self._background):
                task.cancel()
            if self._ws.client_state == WebSocketState.CONNECTED:
                with contextlib.suppress(Exception):
                    await self._ws.close()

    async def _start(self) -> bool:
        try:
            raw = await asyncio.wait_for(self._ws.receive_text(), SESSION_START_TIMEOUT_S)
            start = p.CLIENT_MESSAGE.validate_json(raw)
        except (TimeoutError, ValidationError, KeyError):
            await self._ws.close(code=p.CLOSE_PROTOCOL_ERROR)
            return False
        if not isinstance(start, p.SessionStart):
            await self._ws.close(code=p.CLOSE_PROTOCOL_ERROR)
            return False
        if not await self._authenticate(start.token) or self._principal is None:
            return False

        pack = self._d.pack
        saved = await self._d.store.get_preferred_language(self._principal.user_ref)
        requested = start.language if start.language in pack.languages else None
        language = next(
            (lang for lang in (saved, requested) if lang in pack.languages), pack.default_language
        )
        self._tracker = LanguageTracker(language, pack.languages)
        self._prepare_filler(language)
        self._conversation_id = await self._d.store.create_conversation(
            self._principal.user_ref, pack.id, "app", language
        )
        await self._send(
            p.SessionReady(
                session_id=str(uuid.uuid4()),
                conversation_id=self._conversation_id,
                language=language,
                speaker=self._d.speaker,
                limits={"max_utterance_ms": self._d.max_utterance_ms},
                audio_out=p.AudioOut(
                    encoding=self._d.audio_out_encoding, sample_rate=self._d.audio_out_sample_rate
                ),
            )
        )
        return True

    async def _authenticate(self, token: str) -> bool:
        try:
            principal = await self._d.auth_verifier.verify(token)
        except Exception:
            await self._ws.close(code=p.CLOSE_AUTH_FAILED)
            return False
        if self._principal is not None and principal.user_ref != self._principal.user_ref:
            await self._ws.close(code=p.CLOSE_AUTH_FAILED)  # refresh can't switch users
            return False
        self._principal, self._token = principal, token
        if self._token_expired():
            await self._ws.close(code=p.CLOSE_AUTH_FAILED)
            return False
        return True

    def _token_expired(self) -> bool:
        # The token is verified at session.start/auth.refresh; its `exp` is re-checked before
        # every turn so an expired login can't keep acting (close 4401, PROTOCOL.md).
        exp = self._principal.claims.get("exp") if self._principal else None
        return isinstance(exp, int | float) and exp <= time.time()

    # ------------------------------------------------------------------ inbound

    def _on_audio(self, chunk: bytes) -> None:
        if self._utterance_id is None:
            return
        if len(self._audio) + len(chunk) > self._d.max_utterance_ms * PCM_BYTES_PER_MS:
            self._audio_too_long = True
            return
        self._audio.extend(chunk)

    async def _on_text(self, raw: str) -> bool:
        """Handle one JSON frame; False means the session should end."""
        try:
            msg = p.CLIENT_MESSAGE.validate_json(raw)
        except ValidationError:
            await self._error("PROTOCOL_ERROR", i18n.INTERNAL_ERROR, retryable=False)
            return True

        match msg:
            case p.AudioStart():
                await self._interrupt()
                self._utterance_id, self._audio, self._audio_too_long = (
                    msg.utterance_id,
                    bytearray(),
                    False,
                )
                await self._send(p.State(value="listening"))
            case p.AudioEnd() if msg.utterance_id == self._utterance_id:
                audio, too_long = bytes(self._audio), self._audio_too_long
                self._utterance_id, self._audio = None, bytearray()
                if too_long:
                    await self._error("UTTERANCE_TOO_LONG", i18n.UTTERANCE_TOO_LONG, retryable=True)
                else:
                    return await self._begin_turn(utterance_id=msg.utterance_id, audio=audio)
            case p.AudioCancel():
                self._utterance_id, self._audio = None, bytearray()
                await self._send(p.State(value="idle"))
            case p.TextInput():
                self._utterance_id, self._audio = None, bytearray()
                return await self._begin_turn(utterance_id=msg.utterance_id, text=msg.text)
            case p.ConfirmResponse():
                self._utterance_id, self._audio = None, bytearray()
                current = self._current
                running = (
                    current is not None
                    and current.confirming_action_id == msg.action_id
                    and self._turn is not None
                    and not self._turn.done()
                )
                if running or msg.action_id in self._resolved_actions:
                    return True  # double tap: that card was/is being handled, result already sent
                return await self._begin_turn(confirm=msg)
            case p.Interrupt():
                await self._interrupt()
                await self._send(p.State(value="idle"))
            case p.LanguageSet():
                await self._set_language(msg.language)
            case p.AuthRefresh():
                return await self._authenticate(msg.token)
            case p.Ping():
                await self._send(p.Pong())
            case p.SessionEnd():
                return False
            case _:
                pass  # client_action.result, stray audio.end: nothing to do in M4
        return True

    async def _set_language(self, language: str) -> None:
        if self._tracker is None or self._principal is None:
            return
        if language not in self._d.pack.languages:
            await self._error("PROTOCOL_ERROR", i18n.INTERNAL_ERROR, retryable=False)
            return
        self._tracker.set(language)
        self._prepare_filler(language)
        await self._d.store.set_preferred_language(self._principal.user_ref, language)

    def _prepare_filler(self, language: str) -> None:
        """Synthesize the filler for this language in the background (best-effort)."""
        text = self._d.pack.fillers.get(language)
        if not text or language in self._filler_audio:
            return

        async def synthesize() -> None:
            try:
                spoken = normalize_for_speech(text, language, units=self._d.pack.speech_units)
                segment = await self._d.tts.synthesize(
                    spoken, language, self._d.speaker, self._d.pace
                )
                self._filler_audio[language] = segment.data
            except Exception:
                logger.warning("filler_prepare_failed", exc_info=True)

        task = asyncio.create_task(synthesize())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    # ------------------------------------------------------------------ turns

    async def _begin_turn(
        self,
        *,
        utterance_id: str | None = None,
        audio: bytes | None = None,
        text: str | None = None,
        confirm: p.ConfirmResponse | None = None,
    ) -> bool:
        """Start a turn after ending the previous one (only one turn at a time). False means the
        session must end (expired token)."""
        await self._interrupt()
        if self._token_expired():
            await self._ws.close(code=p.CLOSE_AUTH_FAILED)
            return False
        turn = _Turn(self, turn_id=f"t-{uuid.uuid4().hex[:12]}", utterance_id=utterance_id)
        self._current = turn
        self._turn = asyncio.create_task(turn.run(audio=audio, text=text, confirm=confirm))
        return True

    async def _interrupt(self) -> None:
        """Stop the running turn. A turn that may execute a confirmed write is never cancelled:
        its audio is muted and we wait for it, so the write's real result is still reported."""
        task, current = self._turn, self._current
        self._turn = self._current = None
        if task is None or task.done():
            return
        if current is not None and current.protected:
            current.muted = True
        else:
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    # ------------------------------------------------------------------ outbound

    async def _send(self, message: BaseModel, audio: bytes | None = None) -> None:
        """Send one JSON frame (and, for audio.segment, its binary frame) atomically."""
        if self._ws.client_state != WebSocketState.CONNECTED:
            return
        async with self._send_lock:
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                await self._ws.send_text(message.model_dump_json(exclude_none=True))
                if audio is not None:
                    await self._ws.send_bytes(audio)

    async def _error(self, code: p.ErrorCode, text: dict[str, str], *, retryable: bool) -> None:
        await self._send(
            p.Error(code=code, message=i18n.get(text, self.language), retryable=retryable)
        )


class _Turn:
    def __init__(self, session: VoiceSession, *, turn_id: str, utterance_id: str | None) -> None:
        self.s = session
        self.turn_id = turn_id
        self.utterance_id = utterance_id or turn_id
        self.timer = TurnTimer()
        self.seq = 0
        self.spoken: list[str] = []
        self.filler: asyncio.Task[None] | None = None
        self.filler_played = False
        self.answer_audio_sent = False
        self.stt_confidence: float | None = None
        self.reply_text: str | None = None
        self.reply_language: str | None = None
        # Set while this turn may execute a stored write; see VoiceSession._interrupt.
        self.protected = False
        self.muted = False
        self.confirming_action_id: str | None = None
        self.proposed_action_id: str | None = None
        self.confirm_sent = False

    @property
    def d(self) -> VoiceDeps:
        return self.s._d

    async def run(
        self, *, audio: bytes | None, text: str | None, confirm: p.ConfirmResponse | None
    ) -> None:
        interrupted = False
        try:
            if confirm is not None:
                result = await self._confirm(confirm)
                user_text: str | None = None
            else:
                user_text = await self._user_text(audio, text)
                if user_text is None:
                    return
                result = await self._think(user_text, input_mode="voice" if audio else "text")
            if result is not None:
                await self._deliver(result, user_text)
        except asyncio.CancelledError:
            interrupted = True
            raise
        except Exception:
            logger.exception("voice_turn_failed")
            await self.s._error("INTERNAL", i18n.INTERNAL_ERROR, retryable=True)
        finally:
            if self.filler is not None:
                self.filler.cancel()
            await self._finish(interrupted)

    async def _user_text(self, audio: bytes | None, text: str | None) -> str | None:
        if audio is None:
            return text
        await self.s._send(p.State(value="transcribing"))
        try:
            with self.timer.span("stt"):
                transcript = await self.d.stt.transcribe(audio, "pcm_s16le_16k", self.s.language)
        except ProviderError:
            logger.warning("stt_failed", exc_info=True)
            await self.s._error("STT_FAILED", i18n.STT_FAILED, retryable=True)
            return None
        if not transcript.text:
            await self.s._error("STT_EMPTY", i18n.STT_EMPTY, retryable=True)
            return None

        detected = transcript.language_code or None
        await self.s._send(
            p.TranscriptFinal(
                utterance_id=self.utterance_id,
                text=transcript.text,
                language=detected or self.s.language,
                confidence=transcript.language_confidence,
            )
        )
        if self.s._tracker is not None:
            self.s._tracker.observe(transcript.text, detected, transcript.language_confidence)
        self.stt_confidence = transcript.language_confidence
        return transcript.text

    def _ctx(self) -> ToolContext:
        principal = self.s._principal
        if principal is None:
            raise RuntimeError("turn without an authenticated principal")
        return ToolContext(
            user_ref=principal.user_ref, language=self.s.language, auth_token=self.s._token
        )

    async def _think(self, user_text: str, *, input_mode: Literal["voice", "text"]) -> TurnResult:
        # With an action awaiting confirmation, this utterance may be the "yes" that executes it.
        self.protected = self.s._open_action_id is not None
        try:
            await self.d.store.add_message(
                self.s._conversation_id,
                turn_id=self.turn_id,
                role="user",
                content=user_text,
                language=self.s.language,
                input_mode=input_mode,
                stt_confidence=self.stt_confidence,
            )
        except Exception:
            logger.warning("transcript_persist_failed", exc_info=True)  # best-effort
        await self.s._send(p.State(value="thinking"))
        if not self.filler_played and self.filler is None:
            self.filler = asyncio.create_task(self._filler_after_delay())
        language_before = self.s.language
        with self.timer.span("llm"):
            result = await run_text_turn(
                pack=self.d.pack,
                registry=self.d.registry,
                handler=self.d.handler,
                llm=self.d.llm,
                store=self.d.store,
                conversation_id=self.s._conversation_id,
                ctx=self._ctx(),
                language=language_before,
                history=list(self.s._history),
                user_text=user_text,
                embeddings=self.d.embeddings,
                knowledge_store=self.d.knowledge_store,
                auto_rag_min_sim=self.d.auto_rag_min_sim,
                on_tool=self._on_tool,
            )
        if result.pending_status == "pending" and result.pending_action_id:
            self.proposed_action_id = result.pending_action_id
        if result.reply_language != language_before:
            # The LLM called set_preferred_language: an explicit request, so persist it.
            await self.s._set_language(result.reply_language)
        self.s._history = [
            *self.s._history,
            ChatMessage(role="user", content=user_text),
            ChatMessage(role="assistant", content=result.reply_text),
        ][-HISTORY_MESSAGES:]
        return result

    async def _confirm(self, msg: p.ConfirmResponse) -> TurnResult | None:
        action, _ = await ConfirmationGate(self.d.store).current(self.s._conversation_id)
        if action is not None and action.id == msg.action_id and action.status == "executing":
            return None  # already running; that turn reports the result
        if action is None or action.id != msg.action_id or action.status != "pending":
            await self.s._send(p.ActionResult(action_id=msg.action_id, status="expired"))
            return None
        self.protected = True
        self.confirming_action_id = action.id
        return await resolve_pending_action(
            registry=self.d.registry,
            handler=self.d.handler,
            store=self.d.store,
            ctx=self._ctx(),
            action=action,
            decision="yes" if msg.decision == "confirm" else "no",
            via="button",
            language=self.s.language,
        )

    async def _on_tool(self, name: str, phase: Literal["started", "finished"]) -> None:
        label = None
        if self.d.registry.has_pack_tool(name):
            label = self.d.registry.get(name).display_hint.get(self.s.language)
        await self.s._send(p.ToolActivity(turn_id=self.turn_id, phase=phase, label=label))

    async def _filler_after_delay(self) -> None:
        await asyncio.sleep(FILLER_AFTER_S)
        language = self.s.language
        text = self.d.pack.fillers.get(language)
        if not text or self.muted:
            return
        self.filler_played = True
        cached = self.s._filler_audio.get(language)
        if cached is None:
            await self._speak([text], final=False)  # not ready yet: synthesize now (slower)
            return
        await self.s._send(p.State(value="speaking"))
        await self._send_segment(cached, is_last=False)
        self.spoken.append(text)

    async def _deliver(self, result: TurnResult, user_text: str | None) -> None:
        language = result.reply_language
        self.reply_text = result.reply_text
        self.reply_language = language
        if result.pending_action_id and result.pending_status == "pending":
            await self._send_confirm_request(result)
        elif result.pending_status in ("executed_ok", "executed_error", "cancelled", "expired"):
            action_id = result.pending_action_id or self.s._open_action_id
            if action_id:
                self.s._resolved_actions.add(action_id)
                await self.s._send(
                    p.ActionResult(
                        action_id=action_id,
                        status=result.pending_status,
                        message=result.reply_text or None,
                    )
                )
            self.s._open_action_id = None

        await self.s._send(
            p.AssistantTextFinal(turn_id=self.turn_id, text=result.reply_text, language=language)
        )
        if self.filler is not None and not self.filler.done():
            if self.filler_played:
                with contextlib.suppress(asyncio.CancelledError):
                    await self.filler
            else:
                self.filler.cancel()
        if result.reply_text:
            await self._speak(split_sentences(result.reply_text), language=language, final=True)

    async def _send_confirm_request(self, result: TurnResult) -> None:
        action, _ = await ConfirmationGate(self.d.store).current(self.s._conversation_id)
        if action is None or action.id != result.pending_action_id:
            return
        previous = self.s._open_action_id
        if previous is not None and previous != action.id:
            # The new proposal replaced the old one (the store cancelled it): drop its card.
            await self.s._send(p.ActionResult(action_id=previous, status="cancelled"))
        self.s._open_action_id = action.id
        self.confirm_sent = True
        language = result.reply_language
        await self.s._send(
            p.ConfirmRequest(
                action_id=action.id,
                tool=action.tool_name,
                summary=action.summary,
                expires_at=action.expires_at.isoformat(),
                labels={
                    "confirm": i18n.get(i18n.CONFIRM_LABEL_YES, language),
                    "cancel": i18n.get(i18n.CONFIRM_LABEL_NO, language),
                },
            )
        )

    async def _send_segment(self, data: bytes, *, is_last: bool) -> None:
        await self.s._send(
            p.AudioSegmentHeader(
                turn_id=self.turn_id,
                seq=self.seq,
                encoding=self.d.audio_out_encoding,
                sample_rate=self.d.audio_out_sample_rate,
                byte_length=len(data),
                is_last=is_last,
            ),
            audio=data,
        )
        self.seq += 1
        self.timer.mark("first_audio_total")

    async def _speak(
        self, sentences: list[str], *, final: bool, language: str | None = None
    ) -> None:
        """TTS each sentence (bounded concurrency), send segments strictly in order. Only the
        final reply's audio ends with `is_last` (never the filler), and it always does, even if
        the last sentence's TTS fails (an empty terminal segment is sent then)."""
        if self.muted:
            return
        language = language or self.s.language
        today = datetime.now(ZoneInfo(self.d.pack.timezone)).date()
        spoken = [
            s
            for s in (
                normalize_for_speech(t, language, today=today, units=self.d.pack.speech_units)
                for t in sentences
            )
            if s
        ]
        if not spoken:
            return
        await self.s._send(p.State(value="speaking"))
        gate = asyncio.Semaphore(TTS_CONCURRENCY)

        async def synth(text: str) -> bytes:
            async with gate:
                segment = await self.d.tts.synthesize(text, language, self.d.speaker, self.d.pace)
            return segment.data

        tasks = [asyncio.create_task(synth(t)) for t in spoken]
        tts_failed = False
        sent_last = False
        try:
            for index, (text, task) in enumerate(zip(spoken, tasks, strict=True)):
                if self.muted:
                    break
                first = final and not self.answer_audio_sent
                try:
                    if first:
                        with self.timer.span("tts_first_audio"):
                            data = await task
                    else:
                        data = await task
                except ProviderError:
                    logger.warning("tts_failed", exc_info=True)
                    if not tts_failed:
                        tts_failed = True
                        await self.s._error("TTS_FAILED", i18n.TTS_FAILED, retryable=False)
                    continue
                await self.s._send(
                    p.AudioSegmentHeader(
                        turn_id=self.turn_id,
                        seq=self.seq,
                        encoding=self.d.audio_out_encoding,
                        sample_rate=self.d.audio_out_sample_rate,
                        byte_length=len(data),
                        is_last=final and index == len(spoken) - 1,
                    ),
                    audio=data,
                )
                sent_last = final and index == len(spoken) - 1
                self.seq += 1
                self.spoken.append(text)
                self.timer.mark("first_audio_total")
                if final:
                    self.answer_audio_sent = True
                    self.timer.mark("first_answer_audio")  # the real answer, not the filler
        finally:
            for task in tasks:
                task.cancel()
        if final and self.seq > 0 and not sent_last and not self.muted:
            await self.s._send(
                p.AudioSegmentHeader(
                    turn_id=self.turn_id,
                    seq=self.seq,
                    encoding=self.d.audio_out_encoding,
                    sample_rate=self.d.audio_out_sample_rate,
                    byte_length=0,
                    is_last=True,
                ),
                audio=b"",
            )
            self.seq += 1

    async def _finish(self, interrupted: bool) -> None:
        if self.proposed_action_id and not self.confirm_sent:
            # Interrupted after proposing but before the question was sent: a later bare "yes"
            # must not be able to execute something the user never saw or heard.
            with contextlib.suppress(Exception):
                gate = ConfirmationGate(self.d.store)
                action, _ = await gate.current(self.s._conversation_id)
                if action is not None and action.id == self.proposed_action_id:
                    await gate.cancel(action)
        self.timer.mark("turn_total")
        latency = self.timer.as_dict()
        with contextlib.suppress(Exception):
            # Interrupted: record what the user actually heard (possibly nothing).
            content = " ".join(self.spoken) if interrupted else (self.reply_text or "")
            if interrupted or content:
                await self.d.store.add_message(
                    self.s._conversation_id,
                    turn_id=self.turn_id,
                    role="assistant",
                    content=content,
                    language=self.reply_language or self.s.language,
                    interrupted=interrupted or self.muted,
                    latency_ms=latency,
                )
        logger.info("voice_turn", extra={"turn_id": self.turn_id, "latency_ms": latency})
        await self.s._send(
            p.TurnEnd(
                turn_id=self.turn_id, interrupted=interrupted or self.muted, latency_ms=latency
            )
        )
        await self.s._send(
            p.State(value="awaiting_confirmation" if self.s._open_action_id else "idle")
        )
