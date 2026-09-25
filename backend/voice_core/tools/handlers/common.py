"""Helpers shared by the mock/http/graphql handlers. Domain-agnostic: every mapping comes from
the pack's tools.yaml `handler:` block."""

from __future__ import annotations

import re
from typing import Any, Literal
from urllib.parse import quote

from voice_core.ports.types import ToolContext

_PLACEHOLDER = re.compile(r"^\{\{args\.([a-zA-Z_][a-zA-Z0-9_]*)\}\}$")
_PATH_PARAM = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

AuthMode = Literal["forward_user_jwt", "service_token"]


class HandlerConfigError(ValueError):
    """The pack's handler block is unusable (a pack bug, not a runtime/host failure)."""


class InvalidPathParam(HandlerConfigError):
    """An arg can't be used as a path segment (missing, empty, or a '.'/'..' dot segment)."""


def substitute(value: Any, args: dict[str, Any]) -> Any:
    """Replace whole-string "{{args.x}}" placeholders with the arg's value (type preserved).
    A placeholder for an arg the LLM didn't supply becomes None."""
    if isinstance(value, str):
        match = _PLACEHOLDER.match(value)
        return args.get(match.group(1)) if match else value
    if isinstance(value, dict):
        return {k: substitute(v, args) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, args) for v in value]
    return value


def drop_none(value: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in value.items() if v is not None}


def render_path(template: str, args: dict[str, Any]) -> str:
    """Fill "{name}" path params from args, percent-encoding each value completely (so "../x"
    or "a/b" can never change which endpoint is hit). Only relative paths are allowed: the host
    is fixed by config, never chosen by the LLM."""
    if not template.startswith("/") or "://" in template:
        raise HandlerConfigError(f"handler path must be a relative path, got {template!r}")

    def fill(match: re.Match[str]) -> str:
        name = match.group(1)
        value = args.get(name)
        # quote("..") is still "..", and URL resolution would then climb out of the endpoint.
        if value is None or str(value) in ("", ".", ".."):
            raise InvalidPathParam(f"path param {name!r} is missing or a dot segment")
        return quote(str(value), safe="")

    return _PATH_PARAM.sub(fill, template)


def pick(payload: Any, path: str | None) -> Any:
    """Follow a dotted path ("data.listingBids") into a JSON payload; None if absent."""
    if not path:
        return payload
    current = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def as_data(value: Any) -> dict[str, Any] | None:
    """ToolResult.data is a dict; wrap a bare list/scalar so the LLM still gets it."""
    if value is None or isinstance(value, dict):
        return value
    return {"items": value} if isinstance(value, list) else {"value": value}


def auth_headers(
    mode: AuthMode, ctx: ToolContext, service_token: str | None
) -> dict[str, str] | None:
    """Headers identifying the caller to the host API, or None if the mode can't be satisfied.
    Identity always comes from the verified context, never from tool args (golden rule 3)."""
    if mode == "forward_user_jwt":
        if not ctx.auth_token:
            return None
        return {"Authorization": f"Bearer {ctx.auth_token}"}
    if not service_token:
        return None
    return {"Authorization": f"Bearer {service_token}", "X-User-Ref": ctx.user_ref}
