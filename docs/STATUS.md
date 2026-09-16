# STATUS

Update after every milestone. Keep it short and factual.

## Current milestone
M1 — text brain implemented, gate blocked (see Known issues)

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
  method contract is designed in M3 (confirmation/session persistence), not needed for the
  in-memory M1 text turn loop.
- `voice_core/kb/`, `voice_core/speech/`, `voice_core/observability/` are still empty packages
  (M2/M4 work).
- `search_knowledge` core tool is a stub returning `{"chunks": []}` until M2 wires a real
  `KnowledgeStore`; `evals/run.py --suite retrieval` refuses to run until then.
- Only vendor adapter implemented is `voice_core/adapters/gemini/llm.py` (real network, used for
  the eval "gate" mode); everything else still uses fakes.

## What works (M1)
- `voice_core/agent/loop.py` — `run_text_turn`: prompt → LLM tool-calling loop (max 4 rounds),
  write-tool confirmation gate (`PendingWrite` → summary → explicit yes/no lexicon match →
  execute stored args only), language-switch handling, `end_conversation`.
- `voice_core/agent/prompt.py` — system prompt assembly (core rules + persona + session facts),
  `<tool_result>`/`<knowledge>` delimiting so tool output is never treated as instructions.
- `voice_core/tools/registry.py` + `schema.py` — pack tool loading from `tools.yaml`,
  `assert_no_identity_fields` boundary check, JSON-schema arg validation, `result_fields`
  trimming, `resolve_for_confirm` correlation for confirm-template fields.
- `voice_core/tools/handlers/mock.py` — fixture-based `HostToolHandler` with per-case
  `fixture_overrides` for eval error-path testing.
- `voice_core/transport/rest.py` + `app/main.py` — `POST /v1/chat`.
- `voice_core/evals/{run.py,metrics.py}` — golden-suite runner against `fake` or `gemini:<model>`,
  structural + content checks, markdown report under `backend/evals/reports/`.
- `domain_packs/farm_marketplace/` — persona, 7 tools (5 read, 2 write), 1 workflow, 20 golden
  cases (hi-IN/mr-IN/en-IN), fixtures, redteam + retrieval eval files (retrieval needs M2).

## Measured numbers
| Date | Suite | Provider config | Key metrics |
|---|---|---|---|
| 2026-09-16 | text (golden, 20 cases) | gemini:gemini-2.5-flash | 31.6% tool-selection accuracy — **not trustworthy**, see known issues |

## Decisions log
| Date | Decision | Why | Evidence |
|---|---|---|---|
| 2026-09-16 | M0 scaffold rebuilt from scratch | Prior session's reported commit `ba95ae1` never existed in git history; `backend/` was absent from the working tree | `git log --all` showed only `202227e`, `1ffdd45` before this change |
| 2026-09-16 | Fixed `get_my_listings` `result_fields` in `domain_packs/farm_marketplace/tools.yaml` from a flat field list (`listing_ref, crop, quantity_kg, status, bidding_ends_at, harvest_date`) to `[listings]` | The fixture (`fixtures/listings.json`) nests every listing under a top-level `listings` array; `ToolRegistry.dispatch`'s trimming (`voice_core/tools/registry.py`) only keeps *top-level* keys matching `result_fields`, so the old list matched nothing and the LLM always received `{}` for this tool — starving it of data for every case that needed "which listing" (g-013, g-014, g-016, g-020, and indirectly cases that fall back to it via `resolve_for_confirm`) | Confirmed by reading `fixtures/bids.json`/`orders.json`/`pickups.json` (all flat, `result_fields` match) vs `listings.json` (nested); added `tests/test_tool_registry.py::test_dispatch_get_my_listings_returns_nested_listings` as a regression test |
| 2026-09-16 | Gated `FakeAuthVerifier` in `app/main.py` behind `settings.app_env == "dev"`; non-dev startup now raises `RuntimeError` instead of silently wiring the static `dev-token` bypass | `voice-safety-reviewer` subagent flagged this as a BLOCKER-in-waiting: the code wired `FakeAuthVerifier` unconditionally while a comment claimed it was dev-only — not exploitable today (no real host/writes exist pre-M3) but exactly the kind of thing that survives into M3/M4 by inertia if not fixed now | `backend/tests/test_main.py` covers both branches |

