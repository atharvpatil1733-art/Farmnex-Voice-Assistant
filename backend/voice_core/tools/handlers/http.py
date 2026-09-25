"""`handler: {type: http}` — call a host REST endpoint.

handler:
  type: http
  method: POST                         # default GET
  path: /items/{item_ref}/approve      # {arg} path params, fully percent-encoded
  query: {limit: "{{args.limit}}"}     # optional; None values dropped
  body: {choice: "{{args.choice}}"}    # optional JSON body
  pick: data                           # optional dotted path into the response JSON
  status_map: {409: invalid}           # optional extra HTTP status -> ToolResult status
  timeout_s: 4                         # optional, default 4
"""

from __future__ import annotations

import logging
from typing import Any, Literal

import httpx

from voice_core.ports.types import ToolContext, ToolDef, ToolResult
from voice_core.tools.handlers.common import (
    AuthMode,
    HandlerConfigError,
    InvalidPathParam,
    as_data,
    auth_headers,
    drop_none,
    pick,
    render_path,
    substitute,
)

logger = logging.getLogger(__name__)

ResultStatus = Literal["ok", "error", "not_found", "forbidden", "invalid"]
DEFAULT_TIMEOUT_S = 4.0
_DEFAULT_STATUS_MAP: dict[int, ResultStatus] = {
    400: "invalid",
    401: "forbidden",
    403: "forbidden",
    404: "not_found",
    422: "invalid",
}
_ALLOWED: tuple[ResultStatus, ...] = ("ok", "error", "not_found", "forbidden", "invalid")


def map_http_status(code: int, extra: dict[Any, Any] | None) -> ResultStatus:
    mapping: dict[int, str] = {
        **_DEFAULT_STATUS_MAP,
        **{int(k): v for k, v in (extra or {}).items()},
    }
    status = mapping.get(code, "ok" if 200 <= code < 300 else "error")
    return next((s for s in _ALLOWED if s == status), "error")


class HttpToolHandler:
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
            path = render_path(config["path"], args)
        except InvalidPathParam:
            return ToolResult(status="invalid", error_code="INVALID_PATH_PARAM")
        except (KeyError, HandlerConfigError):
            logger.error("http_handler_bad_config", extra={"tool": tool.name}, exc_info=True)
            return ToolResult(status="error", error_code="HANDLER_CONFIG")

        query = drop_none(substitute(config.get("query") or {}, args))
        body = substitute(config["body"], args) if "body" in config else None
        try:
            response = await self._client.request(
                str(config.get("method", "GET")).upper(),
                path,
                params=query or None,
                json=body,
                headers=headers,
                timeout=float(config.get("timeout_s", DEFAULT_TIMEOUT_S)),
            )
        except httpx.TimeoutException:
            return ToolResult(status="error", error_code="HOST_TIMEOUT")
        except httpx.HTTPError:
            logger.warning("http_handler_unreachable", extra={"tool": tool.name})
            return ToolResult(status="error", error_code="HOST_UNREACHABLE")

        status = map_http_status(response.status_code, config.get("status_map"))
        if status != "ok":
            return ToolResult(status=status, error_code=f"HTTP_{response.status_code}")
        try:
            payload = response.json() if response.content else None
        except ValueError:
            return ToolResult(status="error", error_code="HOST_BAD_JSON")
        return ToolResult(status="ok", data=as_data(pick(payload, config.get("pick"))))
