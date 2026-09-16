# Voice Assistant Kit

A spec-first starter for a **portable Hindi / Marathi / English voice assistant** that you build with
Claude Code and later drop into a real app. There is no application code here yet — Claude Code
writes it, guided by these files.

## What's inside

| Path | Purpose |
|---|---|
| `CLAUDE.md` | Always-loaded instructions for Claude Code: rules, stack, commands, milestones |
| `docs/SPEC.md` | Full architecture and behavior spec (source of truth) |
| `docs/PROTOCOL.md` | WebSocket message contract between Flutter and backend |
| `docs/PORTING.md` | How to move the assistant into another app / DB / frontend |
| `docs/STATUS.md` | Progress, measured numbers, decisions (Claude Code keeps it updated) |
| `.claude/skills/*/SKILL.md` | 7 focused skills Claude Code loads when relevant |
| `.claude/agents/voice-safety-reviewer.md` | Subagent that reviews risky diffs |
| `.claude/settings.json` | Safe command allowlist; blocks reading `.env` |
| `supabase/migrations/0001_voice_core.sql` | Tested schema `voice` with pgvector + `match_chunks` |
| `domain_packs/farm_marketplace/` | Example domain: persona, 8 tools, workflow, fixtures, sample knowledge, 37 eval cases |
| `.env.example` | All backend config keys |

## How to use it

1. Copy this folder into a new git repo and open a terminal there.
2. Create a Supabase project (choose the region closest to your users), enable the database, and
   note the connection string. Install the Supabase CLI and Flutter SDK.
3. Copy `.env.example` to `backend/.env` and fill in what you have (AI keys can wait until M1/M4 —
   fakes work first).
4. Start Claude Code in the folder and send the prompts below **one milestone at a time**. Review,
   test, commit after each.

### Prompt sequence for Claude Code

**M0**
> Read CLAUDE.md, docs/SPEC.md and docs/PROTOCOL.md. Implement milestone M0 only: scaffold
> backend/ with uv, the voice_core package layout, all ports as Protocols, fake adapters, config,
> the boundary test, and pytest/ruff/bandit passing. Show me the plan before writing files.

**M1**
> Implement M1: POST /v1/chat, domain pack loader, tool registry with JSON-schema validation, mock
> handlers from fixtures, agent loop with streaming tool calls, prompt assembly per SPEC §10, and
> the text eval runner. Use the fake LLM for unit tests; add one real LLM adapter I choose:
> <provider + model>. Run the golden suite and report numbers in STATUS.md.

**M2**
> Implement M2 using the knowledge-base-rag skill: apply the migration to my Supabase project,
> Supabase stores, embedding adapter, ingest CLI, auto-retrieval + search_knowledge tool, retrieval
> evals. Mark the sample knowledge docs active only after I review them.

**M3**
> Implement M3: confirmation gate state machine, idempotency, audit rows, confirmation lexicon
> precheck, http and graphql handlers. Add every test listed in the voice-agent-core skill. Then
> run the voice-safety-reviewer subagent and the redteam suite.

**M4**
> Implement M4 with the voice-pipeline skill: WebSocket transport per PROTOCOL.md, Sarvam STT and
> TTS adapters (check current Sarvam docs first), segmenter, normalizer tables for hi/mr/en,
> language switching, interrupts, latency timing. Give me a small Python CLI client that sends a
> WAV file so I can test without Flutter.

**M5**
> Implement M5 with the flutter-voice-client skill: the voice_assistant package and flutter_demo
> host app with Supabase login, consent screen, and the three demo flows. Verify package choices
> on pub.dev first and record them in STATUS.md.

**Porting later**
> Use the port-to-host-app skill. Our real backend is <REST/GraphQL at URL>. Switch these tools to
> live handlers: <list>. Keep contracts unchanged and run evals in live mode on staging.

## Key design choices (short)

- **Text brain first, voice second** — debug the agent without audio in the way.
- **LLM routes via tool calling** — no extra classification call per turn.
- **Writes need confirmation enforced in code**, with templated summaries and idempotency.
- **Push-to-talk first** — robust outdoors and on weak networks; streaming is an upgrade.
- **Own WebSocket protocol** — the Flutter client doesn't depend on any voice framework.
- **Everything app-specific is in a domain pack** — that's what makes the assistant portable.
