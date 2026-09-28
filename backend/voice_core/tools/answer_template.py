"""Fixed-phrase answers: render a pack-provided sentence from a read tool's result, so common
questions skip the second LLM round (~1 s saved per voice turn).

Placeholders (nothing else is evaluated):
    {field} or {a.b}                 a value from the tool result
    {count(list)}                    number of items
    {max(list.f)} {min(list.f)}      over a field of the list's items
    {first(list.f)}                  the first item's field
    {expr|labels}                    map the value through the pack's per-language label set
    {expr|name}                      built-in: a short plain name (letters/spaces, <= 24)
Host data is untrusted and spoken without an LLM in between, so an unfiltered placeholder
only accepts numbers, ISO dates/times and HH:MM; free text needs an explicit filter.
Any placeholder that can't be resolved makes the whole render return None, and the turn falls
back to the normal LLM round — a template never speaks a half-filled sentence.
"""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import datetime, time
from typing import Any

MAX_VALUE_CHARS = 80  # host data is untrusted; never speak a long injected string
_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
_PATH = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*$")
_CALL = re.compile(r"^(count|max|min|first)\(([^()]+)\)$")
_SAFE_TEXT = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$"
    r"|^\d{1,2}:\d{2}$"
)
MAX_NAME_CHARS = 24


class _Unresolved(Exception):
    pass


def render_answer(
    template: str, data: dict[str, Any], labels: dict[str, dict[str, str]]
) -> str | None:
    """`labels` maps label-set name -> {raw value: spoken word} for the reply's language."""
    try:
        return _PLACEHOLDER.sub(lambda m: _render(m.group(1).strip(), data, labels), template)
    except _Unresolved:
        return None


def _render(expr: str, data: dict[str, Any], labels: dict[str, dict[str, str]]) -> str:
    source, _, filter_name = expr.partition("|")
    value = _evaluate(source.strip(), data)
    filter_name = filter_name.strip()
    if filter_name == "name":
        text = _text(value)
        if len(text) > MAX_NAME_CHARS or not _is_plain_name(text):
            raise _Unresolved
        return text
    if filter_name:
        spoken = labels.get(filter_name, {}).get(_text(value))
        if spoken is None:
            raise _Unresolved
        return spoken  # pack-authored words: trusted
    return _safe(value)


def _is_plain_name(text: str) -> bool:
    """Letters and combining marks (Devanagari vowel signs are marks, which regex \w misses),
    in words separated by single spaces. No digits, punctuation or symbols."""
    words = text.split(" ")
    return all(
        word and all(unicodedata.category(ch)[0] in ("L", "M") for ch in word) for word in words
    )


def _safe(value: Any) -> str:
    """Unfiltered values: numbers, ISO dates/times, HH:MM only."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise _Unresolved  # never speak "nan" / "inf"
        text = _text(value)
        if "e" in text.lower():
            raise _Unresolved  # exponent form ("1e+20") reads as nonsense
        return text
    if isinstance(value, str) and _SAFE_TEXT.match(value) and _is_real_date_or_time(value):
        return value
    raise _Unresolved


def _is_real_date_or_time(value: str) -> bool:
    """The regex checks shape; this rejects impossible values like 25:99 or 2026-13-40."""
    try:
        if "-" in value:
            datetime.fromisoformat(value)
        else:
            hours, minutes = value.split(":")
            time(int(hours), int(minutes))
    except ValueError:
        return False
    return True


def _evaluate(expr: str, data: dict[str, Any]) -> Any:
    call = _CALL.match(expr)
    if call:
        name, path = call.group(1), call.group(2).strip()
        if name == "count":
            items = _resolve(path, data)
            if not isinstance(items, list):
                raise _Unresolved
            return len(items)
        values = _collect(path, data)
        if not values:
            raise _Unresolved
        if name == "first":
            return values[0]
        numbers = [v for v in values if isinstance(v, int | float) and not isinstance(v, bool)]
        if len(numbers) != len(values):
            raise _Unresolved
        return max(numbers) if name == "max" else min(numbers)
    return _resolve(expr, data)


def _resolve(path: str, data: dict[str, Any]) -> Any:
    if not _PATH.match(path):
        raise _Unresolved
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise _Unresolved
        current = current[part]
    return current


def _collect(path: str, data: dict[str, Any]) -> list[Any]:
    """ "rows.price" -> [row["price"] for row in data["rows"]]."""
    if not _PATH.match(path) or "." not in path:
        raise _Unresolved
    list_path, _, field = path.rpartition(".")
    items = _resolve(list_path, data)
    if not isinstance(items, list):
        raise _Unresolved
    if not all(isinstance(item, dict) and field in item for item in items):
        raise _Unresolved  # never aggregate over a silent subset
    return [item[field] for item in items]


def _text(value: Any) -> str:
    if isinstance(value, bool) or value is None or isinstance(value, dict | list):
        raise _Unresolved
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value)
    if len(text) > MAX_VALUE_CHARS:
        raise _Unresolved
    return text
