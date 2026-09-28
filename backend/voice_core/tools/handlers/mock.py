from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from voice_core.ports.types import ToolContext, ToolDef, ToolResult
from voice_core.tools.handlers.common import substitute

_DATE_TOKEN = re.compile(r"\{\{date:([+-]\d+)\}\}")


def relative_dates(value: Any, today: date) -> Any:
    """Replace "{{date:+N}}" inside fixture strings with the ISO date N days from today."""
    if isinstance(value, str):
        return _DATE_TOKEN.sub(
            lambda m: (today + timedelta(days=int(m.group(1)))).isoformat(), value
        )
    if isinstance(value, dict):
        return {k: relative_dates(v, today) for k, v in value.items()}
    if isinstance(value, list):
        return [relative_dates(v, today) for v in value]
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
        substituted = relative_dates(substitute(raw, args), date.today())

        status = substituted.get("status", "error")
        return ToolResult(
            status=status,
            data=substituted.get("data"),
            error_code=substituted.get("error_code"),
        )
