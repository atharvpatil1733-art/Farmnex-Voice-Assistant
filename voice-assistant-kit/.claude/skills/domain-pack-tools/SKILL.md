---
name: domain-pack-tools
description: How to add or change what the voice assistant can do for a specific app — tools (read and write), workflows with slot filling, persona, confirmation templates, client actions, fixtures, and host backend handlers (mock, http, graphql, python) inside domain_packs/<pack>/. Use this whenever the user wants the assistant to answer a new kind of question about user data, perform a new action, connect a tool to a real endpoint, change the assistant's personality or scope, or add a guided multi-step flow like creating a listing — even if they just say "make the assistant able to…".
---

# Domain pack tools and workflows

A domain pack is everything app-specific. The core loads it from `DOMAIN_PACK`. The LLM only
sees tool *contracts* (name, description, params), so the contract is the part to design well;
handlers can change later without touching evals.

## Pack layout

```
domain_packs/<pack>/
├── pack.yaml          id, languages, default language, speaker, fillers, client_actions allowlist
├── persona.md         who the assistant is, scope, tone, what it refuses
├── tools.yaml         tool contracts + handlers + confirm templates
├── workflows/*.yaml   guided slot filling for write tools
├── knowledge/<lang>/  markdown docs (see knowledge-base-rag skill)
├── fixtures/*.json    mock data used by `mock` handlers and evals
└── evals/             golden.jsonl, retrieval.jsonl, redteam.jsonl
```

## Designing a tool contract

1. **Name**: `verb_object`, e.g. `get_order_status`, `accept_bid`. Stable forever once evals use it.
2. **Description** (for the model): what it returns, when to use it, and when *not* to use it.
   Mention the words users actually say in all three languages if they differ a lot
   ("bids / बोली / offers").
3. **Params**: JSON Schema with `additionalProperties: false`. Prefer references users can say
   ("latest", crop name) and resolve them in the handler, over opaque ids the user can't know.
   Never add user identity fields — the handler gets `ctx.user_ref`.
4. **kind**: `read` if nothing changes anywhere; otherwise `write`. When in doubt, `write`.
5. **result_fields**: whitelist what the LLM needs. Less data = fewer hallucinations, less PII.
6. **result_hint**: one line on how to speak the result ("highest first, max two").
7. **display_hint**: short per-language label for the "working…" UI state.
8. **confirm** (write only): per-language template using validated arg names. It must state the
   consequence plainly (what, how much, for whom) and end with a question.

Example write tool:

```yaml
- name: accept_bid
  kind: write
  description: >-
    Accept one buyer's bid on the user's listing. Use only after the user clearly picks a bid.
    Do not use to check bids (use get_bids_for_listing).
  params:
    type: object
    additionalProperties: false
    required: [listing_ref, bid_ref]
    properties:
      listing_ref: {type: string}
      bid_ref: {type: string, description: "Bid id from get_bids_for_listing"}
  resolve_for_confirm: get_bids_for_listing   # core fetches display fields for the template
  confirm:
    hi-IN: "{quantity_kg} किलो {crop} के लिए {price_per_kg} रुपये किलो की बोली स्वीकार करूँ?"
    mr-IN: "{quantity_kg} किलो {crop} साठी {price_per_kg} रुपये किलोची बोली स्वीकारू का?"
    en-IN: "Shall I accept the bid of {price_per_kg} rupees per kilo for {quantity_kg} kilos of {crop}?"
  success_message:
    hi-IN: "बोली स्वीकार हो गई है।"
    mr-IN: "बोली स्वीकारली आहे."
    en-IN: "The bid has been accepted."
  handler:
    type: http
    method: POST
    path: /listings/{listing_ref}/bids/{bid_ref}/accept
    auth: forward_user_jwt
    idempotency_header: Idempotency-Key
```

## Handlers

- Start every tool as `mock` with a fixture so the text brain and evals work before the host API
  exists. Fixtures should include edge cases: empty lists, one item, errors.
- Switch to `http`/`graphql` per tool when the endpoint is ready; keep the contract unchanged.
- Map host responses to `ToolResult.status`: 2xx → ok, 404 → not_found, 401/403 → forbidden,
  422 → invalid, timeout → timeout. Never pass raw host error text to the LLM.
- Use `python` handlers only for logic the declarative handlers can't express (e.g. resolving
  "latest listing" across two calls). They live in `domain_packs/<pack>/handlers/`.

## Workflows

```yaml
id: create_prebid_listing
tool: create_prebid_listing          # final write tool
start_phrases: {hi-IN: ["फसल बेचनी है"], mr-IN: ["पीक विकायचे आहे"], en-IN: ["sell my crop"]}
slots:
  - name: crop
    ask: {hi-IN: "कौन सी फसल बेचनी है?", mr-IN: "कोणते पीक विकायचे आहे?", en-IN: "Which crop do you want to sell?"}
    validate: {enum_from: fixtures/crops.json}
  - name: quantity_kg
    ask: {hi-IN: "कितने किलो?", mr-IN: "किती किलो?", en-IN: "How many kilos?"}
    validate: {type: number, min: 1, max: 100000}
    unit_parsing: [quintal=100, ton=1000, क्विंटल=100, टन=1000]
  - name: photo
    ask: {hi-IN: "अब फसल की फोटो डालिए।", mr-IN: "आता पिकाचा फोटो टाका.", en-IN: "Now add a photo of the crop."}
    client_action: {name: open_photo_picker}
    wait_for: client_action.result
```

Rules: one slot per turn; accept multiple slots if the user gives them at once; allow correction
("नहीं, 600 किलो"); leaving the workflow never executes anything.

## Client actions

Tools and workflows may return `client_actions` (`navigate`, `open_photo_picker`, `refresh`).
Every name must be in `pack.yaml → client_actions` allowlist, and the host Flutter app must
register a handler. Unknown actions are dropped and logged.

## Required with every new or changed tool

- ≥ 3 golden eval cases (hi-IN, mr-IN, en-IN), including one where the tool should NOT be used.
- For write tools: a red-team case trying to skip confirmation and one with a changed argument.
- Fixture covering empty and error results.
- Run `uv run python -m voice_core.evals.run --pack <pack> --suite text` and record results.
