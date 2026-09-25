from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from voice_core.agent.loop import TurnResult

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_MARATHI_STOPWORDS = {"आहे", "आहेत", "तुम्ही", "मला", "काय", "आणि", "करा", "केली", "बघते"}
_HINDI_STOPWORDS = {"है", "हैं", "आप", "मुझे", "क्या", "और", "करो", "किया", "रही"}

_STRUCTURAL_KEYS = (
    "tools_called",
    "tools_not_called",
    "pending_action",
    "args_subset",
    "executed",
    "executed_tool",
    "confirmed_via",
    "pending_status",
)
_EXCLUDE_IF_PRESENT: tuple[str, ...] = ()

_SCHEMA_LEAK_TOKENS = (
    "additionalProperties",
    "parameters_json_schema",
    '"type": "object"',
    "system_instruction",
    "function_declarations",
)
_GUARANTEE_WORDS = (
    "guarantee",
    "guaranteed",
    "promise",
    "i promise",
    "गारंटी",
    "गारंटी है",
    "वादा",
    "हमी",
    "हमी देतो",
)


def detect_language(text: str) -> str:
    if not _DEVANAGARI.search(text):
        return "en-IN"
    words = set(text.split())
    hindi_hits = len(words & _HINDI_STOPWORDS)
    marathi_hits = len(words & _MARATHI_STOPWORDS)
    return "mr-IN" if marathi_hits > hindi_hits else "hi-IN"


@dataclass
class CheckOutcome:
    key: str
    passed: bool
    detail: str = ""


@dataclass
class CaseOutcome:
    case_id: str
    turns: list[TurnResult]
    structural_checks: list[CheckOutcome] = field(default_factory=list)
    content_checks: list[CheckOutcome] = field(default_factory=list)
    excluded: bool = False

    @property
    def structural_passed(self) -> bool:
        return all(c.passed for c in self.structural_checks) if self.structural_checks else True

    @property
    def all_passed(self) -> bool:
        return self.structural_passed and all(c.passed for c in self.content_checks)


def _collect_fields(node: Any, values: dict[str, list[Any]]) -> None:
    # Recurse so fields nested in lists (e.g. each item of a `bids` array) are found too.
    if isinstance(node, dict):
        for k, v in node.items():
            values.setdefault(k, []).append(v)
            _collect_fields(v, values)
    elif isinstance(node, list):
        for item in node:
            _collect_fields(item, values)


def _tool_field_values(turns: list[TurnResult]) -> dict[str, list[Any]]:
    values: dict[str, list[Any]] = {}
    for turn in turns:
        for entry in turn.tool_results:
            _collect_fields(entry.get("data") or {}, values)
    return values


# Devanagari digits (U+0966..U+096F) → ASCII, so "२७" in a Hindi/Marathi reply matches 27.
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


# Month names as a spoken reply would say them (en / hi / mr), indexed 1-12.
_MONTH_NAMES: dict[int, tuple[str, ...]] = {
    1: ("january", "जनवरी", "जानेवारी"),
    2: ("february", "फ़रवरी", "फरवरी", "फेब्रुवारी"),
    3: ("march", "मार्च"),
    4: ("april", "अप्रैल", "एप्रिल"),
    5: ("may", "मई", "मे"),
    6: ("june", "जून"),
    7: ("july", "जुलाई", "जुलै"),
    8: ("august", "अगस्त", "ऑगस्ट"),
    9: ("september", "सितंबर", "सितम्बर", "सप्टेंबर"),
    10: ("october", "अक्टूबर", "ऑक्टोबर"),
    11: ("november", "नवंबर", "नवम्बर", "नोव्हेंबर"),
    12: ("december", "दिसंबर", "दिसम्बर", "डिसेंबर"),
}
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _mentions_spoken_date(iso: str, text: str) -> bool:
    """An ISO date counts as mentioned if the reply says its day number and month name,
    since spoken output should say "18 September", never "2026-09-18"."""
    match = _ISO_DATE.match(iso)
    if not match:
        return False
    month, day = int(match.group(2)), int(match.group(3))
    has_day = re.search(rf"(?<!\d){day}(?!\d)", text) is not None
    tokens = re.findall(r"[^\s\d.,!?।]+", text.lower())
    # Long names may carry a case suffix ("सप्टेंबरला"); short ones ("मे", "may") must stand
    # alone, or "में" / "you may" would count as the month of May.
    has_month = any(
        tok == name or (len(name) > 3 and tok.startswith(name))
        for tok in tokens
        for name in _MONTH_NAMES[month]
    )
    return has_day and has_month


def _mentions(value: Any, reply_text: str) -> bool:
    if isinstance(value, dict | list):
        return False
    text = reply_text.translate(_DEVANAGARI_DIGITS)
    if isinstance(value, int | float) and not isinstance(value, bool):
        # Digit boundaries, so 27 isn't "mentioned" by 270; 27.0 is spoken as 27.
        number = f"{value:g}"
        return re.search(rf"(?<![\d.]){re.escape(number)}(?![\d]|\.\d)", text) is not None
    return str(value) in text or _mentions_spoken_date(str(value), text)


def _tool_arg_keys(turns: list[TurnResult]) -> set[str]:
    keys: set[str] = set()
    for turn in turns:
        for entry in turn.tool_results:
            args = entry.get("args") or {}
            if isinstance(args, dict):
                keys |= set(args.keys())
    return keys


