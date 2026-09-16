# STATUS

Update after every milestone. Keep it short and factual.

## Current milestone
M0 — done

## What works
- Repo layout under `backend/` per CLAUDE.md: `app/`, `voice_core/{ports,adapters,agent,tools,kb,
  speech,transport,packs,evals,observability,i18n}`, `tests/`.
- `voice_core/config.py` — pydantic-settings covering every var in `.env.example`.
- `voice_core/ports/` — `LLMProvider`, `STTProvider`, `TTSProvider`, `EmbeddingProvider`,
  `KnowledgeStore`, `AuthVerifier`, `HostToolHandler` as typed `Protocol`s matching SPEC §4.
- `voice_core/adapters/fakes/` — scriptable fake for each port above, no network.
- `tests/test_boundaries.py` — grep gate: fails if domain words leak into `voice_core/` or
  `flutter_voice/lib/`.
- `tests/test_ports.py`, `tests/test_config.py` — fakes satisfy their protocols; settings load.
- `app/main.py` — thin FastAPI shell with `/healthz`; boots and responds `{"status":"ok"}`.

## Stubbed / mocked
- `voice_core/ports/store.py` (`ConversationStore`) is an intentionally empty Protocol — its
  method contract is designed in M1 (session/history) and M3 (confirmation gate), not at M0.
- `agent/`, `tools/`, `kb/`, `speech/`, `transport/`, `packs/`, `evals/`, `observability/`, `i18n/`
  are empty packages (layout only); no logic yet.
- No real vendor adapters yet (sarvam/openai/gemini/anthropic/local_embed/supabase) — only fakes.

## Measured numbers
| Date | Suite | Provider config | Key metrics |
|---|---|---|---|

## Decisions log
| Date | Decision | Why | Evidence |
|---|---|---|---|
| 2026-09-16 | M0 scaffold rebuilt from scratch | Prior session's reported commit `ba95ae1` never existed in git history; `backend/` was absent from the working tree | `git log --all` showed only `202227e`, `1ffdd45` before this change |

## Known issues
- None yet — M1 (text brain) not started.
