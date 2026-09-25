"""`handler: {type: graphql}` — run a host GraphQL query or mutation.

handler:
  type: graphql
  document: |
    query Items($ref: String!) { items(ref: $ref) { itemRef price } }
  variables: {ref: "{{args.item_ref}}"}
  pick: data.items                     # dotted path into the response JSON
  endpoint: /graphql                   # optional, default /graphql
  timeout_s: 4                         # optional
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from voice_core.ports.types import ToolContext, ToolDef, ToolResult
from voice_core.tools.handlers.common import (
    AuthMode,
    HandlerConfigError,
    as_data,
    auth_headers,
    pick,
    render_path,
    substitute,
)
from voice_core.tools.handlers.http import DEFAULT_TIMEOUT_S, ResultStatus, map_http_status

logger = logging.getLogger(__name__)

# Conventional `extensions.code` values (Apollo-style) -> ToolResult status.
_ERROR_CODE_MAP: dict[str, ResultStatus] = {
    "FORBIDDEN": "forbidden",
    "UNAUTHENTICATED": "forbidden",
    "NOT_FOUND": "not_found",
    "BAD_USER_INPUT": "invalid",
}


class GraphQLToolHandler:
    def __init__(
        self,
        base_url: str,
        auth_mode: AuthMode = "forward_user_jwt",
        service_token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._auth_mode = auth_mode
        self._service_token = service_token
        self._client = httpx.AsyncClient(
            base_url=base_url, transport=transport, timeout=DEFAULT_TIMEOUT_S
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def call(self, tool: ToolDef, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        config = tool.config
        headers = auth_headers(self._auth_mode, ctx, self._service_token)
        if headers is None:
            return ToolResult(status="forbidden", error_code="HOST_AUTH_UNAVAILABLE")
        if ctx.idempotency_key:
            headers["Idempotency-Key"] = ctx.idempotency_key

        try:
            endpoint = render_path(str(config.get("endpoint", "/graphql")), {})
            document = str(config["document"])
        except (KeyError, HandlerConfigError):
            logger.error("graphql_handler_bad_config", extra={"tool": tool.name}, exc_info=True)
            return ToolResult(status="error", error_code="HANDLER_CONFIG")

        variables = substitute(config.get("variables") or {}, args)
        try:
            response = await self._client.post(
                endpoint,
                json={"query": document, "variables": variables},
                headers=headers,
                timeout=float(config.get("timeout_s", DEFAULT_TIMEOUT_S)),
            )
        except httpx.TimeoutException:
            return ToolResult(status="error", error_code="HOST_TIMEOUT")
        except httpx.HTTPError:
            logger.warning("graphql_handler_unreachable", extra={"tool": tool.name})
            return ToolResult(status="error", error_code="HOST_UNREACHABLE")

        http_status = map_http_status(response.status_code, None)
        try:
            payload = response.json()
        except ValueError:
            return ToolResult(status="error", error_code=f"HTTP_{response.status_code}")

        errors = payload.get("errors") if isinstance(payload, dict) else None
        if errors:
            first = errors[0] if isinstance(errors[0], dict) else {}
            code = str((first.get("extensions") or {}).get("code", "")).upper()
            return ToolResult(
                status=_ERROR_CODE_MAP.get(code, "error"),
                error_code=f"GRAPHQL_{code or 'ERROR'}",
            )
        if http_status != "ok":
            return ToolResult(
                status=http_status,
                error_code=f"HTTP_{response.status_code}",
            )
        return ToolResult(status="ok", data=as_data(pick(payload, config.get("pick", "data"))))
