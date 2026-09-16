from __future__ import annotations

from typing import Any

from voice_core.ports.types import ToolContext, ToolDef, ToolResult


class FakeHostToolHandler:
    """Returns a scripted ToolResult per tool name, regardless of args or ctx."""

    def __init__(self, results: dict[str, ToolResult] | None = None) -> None:
        self._results = results or {}

    async def call(self, tool: ToolDef, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return self._results.get(
            tool.name,
            ToolResult(status="not_found", error_code="NO_FIXTURE"),
        )
