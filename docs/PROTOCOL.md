# WebSocket Protocol v1

Endpoint: `wss://<host>/v1/voice` · Text frames = JSON · Binary frames = audio.
Every JSON message has `"type"` and optional `"id"` (client-generated UUID for correlation).
Pydantic models for every message live in `backend/voice_core/transport/protocol.py`; Dart
models in `flutter_voice/lib/src/protocol/`. Keep both in sync — a contract test loads the JSON
examples in this file and validates them against the pydantic models.

## Connection lifecycle

1. Client opens socket, sends `session.start` within 5 s (else close `4400`).
2. Server verifies token → `session.ready` (or close `4401`).
3. Turns repeat. Client sends `ping` every 20 s; server replies `pong`.
4. Either side sends `session.end` / closes.

Close codes: `4400` protocol error · `4401` auth failed/expired · `4403` no consent ·
`4429` rate limited · `4500` server error.

## Client → Server

```jsonc
{"type":"session.start","token":"<supabase access token>","language":"hi-IN",
 "input_mode":"push_to_talk","client":{"platform":"android","app_version":"0.1.0","pkg_version":"0.1.0"},
 "audio_in":{"encoding":"pcm_s16le","sample_rate":16000,"channels":1}}

{"type":"auth.refresh","token":"<new token>"}

{"type":"audio.start","utterance_id":"u-1"}
// … binary frames: raw PCM chunks for the current utterance (server caps total duration) …
{"type":"audio.end","utterance_id":"u-1"}
{"type":"audio.cancel","utterance_id":"u-1"}           // user slid off the button

{"type":"text.input","utterance_id":"u-2","text":"मेरी बोली कितनी आई?"}

{"type":"confirm.response","action_id":"pa-9","decision":"confirm"}   // or "cancel"
{"type":"interrupt","reason":"user_tap"}               // or "barge_in"
{"type":"language.set","language":"mr-IN"}
{"type":"client_action.result","action_id":"ca-3","status":"ok"}      // or "failed"
{"type":"ping"}
{"type":"session.end"}
```

## Server → Client

```jsonc
{"type":"session.ready","session_id":"s-1","conversation_id":"c-1","language":"hi-IN",
 "speaker":"<configured female voice>","limits":{"max_utterance_ms":30000},
 "audio_out":{"encoding":"wav","sample_rate":22050},"protocol_version":1}

{"type":"state","value":"thinking"}   // listening|transcribing|thinking|speaking|awaiting_confirmation|idle

{"type":"transcript.partial","utterance_id":"u-1","text":"मेरी बो"}        // M6 only
{"type":"transcript.final","utterance_id":"u-1","text":"मेरी बोली कितनी आई","language":"hi-IN","confidence":0.93}

{"type":"tool.activity","turn_id":"t-1","phase":"started","label":"बोलियाँ देख रही हूँ"}
{"type":"tool.activity","turn_id":"t-1","phase":"finished"}

{"type":"assistant.text.delta","turn_id":"t-1","text":"आपके 500 किलो प्याज़ पर "}
{"type":"assistant.text.final","turn_id":"t-1","text":"…full caption text…","language":"hi-IN"}

{"type":"audio.segment","turn_id":"t-1","seq":0,"encoding":"wav","sample_rate":22050,"byte_length":48210,"is_last":false}
// immediately followed by exactly one binary frame with that segment's bytes

{"type":"confirm.request","action_id":"pa-9","tool":"accept_bid",
 "summary":"500 किलो प्याज़ के लिए 27 रुपये किलो की बोली स्वीकार करूँ?",
 "expires_at":"2026-09-16T10:02:00Z","labels":{"confirm":"हाँ","cancel":"नहीं"}}

{"type":"action.result","action_id":"pa-9","status":"executed_ok","message":"बोली स्वीकार हो गई है।"}
// status: executed_ok | executed_error | cancelled | expired

{"type":"client_action","action_id":"ca-3","name":"navigate","params":{"route":"/listings/L-102"}}

{"type":"turn.end","turn_id":"t-1","interrupted":false,
 "latency_ms":{"stt":640,"retrieval":120,"llm_first_token":510,"tts_first_audio":380,"first_audio_total":1790}}

{"type":"error","code":"STT_EMPTY","message":"माफ़ कीजिए, मैं सुन नहीं पाई। फिर से बोलिए।","retryable":true}
{"type":"pong"}
```

Error codes: `AUTH_FAILED`, `CONSENT_REQUIRED`, `PROTOCOL_ERROR`, `UTTERANCE_TOO_LONG`,
`STT_EMPTY`, `STT_FAILED`, `LLM_TIMEOUT`, `LLM_FAILED`, `TOOL_FAILED`, `TTS_FAILED`,
`RATE_LIMITED`, `INTERNAL`. `message` is always localized and safe to show/speak.

## Rules

- Only one turn runs at a time per session. A new `audio.start` or `text.input` during a turn
  implies `interrupt`.
- `audio.segment` seq starts at 0 per turn and is strictly increasing; client plays in order and
  discards segments from turns older than the latest `turn_id`.
- `confirm.response` for an unknown/expired `action_id` → `action.result` with `expired`.
- `audio_out.encoding` / `audio.segment.encoding` is `wav` or `mp3` (depends on the server's TTS
  provider; added in v1 as an additive change). A segment with `byte_length: 0` and `is_last: true`
  only marks the end of the turn's audio.
- If TTS fails for a sentence, the server still sends `assistant.text.final` (captions) and an
  `error` with `TTS_FAILED`; the client shows the text.
- Version bump rules: additive fields = same version; renamed/removed fields = `protocol_version` + 1.

## REST text API (M1–M3, used before the WebSocket exists)

All calls send `Authorization: Bearer <user session JWT>`.

`POST /v1/chat` — `{"text", "language", "conversation_id"?, "history"?: [{"role":"user"|"assistant","content"}]}`.
Omit `conversation_id` on the first turn; the response returns one, and later turns must send it.
The conversation must belong to the caller (otherwise `404`). Response:
`{"conversation_id","reply","reply_language","tools_called","pending_action","pending_action_id",
"executed","executed_tool","confirmed_via","pending_status","prompt_hash"}`.
When `pending_action_id` is set, `reply` is the confirmation question; show a confirm card for it.
`pending_status` values: `pending` | `cancelled` | `expired` | `executed_ok` | `executed_error`.
Any `cancelled`/`expired`/`executed_*` status means: drop any confirm card you are showing.

`POST /v1/confirm` — the ✓/✗ buttons: `{"conversation_id","action_id","decision":"yes"|"no","language"}`.
Only the conversation's current open action can be resolved; a stale or unknown `action_id`
returns `executed: false` with a "nothing to confirm" reply. A voice "yes"/"no" sent as a normal
`/v1/chat` turn resolves the same action. Pending state is server-side only — clients never send
tool turns or arguments back (`history` accepts only user/assistant roles).
