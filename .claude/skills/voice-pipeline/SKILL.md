---
name: voice-pipeline
description: Building and tuning the speech side of the voice assistant — speech-to-text (Sarvam saaras), text-to-speech (Sarvam bulbul, female speaker), the WebSocket audio protocol, sentence segmentation for streaming TTS, spoken-text normalization for Hindi/Marathi/English numbers, units, currency and dates, language detection and switching, interruptions/barge-in, and latency measurement. Use this whenever working on voice_core/speech, STT/TTS adapters, transport/ws.py, audio formats, "the voice sounds wrong / reads symbols / is slow / switches language randomly" issues, or moving from push-to-talk to realtime streaming.
---

# Voice pipeline

Voice quality is mostly decided by three unglamorous things: what text reaches TTS, how fast the
first sentence gets out, and not switching language by accident. Protocol details are in
`docs/PROTOCOL.md`; budgets in `docs/SPEC.md` §6.

## Modes

- **M4 push-to-talk (default for farmers)**: client streams PCM while the button is held →
  `audio.end` → batch STT. Robust in noisy outdoor environments and on weak networks; no VAD
  tuning needed. Barge-in = pressing the mic button (client stops playback, sends `interrupt`).
- **M6 streaming**: realtime STT over WebSocket with server-side VAD, partial transcripts,
  voice barge-in. Only after M4 metrics are solid. Check the provider's current realtime docs
  (model id, frame size, VAD parameters) before implementing.

## STT adapter notes

- Verify the current Sarvam model ids and parameters in their docs before coding (at the time
  this kit was written: `saaras:v3` for REST with a `mode` parameter such as `transcribe`, and a
  realtime variant for streaming). Use `mode=transcribe` so the transcript stays in the spoken
  language; do not translate to English before the LLM.
- Send 16 kHz mono PCM16 wrapped as WAV for REST. Reject clips < 300 ms (accidental taps).
- Map `language_code` + confidence into `Transcript`. `null` language or empty text → `STT_EMPTY`.
- Pack-specific hotwords/vocabulary (crop names, app name) go in `pack.yaml → stt_context` if the
  provider supports prompting; otherwise fix common misrecognitions in a pack-level
  post-correction map (`"बीड" → "बिड"` only if evals prove it helps).

## TTS adapter notes

- Model `bulbul:v3` at the time of writing; speaker comes from `pack.yaml`/user prefs. Audition
  female voices with real app sentences in all three languages before choosing; store the choice
  in STATUS.md.
- Respect per-request character limits; the segmenter already keeps requests sentence-sized.
- Pass the session language as the target language code so the provider's text normalization
  matches.
- Cache synthesized audio for fixed phrases (fillers, errors, confirmations' fixed parts) keyed
  by `(text, language, speaker, pace, model)`.

## Segmenter (speech/segmenter.py)

- Input: async stream of text deltas. Output: sentences.
- Boundaries: `।`, `॥`, `.`, `?`, `!`, newline — but not inside numbers (`2.5`), abbreviations
  (`Dr.`, `Rs.`), or times (`10.30`).
- Force-flush at ~180 characters on the last comma/space. Flush the remainder at stream end.
- Emit the first sentence immediately — this is the biggest latency win.

## Normalizer (speech/normalize.py + speech/locale/{hi,mr,en}.py)

Deterministic, table-driven, fully unit-tested per language:
- Currency: `₹27`, `Rs 27`, `27/-` → "27 रुपये" / "27 रुपये" / "27 rupees".
- Units: `kg`, `/kg`, `qtl`, `quintal`, `ton` → "किलो", "प्रति किलो", "क्विंटल"…
- Dates `2026-09-18` → "18 सितंबर" (hi) / "18 सप्टेंबर" (mr) / "18 September" (en). Relative words
  ("आज", "कल", "उद्या") when the date is today/tomorrow.
- Times `10:30` → "सुबह साढ़े दस बजे" / "सकाळी साडेदहा वाजता" / "10:30 in the morning".
- Remove markdown (`**`, `#`, `-` bullets), emoji, URLs, parentheses content that is an id.
- Keep digits as digits unless tests show the TTS reads them badly in that language.
Add a test for every mispronunciation bug before fixing it.

## Language switching (agent/language.py)

Implement exactly SPEC §9: explicit request → switch + persist; auto-detected different language
only with confidence ≥ 0.8, ≥ 3 words, 2 consecutive turns → session-only switch. Code-mixed
Hinglish/Manglish utterances must not flip the session. Test with code-mixed fixtures.

## Interruptions

- Server: each turn is an `asyncio.Task`; `interrupt` cancels it; TTS tasks for later sequence
  numbers are cancelled; persist partial assistant text with `interrupted=true`.
- Never cancel an executing write tool; let it finish and report via `action.result`.
- Client: stop playback immediately on mic press, then send `interrupt`; drop audio segments
  whose `turn_id` isn't current.

## Latency measurement

`observability/timing.py` records `stt`, `retrieval`, `llm_first_token`, `first_sentence_ready`,
`tts_first_audio`, `first_audio_total`, `turn_total`. Log per turn, store in
`messages.latency_ms`, and have `evals.run --suite latency` print median/p90. When something is
slow, find the stage from these numbers before changing anything.

## Tests to keep green

Segmenter edge cases, normalizer tables per language, language-switch rules, interrupt
cancellation (fake TTS with delays), protocol contract test against PROTOCOL.md examples,
end-to-end fake pipeline: audio bytes → FakeSTT → FakeLLM → FakeTTS → ordered segments.
