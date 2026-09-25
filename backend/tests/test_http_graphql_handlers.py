from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from voice_core.ports.types import ToolContext, ToolDef
from voice_core.tools.handlers.common import HandlerConfigError, render_path
from voice_core.tools.handlers.graphql import GraphQLToolHandler
from voice_core.tools.handlers.http import HttpToolHandler
from voice_core.tools.handlers.router import HandlerRouter

CTX = ToolContext(user_ref="u-42", language="en-IN", auth_token="user-jwt")


class Recorder:
    """httpx MockTransport handler that records requests and replays a canned response."""

    def __init__(self, status: int = 200, body: Any = None, exc: Exception | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._status = status
        self._body = body
        self._exc = exc

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._exc:
            raise self._exc
        return httpx.Response(self._status, json=self._body)


def _http(recorder: Recorder, **kwargs: Any) -> HttpToolHandler:
    return HttpToolHandler(
        "https://host.example", transport=httpx.MockTransport(recorder), **kwargs
    )


def _tool(handler_type: str = "http", kind: str = "read", **config: Any) -> ToolDef:
    return ToolDef(name="t", kind=kind, handler_type=handler_type, config=config)  # type: ignore[arg-type]


async def test_http_get_fills_path_query_and_picks_response() -> None:
    rec = Recorder(body={"data": {"items": [1, 2]}, "meta": {}})
    tool = _tool(
        path="/things/{ref}",
        query={"limit": "{{args.limit}}", "skip": "{{args.none}}"},
        pick="data",
    )

    result = await _http(rec).call(tool, {"ref": "R-1", "limit": 5}, CTX)

    assert result.status == "ok"
    assert result.data == {"items": [1, 2]}
    request = rec.requests[0]
    assert request.method == "GET"
    assert request.url.path == "/things/R-1"
    assert dict(request.url.params) == {"limit": "5"}
    assert request.headers["Authorization"] == "Bearer user-jwt"


async def test_path_params_cannot_escape_the_endpoint() -> None:
    rec = Recorder(body={})
    tool = _tool(path="/things/{ref}")

    await _http(rec).call(tool, {"ref": "../admin/delete?x=1"}, CTX)

    raw = rec.requests[0].url.raw_path.decode()
    assert raw.startswith("/things/..%2Fadmin%2Fdelete%3Fx%3D1")


def test_absolute_urls_are_rejected_as_paths() -> None:
    with pytest.raises(HandlerConfigError):
        render_path("https://evil.example/x", {})
    with pytest.raises(HandlerConfigError):
        render_path("relative/no/slash", {})


async def test_http_write_sends_json_body_and_idempotency_key() -> None:
    rec = Recorder(body={"accepted": True})
    tool = _tool(
        kind="write", method="post", path="/things/{ref}/accept", body={"item": "{{args.item}}"}
    )
    ctx = ToolContext(
        user_ref="u-42", language="en-IN", auth_token="user-jwt", idempotency_key="k-1"
    )

    result = await _http(rec).call(tool, {"ref": "R-1", "item": "B-9"}, ctx)

    assert result.status == "ok"
    request = rec.requests[0]
    assert request.method == "POST"
    assert json.loads(request.content) == {"item": "B-9"}
    assert request.headers["Idempotency-Key"] == "k-1"


async def test_service_token_mode_sends_user_ref_from_context_only() -> None:
    rec = Recorder(body={})
    handler = _http(rec, auth_mode="service_token", service_token="svc")

    await handler.call(_tool(path="/x"), {"user_ref": "someone-else"}, CTX)

    headers = rec.requests[0].headers
    assert headers["Authorization"] == "Bearer svc"
    assert headers["X-User-Ref"] == "u-42"


async def test_missing_user_token_is_forbidden_without_calling_host() -> None:
    rec = Recorder(body={})
    ctx = ToolContext(user_ref="u-42", language="en-IN")

    result = await _http(rec).call(_tool(path="/x"), {}, ctx)

    assert result.status == "forbidden"
    assert rec.requests == []


@pytest.mark.parametrize(
    ("code", "status_map", "expected"),
    [
        (404, None, "not_found"),
        (403, None, "forbidden"),
        (500, None, "error"),
        (409, {409: "invalid"}, "invalid"),
    ],
)
async def test_http_status_mapping(code: int, status_map: Any, expected: str) -> None:
    rec = Recorder(status=code, body={"error": "x"})
    tool = _tool(path="/x", status_map=status_map) if status_map else _tool(path="/x")

    result = await _http(rec).call(tool, {}, CTX)

    assert result.status == expected
    assert result.error_code == f"HTTP_{code}"


async def test_network_failure_is_an_error_result_not_an_exception() -> None:
    rec = Recorder(exc=httpx.ConnectError("down"))
    result = await _http(rec).call(_tool(path="/x"), {}, CTX)
    assert result.status == "error"
    assert result.error_code == "HOST_UNREACHABLE"


async def test_timeout_is_reported() -> None:
    rec = Recorder(exc=httpx.ReadTimeout("slow"))
    result = await _http(rec).call(_tool(path="/x"), {}, CTX)
    assert result.error_code == "HOST_TIMEOUT"


def _gql(recorder: Recorder) -> GraphQLToolHandler:
    return GraphQLToolHandler("https://host.example", transport=httpx.MockTransport(recorder))


async def test_graphql_sends_document_and_variables_and_picks_data() -> None:
    rec = Recorder(body={"data": {"things": [{"ref": "R-1"}]}})
    tool = _tool(
        "graphql",
        document="query Q($r: String!) { things(ref: $r) { ref } }",
        variables={"r": "{{args.ref}}"},
        pick="data.things",
    )

    result = await _gql(rec).call(tool, {"ref": "R-1"}, CTX)

    assert result.status == "ok"
    assert result.data == {"items": [{"ref": "R-1"}]}
    sent = json.loads(rec.requests[0].content)
    assert sent["variables"] == {"r": "R-1"}
    assert rec.requests[0].url.path == "/graphql"


async def test_graphql_errors_map_to_statuses() -> None:
    rec = Recorder(
        body={"data": None, "errors": [{"message": "no", "extensions": {"code": "FORBIDDEN"}}]}
    )
    tool = _tool("graphql", document="query { x }")

    result = await _gql(rec).call(tool, {}, CTX)

    assert result.status == "forbidden"
    assert result.error_code == "GRAPHQL_FORBIDDEN"


async def test_router_dispatches_by_handler_type_and_rejects_unknown() -> None:
    rec = Recorder(body={"ok": 1})
    router = HandlerRouter({"http": _http(rec)})

    assert (await router.call(_tool(path="/x"), {}, CTX)).status == "ok"
    unknown = await router.call(_tool("python"), {}, CTX)
    assert unknown.status == "error"
    assert unknown.error_code == "HANDLER_TYPE_UNAVAILABLE"


def test_auth_token_never_appears_in_context_repr() -> None:
    assert "user-jwt" not in repr(CTX)


@pytest.mark.parametrize("value", ["..", ".", ""])
def test_dot_segment_or_empty_path_params_are_rejected(value: str) -> None:
    """quote('..') is still '..', and httpx resolves dot segments — so '..' would move the
    request to a different endpoint with the user's token."""
    with pytest.raises(HandlerConfigError):
        render_path("/things/{ref}/sub", {"ref": value})


async def test_dot_segment_param_never_reaches_the_host() -> None:
    rec = Recorder(body={})
    result = await _http(rec).call(_tool(path="/things/{ref}"), {"ref": ".."}, CTX)
    assert result.status == "invalid"
    assert rec.requests == []
