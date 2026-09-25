from __future__ import annotations

import pytest

from voice_core.config import get_settings
from voice_core.ports.types import ChatMessage, Done, TextDelta, ToolCall, ToolSpec

pytestmark = pytest.mark.live

_settings = get_settings()
_API_KEY = _settings.llm_api_key
_MODEL = _settings.llm_model
_BASE_URL = _settings.llm_base_url


@pytest.mark.skipif(
    not (_API_KEY and _BASE_URL), reason="LLM_API_KEY / LLM_BASE_URL not set (backend/.env)"
)
async def test_tool_triggering_prompt_yields_a_tool_call() -> None:
    from voice_core.adapters.openai_compat.llm import OpenAICompatLLM

    llm = OpenAICompatLLM(api_key=_API_KEY, model=_MODEL, base_url=_BASE_URL)
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
            max_tokens=200,
            timeout_s=30.0,
        )
    ]
    tool_calls = [e for e in events if isinstance(e, ToolCall)]
    assert tool_calls
    assert tool_calls[0].name == "get_weather"
    assert any(isinstance(e, Done) for e in events)


@pytest.mark.skipif(
    not (_API_KEY and _BASE_URL), reason="LLM_API_KEY / LLM_BASE_URL not set (backend/.env)"
)
async def test_plain_prompt_yields_text_and_done() -> None:
    from voice_core.adapters.openai_compat.llm import OpenAICompatLLM

    llm = OpenAICompatLLM(api_key=_API_KEY, model=_MODEL, base_url=_BASE_URL)
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
