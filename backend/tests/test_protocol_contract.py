"""Every JSON example in docs/PROTOCOL.md must parse with the pydantic protocol models."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from voice_core.transport.protocol import CLIENT_MESSAGE, SERVER_MESSAGE

PROTOCOL_MD = Path(__file__).resolve().parents[2] / "docs" / "PROTOCOL.md"


def _examples(section_title: str) -> list[dict[str, Any]]:
    text = PROTOCOL_MD.read_text(encoding="utf-8")
    section = text.split(f"## {section_title}", 1)[1].split("\n## ", 1)[0]
    block = re.search(r"```jsonc\n(.*?)```", section, re.S)
    assert block, f"no jsonc block under {section_title}"
    examples: list[dict[str, Any]] = []
    buffer = ""
    for raw_line in block.group(1).splitlines():
        line = re.sub(r"\s+//.*$", "", raw_line).strip()  # strip trailing // comments
        if not line or line.startswith("//"):
            continue
        buffer += line
        if buffer.count("{") == buffer.count("}"):
            examples.append(json.loads(buffer))
            buffer = ""
    assert not buffer, f"unbalanced example: {buffer}"
    return examples


CLIENT_EXAMPLES = _examples("Client → Server")
SERVER_EXAMPLES = _examples("Server → Client")


def test_examples_were_found() -> None:
    assert len(CLIENT_EXAMPLES) >= 12
    assert len(SERVER_EXAMPLES) >= 14


@pytest.mark.parametrize("example", CLIENT_EXAMPLES, ids=lambda e: e["type"])
def test_client_examples_match_models(example: dict[str, Any]) -> None:
    CLIENT_MESSAGE.validate_python(example)


@pytest.mark.parametrize("example", SERVER_EXAMPLES, ids=lambda e: e["type"])
def test_server_examples_match_models(example: dict[str, Any]) -> None:
    SERVER_MESSAGE.validate_python(example)


def test_every_documented_type_has_a_model() -> None:
    documented = {e["type"] for e in CLIENT_EXAMPLES} | {e["type"] for e in SERVER_EXAMPLES}
    assert "transcript.final" in documented and "confirm.response" in documented


def test_unknown_client_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CLIENT_MESSAGE.validate_python({"type": "tool.call", "name": "accept"})


def test_session_start_token_is_not_in_repr() -> None:
    message = CLIENT_MESSAGE.validate_python({"type": "session.start", "token": "secret-jwt"})
    assert "secret-jwt" not in repr(message)
