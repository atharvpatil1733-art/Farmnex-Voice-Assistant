---
name: voice-agent-core
description: Architecture rules and implementation recipes for the portable voice assistant core in backend/voice_core — ports and adapters, the agent turn loop, prompt assembly, sessions, the write-confirmation gate, and provider adapters (LLM, STT, TTS, embeddings, auth, stores). Use this whenever creating or editing anything under backend/voice_core or backend/app, adding or swapping an AI provider, changing how turns, tools, history or prompts work, or when unsure where a piece of code belongs — even for small changes, because the boundary rules are easy to break silently.
---

# Voice agent core

The core is reusable across products. Its value comes from staying domain-agnostic and
provider-agnostic, so most mistakes here are *placement* mistakes. Read `docs/SPEC.md` §2–§7
before large changes.

## Where code goes

| You are writing… | Put it in | Must not import |
|---|---|---|
| A capability interface | `voice_core/ports/*.py` (typing.Protocol + dataclasses/pydantic) | any vendor SDK |
| A vendor integration | `voice_core/adapters/<vendor>/` | agent/, transport/ |
| Test doubles | `voice_core/adapters/fakes/` | network libs |
| Turn logic, prompt, session, gate | `voice_core/agent/` | adapters (use ports) |
| Tool registry, schema validation, handlers | `voice_core/tools/` | domain names |
| WebSocket/REST + message models | `voice_core/transport/` | adapters |
| Wiring (choose adapters from config) | `voice_core/config.py`, `app/main.py` | — |
| Anything mentioning a domain noun | `domain_packs/<pack>/` only | — |

Dependency direction: `transport → agent → ports ← adapters`. `app/main.py` is the only place
that knows concrete adapters. If you need a vendor type inside `agent/`, the port is missing a
field — extend the port instead.

## Adding a provider adapter — checklist

1. Check the vendor's current docs (Context7 MCP if available). Note the exact model id, auth
   header, request limits (e.g. max characters per TTS call), and response fields.
2. Implement the port in `adapters/<vendor>/<capability>.py`. Map vendor errors to core error
   types (`ProviderTimeout`, `ProviderRateLimited`, `ProviderBadRequest`, `ProviderUnavailable`).
3. Use one shared `httpx.AsyncClient` per provider with explicit timeouts; retry only idempotent
   calls, at most 2 times with jitter, and never retry after the first audio/text byte was emitted.
4. Register it in `config.py` under a provider name (`STT_PROVIDER=sarvam`).
5. Tests: unit test with recorded/fixture responses (no network) + one `@pytest.mark.live` test.
6. Record model id and date verified in `docs/STATUS.md` decisions log.

## The turn loop (agent/loop.py) — shape to preserve

```python
async def run_turn(session: Session, user_input: UserInput, out: TurnSink) -> None:
    timer = TurnTimer()
    text, lang = await resolve_input(user_input, session, timer)        # STT if audio
    if (handled := await precheck(text, lang, session, out)):            # confirm/stop/lang
        return
    retrieval = asyncio.create_task(auto_retrieve(text, session, timer)) # parallel
    messages = build_prompt(session, text, await retrieval)
    for round_ in range(MAX_TOOL_ROUNDS):
        tool_calls = []
        async for ev in llm.stream(messages, session.pack.tool_specs(), ...):
            match ev:
                case TextDelta(): await out.text(ev.text)                # → segmenter → TTS
                case ToolCall():  tool_calls.append(ev)
        if not tool_calls:
            break
        messages += await execute_tool_calls(tool_calls, session, out)   # read runs; write → gate
    await out.finish(timer)
```

Keep it linear and readable. Cancellation must work at every `await` (interrupts cancel the task).
Wrap tool execution so a `CancelledError` during a *write* waits for the handler to finish.

## Confirmation gate (agent/confirmation.py)

- `request(tool, args) -> PendingAction`: validate, compute idempotency key, render the
  per-language confirm template from the pack, persist, emit `confirm.request`, return
  `{"status":"awaiting_confirmation","summary":…}` to the LLM.
- `resolve(decision, via)`: only transitions `pending → executing → executed_*` or
  `pending → cancelled|expired`. Execution uses the **stored** args.
- Precheck lexicon matching: normalize (lowercase, strip punctuation, collapse spaces, NFC) and
  require the *whole* utterance to be a yes/no phrase. Partial matches go to the LLM.
- Tests that must exist: yes executes once; duplicate yes doesn't execute twice; no cancels;
  expiry cancels; changed args create a new action; LLM claiming success without execution is
  caught by the eval "no unsupported success claims".

## Prompt assembly (agent/prompt.py)

Order is fixed (SPEC §10): core rules → persona → session facts → workflow/pending state →
knowledge → history → utterance. Untrusted content goes inside `<knowledge source=…>` and
`<tool_result tool=…>` tags. Keep a `prompt_hash` of the rendered static part and store it with
eval runs so prompt changes are traceable.

## Common mistakes to avoid

- Adding `user_id` to a tool schema "for convenience" → identity must come from `ctx.user_ref`.
- Letting the LLM phrase the confirmation question freely → use the template.
- Calling a second LLM to classify intent → adds 300–800 ms per turn; let tool calling route.
- Catching broad exceptions and returning "done" text → surface a localized error instead.
- Importing `sarvamai`/`openai` in `agent/` for a type hint → extend the port.

## Definition of done for core changes

`uv run pytest`, `uv run ruff check .`, `uv run bandit -r voice_core app -q` all pass;
boundary test passes; affected eval suite re-run if behavior changed; STATUS.md updated when a
milestone item changes.
