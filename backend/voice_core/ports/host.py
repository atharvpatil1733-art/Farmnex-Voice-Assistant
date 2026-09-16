from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from voice_core.ports.types import ToolContext, ToolDef, ToolResult


@runtime_checkable
class HostToolHandler(Protocol):
    async def call(self, tool: ToolDef, args: dict[str, Any], ctx: ToolContext) -> ToolResult: ...
