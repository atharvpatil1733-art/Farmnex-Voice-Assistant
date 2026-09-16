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
| 2026-09-16 | Fixed `get_my_listings` `result_fields` in `domain_packs/farm_marketplace/tools.yaml` from a flat field list (`listing_ref, crop, quantity_kg, status, bidding_ends_at, harvest_date`) to `[listings]` | The fixture (`fixtures/listings.json`) nests every listing under a top-level `listings` array; `ToolRegistry.dispatch`'s trimming (`voice_core/tools/registry.py`) only keeps *top-level* keys matching `result_fields`, so the old list matched nothing and the LLM always received `{}` for this tool — starving it of data for every case that needed "which listing" (g-013, g-014, g-016, g-020, and indirectly cases that fall back to it via `resolve_for_confirm`) | Confirmed by reading `fixtures/bids.json`/`orders.json`/`pickups.json` (all flat, `result_fields` match) vs `listings.json` (nested) |

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
