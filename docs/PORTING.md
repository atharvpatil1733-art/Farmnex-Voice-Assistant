# Porting the assistant into a real app

The kit is designed so that moving it is configuration + a domain pack, not a rewrite.

## A. Same idea, real backend (e.g. the marketplace's actual FastAPI/GraphQL API)

1. Keep `domain_packs/farm_marketplace/`; change each tool's `handler` from `mock` to `http` or
   `graphql`. The tool `name`, `description`, and `params` stay the same, so evals stay valid.
2. Pick an auth mode: `forward_user_jwt` if the host API can validate Supabase tokens, otherwise
   `service_token` + `X-User-Ref` and have the host API trust only the assistant backend.
3. Add a contract test per tool that calls the real endpoint in a staging environment
   (`pytest -m live`).
4. Re-run `evals.run --suite text` with `TOOL_MODE=live` against staging data.

## B. Different database for the assistant

- Any Postgres ≥ 15 with pgvector: point `DATABASE_URL` at it and apply
  `supabase/migrations/0001_voice_core.sql` (it only uses schema `voice`; the `auth.uid()` RLS
  policies are Supabase-specific — guarded by a check in the migration).
- Host app on MySQL/Firebase/anything: no change — the assistant never reads host tables directly.
- Non-Postgres assistant store: implement `ConversationStore` and `KnowledgeStore` adapters; the
  core doesn't change.

## C. Different frontend

- Other Flutter app: add `voice_assistant` as a path/git dependency, provide `tokenProvider`,
  register `clientActions`, drop `VoiceMicButton` or `VoiceAssistantSheet` into a screen, add mic
  permissions.
- Non-Flutter client (web/React Native): implement `docs/PROTOCOL.md` — it's plain WebSocket.
- Different auth provider: implement an `AuthVerifier` adapter (e.g. Firebase, custom JWT).

## D. Different product (new domain)

1. Copy `domain_packs/_template` (create it from farm_marketplace by removing domain content).
2. Write `persona.md`, `tools.yaml`, workflows, knowledge docs, fixtures.
3. Write evals first: ≥ 30 golden cases, ≥ 20 retrieval cases, the shared red-team suite.
4. `DOMAIN_PACK=<new_pack>`; ingest knowledge; run evals; tune tool descriptions until targets pass.

## Checklist before calling it "ported"

- [ ] Boundary test passes (no domain words in core).
- [ ] All tools have real handlers or are explicitly marked `mock` in STATUS.md.
- [ ] Red-team suite: 0 unconfirmed writes, 0 cross-user access.
- [ ] Latency measured on a real phone on mobile data and recorded in STATUS.md.
- [ ] Consent screen + retention job configured.
- [ ] Secrets only in server env; Flutter bundle scanned for keys.
