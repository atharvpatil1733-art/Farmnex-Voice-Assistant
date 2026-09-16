from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from voice_core.ports.types import ToolContext, ToolDef, ToolResult

_PLACEHOLDER = re.compile(r"^\{\{args\.([a-zA-Z_][a-zA-Z0-9_]*)\}\}$")


def _substitute(value: Any, args: dict[str, Any]) -> Any:
    if isinstance(value, str):
        match = _PLACEHOLDER.match(value)
        if match:
            return args[match.group(1)]
        return value
    if isinstance(value, dict):
        return {k: _substitute(v, args) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, args) for v in value]
    return value


class MockToolHandler:
    """Fixture-based HostToolHandler. Never touches fixtures without a {status,data} envelope
    (e.g. workflow slot-validator fixtures) — those aren't referenced by any tool's handler."""

    def __init__(self, pack_dir: Path, fixture_overrides: dict[str, str] | None = None) -> None:
        self._pack_dir = pack_dir
        self._fixture_overrides = fixture_overrides or {}

    async def call(self, tool: ToolDef, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        fixture_path = self._fixture_overrides.get(tool.name, tool.config["fixture"])
        raw = json.loads((self._pack_dir / fixture_path).read_text(encoding="utf-8"))
        substituted = _substitute(raw, args)

        status = substituted.get("status", "error")
        return ToolResult(
            status=status,
            data=substituted.get("data"),
            error_code=substituted.get("error_code"),
        )
