from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from voice_core.packs.loader import LoadedPack
from voice_core.ports.embeddings import EmbeddingProvider
from voice_core.ports.host import HostToolHandler
from voice_core.ports.knowledge import KnowledgeStore
from voice_core.ports.types import ToolContext, ToolDef, ToolResult, ToolSpec
from voice_core.tools.schema import assert_no_identity_fields, validate_args

CoreHandler = Callable[[dict[str, Any], ToolContext], Awaitable[ToolResult]]

_SEARCH_KNOWLEDGE_PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "required": ["query"],
    "properties": {
        "query": {"type": "string"},
        "domain": {"type": "string"},
    },
}


@dataclass(frozen=True)
class PackTool:
    tool_def: ToolDef
    description: str
    params: dict[str, Any]
    result_fields: tuple[str, ...]
    result_hint: str
    display_hint: dict[str, str]
    confirm: dict[str, str] | None = None
    success_message: dict[str, str] | None = None
    resolve_for_confirm: str | None = None


@dataclass(frozen=True)
class CoreTool:
    name: str
    description: str
    params: dict[str, Any]
    handler: CoreHandler


async def _set_preferred_language(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    return ToolResult(status="ok", data={"language": args["language"]})


async def _end_conversation(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    return ToolResult(status="ok")


def _find_matching_records(
    data: dict[str, Any], args: dict[str, Any]
) -> tuple[dict[str, Any], int]:
    """Generic correlation for resolve_for_confirm: find the item (inside any top-level list of
    dicts in the resolved data) whose same-named fields equal the write's string args — ids like
    "B-9", never numbers, which match by coincidence. Returns the top-level data merged with the
    matched item, and how many items matched (only exactly 1 is usable; 0 keeps the top level).
    Stays domain-agnostic — no field names are hardcoded."""
    id_args = {k: v for k, v in args.items() if isinstance(v, str)}
    matched: list[dict[str, Any]] = []
    for field_value in data.values():
        if not isinstance(field_value, list):
            continue
        for item in field_value:
            if not isinstance(item, dict):
                continue
            shared = [k for k in id_args if k in item]
            if shared and all(item[k] == id_args[k] for k in shared):
                matched.append(item)
    if len(matched) != 1:
        return dict(data), len(matched)
    return {**data, **matched[0]}, 1


@dataclass(frozen=True)
class ConfirmResolution:
    """What the confirmation question is rendered from, and the args to store for execution.
    `pinned_args` replaces each arg with the resolved record's same-named value (e.g. a
    'latest' sentinel ref becomes the concrete id), so what executes is what the user heard."""

    fields: dict[str, Any]
    pinned_args: dict[str, Any]
    ambiguous: bool = False  # several records matched: the confirmation can't name just one


def render_confirm_template(template: str, fields: dict[str, Any]) -> str:
    try:
        return template.format(**fields)
    except KeyError as exc:
        raise ValueError(f"confirm template references unknown field {exc}") from exc


class ToolRegistry:
    def __init__(
        self,
        pack: LoadedPack,
        embeddings: EmbeddingProvider | None = None,
        knowledge_store: KnowledgeStore | None = None,
    ) -> None:
        self._pack_id = pack.id
        self._embeddings = embeddings
        self._knowledge_store = knowledge_store
        self._core_tools: dict[str, CoreTool] = {
            "search_knowledge": CoreTool(
                name="search_knowledge",
                description="Search the app's curated help content for how-to questions.",
                params=_SEARCH_KNOWLEDGE_PARAMS,
                handler=self._search_knowledge,
            ),
            "set_preferred_language": CoreTool(
                name="set_preferred_language",
                description=(
                    "Switch the language the assistant replies in for the rest of "
                    "this conversation."
                ),
                params={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["language"],
                    "properties": {"language": {"type": "string", "enum": list(pack.languages)}},
                },
                handler=_set_preferred_language,
            ),
            "end_conversation": CoreTool(
                name="end_conversation",
                description="End the conversation when the user is done and says goodbye.",
                params={"type": "object", "additionalProperties": False, "properties": {}},
                handler=_end_conversation,
            ),
        }
        self._pack_tools: dict[str, PackTool] = {}

        for raw in pack.tools:
            name = raw["name"]
            if name in self._core_tools:
                raise ValueError(f"pack tool '{name}' collides with a core built-in tool name")
            params = raw["params"]
            assert_no_identity_fields(params, name)
            handler = raw["handler"]
            self._pack_tools[name] = PackTool(
                tool_def=ToolDef(
                    name=name,
                    kind=raw["kind"],
                    handler_type=handler["type"],
                    config={k: v for k, v in handler.items() if k != "type"},
                ),
                description=raw["description"],
                params=params,
                result_fields=tuple(raw.get("result_fields") or ()),
                result_hint=raw.get("result_hint", ""),
                display_hint=dict(raw.get("display_hint") or {}),
                confirm=dict(raw["confirm"]) if "confirm" in raw else None,
                success_message=(
                    dict(raw["success_message"]) if "success_message" in raw else None
                ),
                resolve_for_confirm=raw.get("resolve_for_confirm"),
            )

    def tool_specs(self) -> list[ToolSpec]:
        specs = [
            ToolSpec(name=t.name, description=t.description, parameters=t.params)
            for t in self._core_tools.values()
        ]
        specs += [
            ToolSpec(name=name, description=t.description, parameters=t.params)
            for name, t in self._pack_tools.items()
        ]
        return specs

    def get(self, name: str) -> PackTool:
        return self._pack_tools[name]

    def has_pack_tool(self, name: str) -> bool:
        return name in self._pack_tools

    def is_core_tool(self, name: str) -> bool:
        return name in self._core_tools

    def is_write(self, name: str) -> bool:
        return name in self._pack_tools and self._pack_tools[name].tool_def.kind == "write"

    async def dispatch(
        self, name: str, args: dict[str, Any], ctx: ToolContext, handler: HostToolHandler
    ) -> ToolResult:
        if name in self._core_tools:
            core_tool = self._core_tools[name]
            errors = validate_args(core_tool.params, args)
            if errors:
                return ToolResult(
                    status="invalid", error_code="SCHEMA_INVALID", data={"errors": errors}
                )
            return await core_tool.handler(args, ctx)

        pack_tool = self._pack_tools[name]
        errors = validate_args(pack_tool.params, args)
        if errors:
            return ToolResult(
                status="invalid", error_code="SCHEMA_INVALID", data={"errors": errors}
            )

        result = await handler.call(pack_tool.tool_def, args, ctx)
        if result.status == "ok" and result.data and pack_tool.result_fields:
            trimmed = {k: v for k, v in result.data.items() if k in pack_tool.result_fields}
            result = ToolResult(
                status=result.status,
                data=trimmed,
                error_code=result.error_code,
                user_message_key=result.user_message_key,
                client_actions=result.client_actions,
            )
        return result

    def validate(self, name: str, args: dict[str, Any]) -> list[str]:
        tool = self._core_tools.get(name) or self._pack_tools[name]
        return validate_args(tool.params, args)

    async def resolve_confirm_fields(
        self, write_tool_name: str, args: dict[str, Any], ctx: ToolContext, handler: HostToolHandler
    ) -> ConfirmResolution:
        pack_tool = self._pack_tools[write_tool_name]
        if not pack_tool.resolve_for_confirm:
            return ConfirmResolution(fields=dict(args), pinned_args=dict(args))

        resolve_tool = self._pack_tools[pack_tool.resolve_for_confirm]
        resolve_args = {
            k: v for k, v in args.items() if k in (resolve_tool.params.get("properties") or {})
        }
        result = await self.dispatch(pack_tool.resolve_for_confirm, resolve_args, ctx, handler)
        if result.status != "ok" or not result.data:
            return ConfirmResolution(fields=dict(args), pinned_args=dict(args))

        record, matches = _find_matching_records(result.data, args)
        # Pin only what the resolver was asked about (e.g. a 'latest' ref it resolved), so no
        # other user-supplied value can be silently swapped for a record's field.
        pinned = {
            k: record[k]
            if k in resolve_args and isinstance(record.get(k), str | int | float)
            else v
            for k, v in args.items()
        }
        return ConfirmResolution(
            fields={**record, **pinned}, pinned_args=pinned, ambiguous=matches > 1
        )

    async def _search_knowledge(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if self._embeddings is None or self._knowledge_store is None:
            return ToolResult(status="ok", data={"chunks": []})

        from voice_core.kb.retriever import search_knowledge

        domain = args.get("domain")
        try:
            chunks = await search_knowledge(
                query=args["query"],
                pack_id=self._pack_id,
                language=ctx.language,
                embeddings=self._embeddings,
                store=self._knowledge_store,
                domains=[domain] if domain else None,
            )
        except Exception:
            logging.getLogger(__name__).warning("search_knowledge tool call failed", exc_info=True)
            return ToolResult(status="error", error_code="KNOWLEDGE_STORE_UNAVAILABLE")
        return ToolResult(
            status="ok",
            data={
                "chunks": [
                    {
                        "source": f"{c.doc_slug}@v{c.doc_version}",
                        "text": c.text,
                        "similarity": c.similarity,
                    }
                    for c in chunks
                ]
            },
        )