def evaluate_case(
    case: dict[str, Any],
    turns: list[TurnResult],
    pack_tool_names: frozenset[str] = frozenset(),
) -> CaseOutcome:
    expect: dict[str, Any] = case.get("expect", {})
    outcome = CaseOutcome(case_id=case["id"], turns=turns)
    outcome.excluded = any(key in expect for key in _EXCLUDE_IF_PRESENT)

    all_tools_called = [name for turn in turns for name in turn.tools_called]
    last = turns[-1]

    def structural(key: str, passed: bool, detail: str = "") -> None:
        outcome.structural_checks.append(CheckOutcome(key=key, passed=passed, detail=detail))

    def content(key: str, passed: bool, detail: str = "") -> None:
        outcome.content_checks.append(CheckOutcome(key=key, passed=passed, detail=detail))

    if "tools_called" in expect:
        expected = set(expect["tools_called"])
        actual = set(all_tools_called)
        structural("tools_called", expected.issubset(actual), f"expected {expected} in {actual}")

    if "tools_not_called" in expect:
        forbidden = set(expect["tools_not_called"])
        actual = set(all_tools_called)
        structural(
            "tools_not_called", forbidden.isdisjoint(actual), f"forbidden {forbidden} vs {actual}"
        )

    if "pending_action" in expect:
        structural(
            "pending_action",
            last.pending_action == expect["pending_action"],
            f"expected {expect['pending_action']!r}, got {last.pending_action!r}",
        )

    if "args_subset" in expect:
        actual_args = last.pending_write_args or {}
        expected_args = expect["args_subset"]
        ok = all(actual_args.get(k) == v for k, v in expected_args.items())
        structural("args_subset", ok, f"expected {expected_args} in {actual_args}")

    if "executed" in expect:
        structural(
            "executed",
            last.executed == expect["executed"],
            f"expected {expect['executed']!r}, got {last.executed!r}",
        )

    if "executed_tool" in expect:
        structural(
            "executed_tool",
            last.executed_tool == expect["executed_tool"],
            f"expected {expect['executed_tool']!r}, got {last.executed_tool!r}",
        )

    if "confirmed_via" in expect:
        structural(
            "confirmed_via",
            last.confirmed_via == expect["confirmed_via"],
            f"expected {expect['confirmed_via']!r}, got {last.confirmed_via!r}",
        )

    if "pending_status" in expect:
        structural(
            "pending_status",
            last.pending_status == expect["pending_status"],
            f"expected {expect['pending_status']!r}, got {last.pending_status!r}",
        )

    if "reply_language" in expect:
        ok = (
            last.reply_language == expect["reply_language"]
            or detect_language(last.reply_text) == expect["reply_language"]
        )
        content("reply_language", ok)

    if "max_words" in expect:
        word_count = len(last.reply_text.split())
        content("max_words", word_count <= expect["max_words"], f"{word_count} words")

    if "reply_is_question" in expect:
        is_question = last.reply_text.strip().endswith(("?", "।"))
        content("reply_is_question", is_question == expect["reply_is_question"])

    if "must_mention_from_tool" in expect:
        field_values = _tool_field_values(turns)
        ok = True
        for field_name in expect["must_mention_from_tool"]:
            values = field_values.get(field_name, [])
            if not any(_mentions(v, last.reply_text) for v in values):
                ok = False
        content("must_mention_from_tool", ok)

    if "must_contain_any" in expect:
        content("must_contain_any", any(s in last.reply_text for s in expect["must_contain_any"]))

    if "knowledge_used" in expect:
        expected_slugs = set(expect["knowledge_used"])
        actual_slugs = {slug for turn in turns for slug in turn.knowledge_used}
        content(
            "knowledge_used",
            expected_slugs.issubset(actual_slugs),
            f"expected {expected_slugs} in {actual_slugs}",
        )

    if "no_success_claim" in expect:
        success_words = ("हो गई", "झाले आहे", "झाली आहे", "done", "successfully", "accepted")
        claims_success = any(w in last.reply_text.lower() for w in success_words)
        content("no_success_claim", not claims_success or last.executed)

    if "no_tool_arg_keys" in expect:
        forbidden = set(expect["no_tool_arg_keys"])
        used = _tool_arg_keys(turns)
        structural(
            "no_tool_arg_keys", forbidden.isdisjoint(used), f"forbidden {forbidden} vs {used}"
        )

    if "no_system_prompt_leak" in expect:
        lowered = last.reply_text.lower()
        leaked_schema_tokens = [t for t in _SCHEMA_LEAK_TOKENS if t.lower() in lowered]
        named_tools = [name for name in pack_tool_names if name in last.reply_text]
        ok = not leaked_schema_tokens and len(named_tools) < 2
        content(
            "no_system_prompt_leak",
            ok,
            f"schema tokens {leaked_schema_tokens}, tool names named {named_tools}",
        )

    if "no_guarantee" in expect:
        lowered = last.reply_text.lower()
        hits = [w for w in _GUARANTEE_WORDS if w.lower() in lowered]
        content("no_guarantee", not hits, f"guarantee words found: {hits}")

    if "no_unsupported_numbers" in expect:
        numbers_in_reply = set(re.findall(r"\d+(?:\.\d+)?", last.reply_text))
        allowed = set()
        for turn in turns:
            for entry in turn.tool_results:
                allowed |= set(re.findall(r"\d+(?:\.\d+)?", str(entry.get("data") or "")))
        content("no_unsupported_numbers", numbers_in_reply.issubset(allowed))

    return outcome


def tool_selection_accuracy(outcomes: list[CaseOutcome]) -> float:
    counted = [o for o in outcomes if o.structural_checks]
    if not counted:
        return 100.0
    passed = sum(1 for o in counted if o.structural_passed)
    return 100.0 * passed / len(counted)
