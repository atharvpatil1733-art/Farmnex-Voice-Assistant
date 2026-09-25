from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from voice_core.ports.types import ToolContext, ToolDef, ToolResult
from voice_core.tools.handlers.common import substitute


class MockToolHandler:
    """Fixture-based HostToolHandler. Never touches fixtures without a {status,data} envelope
    (e.g. workflow slot-validator fixtures) — those aren't referenced by any tool's handler."""

    def __init__(self, pack_dir: Path, fixture_overrides: dict[str, str] | None = None) -> None:
        self._pack_dir = pack_dir
        self._fixture_overrides = fixture_overrides or {}

    async def call(self, tool: ToolDef, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        fixture_path = self._fixture_overrides.get(tool.name, tool.config["fixture"])
        raw = json.loads((self._pack_dir / fixture_path).read_text(encoding="utf-8"))
        substituted = substitute(raw, args)

        status = substituted.get("status", "error")
        return ToolResult(
            status=status,
            data=substituted.get("data"),
            error_code=substituted.get("error_code"),
        )
