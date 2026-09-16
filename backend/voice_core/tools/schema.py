from __future__ import annotations

import re
from typing import Any

import jsonschema

_IDENTITY_FIELD_PATTERN = re.compile(
    r"^(user_id|owner_id|.*_user_ref)$|^[a-z0-9]+_id$", re.IGNORECASE
)


def validate_args(schema: dict[str, Any], args: dict[str, Any]) -> list[str]:
    validator = jsonschema.Draft202012Validator(schema)
    return [error.message for error in validator.iter_errors(args)]


def assert_no_identity_fields(schema: dict[str, Any], tool_name: str) -> None:
    properties = schema.get("properties") or {}
    for field_name in properties:
        if _IDENTITY_FIELD_PATTERN.match(field_name):
            raise ValueError(
                f"tool '{tool_name}' declares identity-like field '{field_name}' in its schema; "
                "identity must come from ctx.user_ref, never an LLM-supplied argument"
            )