## Known issues
- **The 31.6% gate run is not a reliable signal.** Gemini free-tier quota for `gemini-2.5-flash`
  is 20 requests/day per project; a 20-case golden suite easily needs 40-80+ requests (multi-turn
  cases, multi-round tool loops), so the quota was exhausted partway through and most later turns
  silently got the `LLM_FAILURE` fallback reply with zero tool calls — which reads as a
  tool-selection failure in the report but is actually a rate-limit artifact. Confirmed live:
  replaying a single case now returns `LLMError(code='rate_limited', ...
  GenerateRequestsPerDayPerProjectPerModel-FreeTier ... limit: 20)`.
- Before re-running the gate for a real number: either wait for the daily quota to reset, use a
  paid/higher-quota key, or point `--llm` at a cheaper/different model — and budget requests
  (20 cases × up to ~4 rounds × up to 4 turns for multi-turn cases can exceed 100 calls).
- `.env` currently sets `llm_model=gemini-3.8-flash` but the last gate run was invoked with
  `--llm gemini:gemini-2.5-flash` explicitly; confirm which model the gate should target before
  the next run.
- One real bug found and fixed this session (see decisions log): `get_my_listings` was returning
  empty data to the LLM. Re-run the gate to see how much of the 31.6% it accounts for — likely
  a meaningful chunk, but the quota exhaustion means the current report can't isolate its effect.
- **`PendingAction` trust model is a known M1 stub, not to carry past M3 unchanged.**
  `transport/rest.py` has no server-side pending-write store; the client must echo back the
  `pending_action_turn` JSON verbatim as the last history item on the confirm turn, and
  `_extract_pending_write` trusts whatever `tool`/`args` it contains — no id, no signature, no
  idempotency key, no audit row. `run_text_turn` does correctly execute the *stored* args rather
  than re-asking the LLM (the important half of golden rule 4), but a tampered echo (bad client,
  no-TLS MITM) could change args between the confirm and "yes" turns undetected. M3 replaces this
  with a server-side `PendingAction` row keyed by an opaque id + idempotency key, per the milestone
  plan in CLAUDE.md — flagged here so it's not missed.
- `voice_core/tools/registry.py::resolve_confirm_fields` feeds resolved fields straight into
  `render_confirm_template` (spoken aloud) via plain `str.format`. Fine today since `mock` handlers
  only read static fixture files, but once `resolve_for_confirm` points at a real `http`/`graphql`
  handler (M3), that becomes untrusted host data flowing into a spoken confirmation — should get
  the same `<tool_result>`-style treatment other tool output already has, at that point.
- `voice_core/evals/metrics.py::evaluate_case` doesn't implement four `expect` keys used by
  `domain_packs/farm_marketplace/evals/redteam.jsonl`: `no_tool_arg_keys` (rt-002),
  `no_other_user_data` (rt-002), `no_system_prompt_leak` (rt-006), `no_guarantee` (rt-009).
  Unrecognized keys are silently ignored rather than failing loudly, so running
  `--suite redteam` today reports those four cases as passing without actually checking the
  properties in their names. `no_tool_arg_keys` is partially covered structurally already
  (`assert_no_identity_fields` + `additionalProperties: false` mean no pack tool schema can even
  accept an identity-shaped arg name), but the other three have no coverage at all. Needs design
  before implementing (e.g. `no_system_prompt_leak` needs a defensible heuristic, `no_other_user_data`
  needs pack-level knowledge of what's off-limits) — left as a known gap rather than guessed at.
- Rate limiting: `Settings.rate_limit_turns_per_min` / `rate_limit_turns_per_day` are declared but
  nothing reads them yet, and `ChatRequest` has no size caps on `text`/`history`. Per CLAUDE.md's
  milestone plan this is explicitly M7 ("Hardening — rate limits...") — intentionally not pulled
  forward into M1. Separately, quota safety for the *live LLM provider* (not in-app rate limiting)
  should be handled by moving off the Gemini free tier before any real demo/judging traffic — see
  the quota entries above.
