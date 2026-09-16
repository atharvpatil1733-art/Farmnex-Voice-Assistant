---
name: port-to-host-app
description: Step-by-step procedure for moving the voice assistant kit into a real product — connecting tools to the real backend (REST/GraphQL), changing the assistant database or auth provider, embedding the Flutter package in another app, or creating a new domain pack for a different product. Use this whenever the user says integrate, plug in, move, port, reuse, connect to the real app/API/database, switch from mock data, or start a new app with the same assistant.
---

# Porting the assistant

Read `docs/PORTING.md` for the full checklist. This skill is the working procedure.

## 1. Inventory first (write it in chat before coding)

- Host backend type (REST / GraphQL / other), base URL per environment, auth model.
- Host user id format and how it maps to the assistant's `user_ref`.
- Host frontend (Flutter version, state management, router) — only to write the glue, never to
  change the package.
- Which pack tools map to which host endpoints; which have no endpoint yet (stay `mock`).
- Assistant DB target (same Supabase project, separate project, other Postgres).

## 2. Backend tools: mock → live, one tool at a time

1. Change `handler` in `tools.yaml`; do not rename the tool or change params.
2. If the host needs different params, add a `request_mapping` in the handler instead of changing
   the contract.
3. Write a `@pytest.mark.live` contract test hitting staging: success, not found, forbidden.
4. Run golden evals with `TOOL_MODE=live` on staging data; compare with mock-mode results.
5. Mark progress in STATUS.md ("accept_bid: live on staging, 2026-…").

## 3. Auth

- Host validates Supabase JWTs → `forward_user_jwt`.
- Host has its own auth → implement an `AuthVerifier` adapter for it, or run a token exchange
  endpoint; keep `user_ref` = host user id as text.
- Service-to-service → `service_token` + `X-User-Ref`; host must accept it only from the assistant
  backend (network allowlist or mTLS) because otherwise anyone could impersonate users.

## 4. Database

- Apply `supabase/migrations/0001_voice_core.sql` to the target. Schema `voice` avoids collisions.
- Re-ingest knowledge; run retrieval evals.
- If the host already uses Supabase: same project is fine; keep `voice` unexposed.

## 5. Frontend

- Add `voice_assistant` via path or git dependency pinned to a tag.
- Glue file in the host app only: config, `tokenProvider`, client action handlers, where the mic
  button appears, consent screen. No edits inside the package — if you need one, it's a package
  feature with a version bump.

## 6. New product (new domain pack)

Copy the pack structure, then in this order: persona (scope + refusals) → tool contracts →
fixtures → evals → knowledge → workflows → handlers. Evals before handlers keeps the contract
honest.

## 7. Done means

Boundary test green · all red-team hard assertions pass · latency measured on a real phone ·
secrets scan of the Flutter build clean · STATUS.md updated with what is live vs mock.
