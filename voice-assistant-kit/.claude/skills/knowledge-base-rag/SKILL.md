---
name: knowledge-base-rag
description: Writing, versioning, ingesting, and retrieving the voice assistant's curated knowledge (RAG) with pgvector in the Supabase `voice` schema — markdown knowledge docs with front matter, chunking for spoken answers, embeddings, the match_chunks function, cross-lingual Hindi/Marathi/English retrieval, and retrieval evals. Use this whenever adding or editing app help content, FAQs, policies or how-to guides, changing the embedding model, debugging "the assistant doesn't know X" or wrong-answer problems, or touching voice_core/kb.
---

# Knowledge base (RAG)

The knowledge base answers "how does the app work" questions. It never holds per-user data
(that comes from tools) and never ingests raw web content (quality and injection risk).

## Writing a knowledge doc

Path: `domain_packs/<pack>/knowledge/<lang-folder>/<slug>.md`

```markdown
---
slug: pre-bidding-basics
title: Pre-bidding basics
domain: pre_bidding
language: en-IN
version: 2
status: active            # draft | active | retired
effective_from: 2026-09-01
audience: farmer
---

## What is pre-bidding?
Short, plain answer in 2–4 sentences…

## How long does pre-bidding stay open?
…
```

Writing for voice retrieval:
- One question per `##` section, phrased the way users ask. Answer first, details after.
- Each section must make sense alone (chunks are retrieved individually).
- Use the words farmers actually say, including common English loanwords.
- Put exact numbers/rules here only if the backend enforces the same numbers; otherwise link to a
  tool that returns the live value.
- Mark unverified content `status: draft` — drafts are never retrieved.

## Versioning

- Editing meaning → bump `version`. Typos → same version (hash changes, re-ingest replaces chunks
  of that version).
- Ingest publishes the new version as `active` and retires the old one in one transaction; the
  unique partial index guarantees one active version per slug+language.
- Future rules: set `effective_from` in the future; retrieval ignores them until then.

## Ingestion (`voice_core/kb/ingest.py`)

Pipeline: load → validate front matter (fail loudly) → hash → skip if unchanged → chunk by `##`
(split long sections at ~350 tokens on sentence boundaries; prefix each chunk with
`title › heading`) → embed with `kind="document"` → write doc + chunks → retire previous.
CLI: `uv run python -m voice_core.kb.ingest --pack <pack> [--dry-run] [--slug <slug>]`.
Ingest must be idempotent: running it twice changes nothing.

## Embeddings

- Must be multilingual and cross-lingual (a Marathi question should find an English doc).
  Default candidate: `BAAI/bge-m3` (1024-d). Validate on `retrieval.jsonl` before committing.
- Some models need query/document prefixes — handle that inside the adapter via `kind`.
- Dimension lives in the migration and `EMBEDDING_DIM`; startup check compares them.
- Changing model = new migration altering the column + full re-ingest + retrieval eval.

## Retrieval

- Auto-retrieval runs in parallel each turn; inject top 3 only if best similarity ≥
  `AUTO_RAG_MIN_SIM`. The LLM can also call `search_knowledge` explicitly.
- `voice.match_chunks(pack, embedding, k, min_sim, domains, prefer_language)` — prefers same
  language with a +0.03 bonus rather than filtering.
- Exact terms (scheme names, numbers) retrieve poorly with vectors; for those, add hybrid search
  on the `fts` column and merge with reciprocal rank fusion.
- Format injected chunks as `<knowledge source="slug@v2">…</knowledge>`.

## Debugging "it didn't know"

1. Run the question through `uv run python -m voice_core.kb.search "<question>" --pack <pack>`
   (build this tiny CLI) and look at similarities.
2. Doc missing or draft? → write/activate it.
3. Doc present but low score? → rephrase the section heading as the user's question; add
   Hindi/Marathi version; check chunk isn't mixing topics.
4. Retrieved but answer wrong? → prompt/persona issue, not retrieval; add a golden eval.

## Evals

`evals/retrieval.jsonl` lines: `{"id":"r-001","question":"प्री बिडिंग कितने दिन चलती है?","language":"hi-IN","expected_slugs":["pre-bidding-basics"]}`
Track hit@1, hit@3, MRR per language. Target hit@3 ≥ 90%. Add a case for every "it didn't know"
bug before fixing it.
