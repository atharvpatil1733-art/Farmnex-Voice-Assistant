from __future__ import annotations

from typing import Any

from voice_core.ports.host import HostToolHandler
from voice_core.ports.types import ToolContext, ToolDef, ToolResult


class HandlerRouter:
    """HostToolHandler that sends each tool to the handler for its `handler.type`, so one pack
    can mix mock, http and graphql tools while the real host API is built out."""

    def __init__(self, handlers: dict[str, HostToolHandler]) -> None:
        self._handlers = handlers

    async def call(self, tool: ToolDef, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        handler = self._handlers.get(tool.handler_type)
        if handler is None:
            return ToolResult(status="error", error_code="HANDLER_TYPE_UNAVAILABLE")
        return await handler.call(tool, args, ctx)
