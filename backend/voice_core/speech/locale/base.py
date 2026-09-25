from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class SpeechLocale:
    """Everything language-specific the normalizer needs. One instance per language."""

    currency: Callable[[str], str]  # "27" -> "27 रुपये"
    per_unit: Callable[[str], str]  # unit singular -> "per <unit>" phrase ("किलो" / "per kilo")
    count: Callable[[str, tuple[str, str]], str]  # ("500", (singular, plural)) -> "500 <unit>"
    months: tuple[str, ...]  # 12 names, January first
    today: str
    tomorrow: str
    yesterday: str
    time: Callable[[int, int], str]  # (hour 0-23, minute) -> spoken time
    percent: str
    and_word: str
    sentence_end: str  # used when joining list items: "." or "।"
