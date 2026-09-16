from __future__ import annotations

import pytest

from voice_core.config import get_settings
from voice_core.ports.types import ChatMessage, Done, TextDelta, ToolCall, ToolSpec

pytestmark = pytest.mark.live

_settings = get_settings()
GEMINI_API_KEY = _settings.llm_api_key
GEMINI_MODEL = _settings.llm_model or "gemini-3.8-flash"


@pytest.mark.skipif(not GEMINI_API_KEY, reason="LLM_API_KEY not set (backend/.env)")
async def test_plain_prompt_yields_text_and_done() -> None:
    from voice_core.adapters.gemini.llm import GeminiLLM

    llm = GeminiLLM(api_key=GEMINI_API_KEY, model=GEMINI_MODEL)
    events = [
        e
        async for e in llm.stream(
            [ChatMessage(role="user", content="Say hello in one short word.")],
            [],
            temperature=0.0,
            max_tokens=50,
            timeout_s=30.0,
        )
    ]
    assert any(isinstance(e, TextDelta) for e in events)
    assert any(isinstance(e, Done) for e in events)


@pytest.mark.skipif(not GEMINI_API_KEY, reason="LLM_API_KEY not set (backend/.env)")
async def test_tool_triggering_prompt_yields_a_tool_call() -> None:
    from voice_core.adapters.gemini.llm import GeminiLLM

    llm = GeminiLLM(api_key=GEMINI_API_KEY, model=GEMINI_MODEL)
    tool = ToolSpec(
        name="get_weather",
        description="Get the current weather for a city. Use whenever weather is asked about.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "required": ["city"],
            "properties": {"city": {"type": "string"}},
        },
    )
    events = [
        e
        async for e in llm.stream(
            [ChatMessage(role="user", content="What's the weather like in Boston right now?")],
            [tool],
            temperature=0.0,
            max_tokens=100,
            timeout_s=30.0,
        )
    ]
    tool_calls = [e for e in events if isinstance(e, ToolCall)]
    assert tool_calls
    assert tool_calls[0].name == "get_weather"
    assert any(isinstance(e, Done) for e in events)
