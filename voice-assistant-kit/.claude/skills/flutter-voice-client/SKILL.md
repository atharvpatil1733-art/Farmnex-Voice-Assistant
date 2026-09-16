---
name: flutter-voice-client
description: Building the reusable Flutter package `voice_assistant` (flutter_voice/) and the demo host app (flutter_demo/) for the voice assistant — WebSocket client for docs/PROTOCOL.md, microphone recording, ordered audio playback, assistant state machine, captions, confirmation card, language chips, client actions, permissions, reconnect, and accessible UI for low-literacy users. Use this whenever writing or changing Dart/Flutter code for the assistant, embedding the assistant into a Flutter app, fixing mic/playback/permission issues on Android or iOS, or designing the assistant's UI.
---

# Flutter voice client

Two projects with different jobs:
- `flutter_voice/` — a **package**. Knows the protocol, audio, and generic UI. Knows nothing
  about Supabase, routing, state-management libraries, or the domain.
- `flutter_demo/` — a **host app**. Logs in with `supabase_flutter`, passes the token, registers
  client actions, shows how to embed. Throwaway-quality is fine here; the package is the product.

## Package structure

```
flutter_voice/lib/
├── voice_assistant.dart            public exports only
└── src/
    ├── config.dart                 VoiceAssistantConfig, InputMode
    ├── controller.dart             VoiceAssistantController (ChangeNotifier + streams)
    ├── state.dart                  sealed AssistantState classes
    ├── protocol/                   message models + JSON (mirror of PROTOCOL.md)
    ├── transport/ws_client.dart    connect, session.start, ping, reconnect/backoff
    ├── audio/recorder.dart         PCM16 16 kHz mono stream → binary frames
    ├── audio/player_queue.dart     ordered segment playback, stop(), turn filtering
    ├── actions/registry.dart       client action allowlist + dispatch
    ├── l10n/strings.dart           package UI strings hi/mr/en
    └── ui/                         mic_button.dart, assistant_sheet.dart, confirm_card.dart,
                                    caption_list.dart, language_chips.dart, offline_banner.dart
```

Public API is specified in `docs/SPEC.md` §13 — keep it small; everything else stays in `src/`.

## Before adding dependencies

Check pub.dev for current versions, platform support (Android, iOS; web optional), maintenance,
and license. You need roughly: a WebSocket client, a recorder that can stream raw PCM16, and a
player that can play short WAV/compressed segments sequentially. Prefer packages with official or
widely used status; avoid thin wrappers with few users. Record the choice and reason in STATUS.md.

## State machine

```
idle ─connect→ connecting ─session.ready→ idle
idle ─press mic→ listening ─release→ uploading ─state:thinking→ thinking
thinking ─audio.segment→ speaking ─turn.end & queue empty→ idle
any ─confirm.request→ awaitingConfirmation(action) ─✓/✗ or voice→ thinking|idle
any ─socket lost→ offline ─reconnected→ idle
speaking ─press mic→ (stop playback, send interrupt) listening
```

Implement as a sealed class hierarchy; the UI renders purely from state. Unit-test transitions
with a fake transport and fake recorder/player.

## Audio details

- Recording: 16 kHz, mono, PCM16 little-endian; send ~100–200 ms chunks as binary frames between
  `audio.start` and `audio.end`. Cap at `limits.max_utterance_ms` from `session.ready`
  (auto-release with a haptic).
- Minimum hold 300 ms; shorter = show "hold the button while speaking" hint, send `audio.cancel`.
- Playback: queue keyed by `(turn_id, seq)`; play strictly in order; discard stale turns; `stop()`
  must be immediate (< 100 ms) because barge-in depends on it.
- Request audio focus / ducking appropriately; stop playback when the app goes to background.

## UX rules (low-literacy, outdoor, one-handed)

- One large mic button (≥ 72 dp), bottom center, reachable by thumb.
- Every state has color + icon + animation + haptic, not color alone; captions always shown.
- Confirmation: big ✓ and ✗ buttons with icons and localized words, shown with the spoken
  question; buttons disabled after tap to prevent double submit.
- Language chips "हिंदी · मराठी · English" always visible in the sheet; sends `language.set`.
- Text input fallback behind a keyboard icon.
- Works at 1.3× text scale and on 360×640 screens; TalkBack labels on all controls.
- Error and offline messages are short, localized, and say what to do next.

## Host integration (flutter_demo)

1. `supabase_flutter` login (phone OTP or email for the demo).
2. `tokenProvider: () async => Supabase.instance.client.auth.currentSession?.accessToken`.
3. Register client actions matching the pack's allowlist.
4. Consent screen before first voice use; store consent via a backend endpoint.
5. Android `RECORD_AUDIO` + `INTERNET`; iOS `NSMicrophoneUsageDescription`. Handle "denied
   forever" by opening app settings.

Never put AI API keys, the Supabase service-role key, or the database URL in any Flutter code or
`--dart-define`. The only secret the app handles is the user's own session token.

## Checks

`flutter analyze` clean, `flutter test` green (controller state tests, protocol JSON tests using
the examples from PROTOCOL.md, player queue ordering tests). Manual test on a real low-end
Android device on mobile data before calling M5 done.
