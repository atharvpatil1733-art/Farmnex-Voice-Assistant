"""Make assistant text safe and natural to *speak* (golden rule 6). Captions keep the original
text; only TTS input goes through here. Deterministic and table-driven per language
(`speech/locale/`), so every mispronunciation fix is a table entry plus a test.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from datetime import date

from voice_core.speech.locale import get_locale
from voice_core.speech.locale.base import SpeechLocale
from voice_core.speech.units import SpokenUnit

_NUM = r"\d[\d,]*(?:\.\d+)?"

_MD_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)]+)\)")
# Any scheme (http, ftp, …), www., emails and bare domains: never read out (tool data from the
# host is untrusted and may contain them).
_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+|\bwww\.\S+", re.IGNORECASE)
_EMAIL = re.compile(r"\S+@\S+")
_DOMAIN = re.compile(
    r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|in|org|net|io|co|app|ai|gov|edu|info)\b(?:/\S*)?",
    re.IGNORECASE,
)
_MD_MARKS = re.compile(r"\*\*|__|`+|^#+\s*", re.MULTILINE)
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+", re.MULTILINE)
_ISO_DATETIME = re.compile(
    r"\b(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?"
)
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_TIME = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
_MONEY = re.compile(rf"(?:₹|\bRs\.?)\s*({_NUM})", re.IGNORECASE)
_SLASH_DASH = re.compile(rf"({_NUM})\s*/-")
_BRACKETED_ID = re.compile(r"\s*\(\s*[A-Z]{1,3}-?\d+\s*\)")
_PERCENT = re.compile(rf"({_NUM})\s*%")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.!?।॥:;])")
_TERMINAL = tuple(".?!।॥:,;")
# Brackets/markup TTS would read out, plus the emoji variation selector (U+FE0F).
# Not ZWJ/ZWNJ: Marathi spellings like the eyelash-ra need them.
_STRIP_CHARS = "()[]{}<>*#_~|^" + chr(0x5C) + chr(0xFE0F)


def normalize_for_speech(
    text: str,
    language: str,
    *,
    today: date | None = None,
    units: Sequence[SpokenUnit] = (),
) -> str:
    loc = get_locale(language)
    text = _MD_LINK.sub(r"\1", text)
    text = _URL.sub("", text)
    text = _EMAIL.sub("", text)
    text = _DOMAIN.sub("", text)
    text = _MD_MARKS.sub("", text)
    text = _join_lines(_BULLET.sub("", text), loc)

    text = _ISO_DATETIME.sub(lambda m: _spoken_datetime(m, loc, today), text)
    text = _ISO_DATE.sub(lambda m: _spoken_date(m, loc, today), text)
    text = _TIME.sub(lambda m: loc.time(int(m.group(1)), int(m.group(2))), text)

    text = _MONEY.sub(lambda m: loc.currency(_plain(m.group(1))), text)
    text = _SLASH_DASH.sub(lambda m: loc.currency(_plain(m.group(1))), text)
    text = _apply_units(text, units, language, loc)

    text = _BRACKETED_ID.sub("", text)
    text = _PERCENT.sub(lambda m: f"{_plain(m.group(1))} {loc.percent}", text)
    text = text.replace("&", f" {loc.and_word} ")
    text = _strip_symbols(text)
    text = re.sub(r"\s+", " ", text).strip()
    return _SPACE_BEFORE_PUNCT.sub(r"\1", text)


def _apply_units(text: str, units: Sequence[SpokenUnit], language: str, loc: SpeechLocale) -> str:
    for unit in units:
        forms = unit.forms(language)
        if forms is None:
            continue
        alias = "|".join(re.escape(a) for a in sorted(unit.aliases, key=len, reverse=True))
        # "/unit" or "per unit" after an amount -> "per <unit>" phrase
        text = re.sub(
            rf"\s*(?:/|\bper\s+)\s*(?:{alias})\b",
            f" {loc.per_unit(forms[0])}",
            text,
            flags=re.IGNORECASE,
        )

        # "500<alias>" / "500 <alias>" -> "500 <spoken unit>"; a bare alias -> the unit word.
        # One pass, so an already-spoken plural ("kilos") is never rewritten again.
        def spoken(m: re.Match[str], f: tuple[str, str] = forms) -> str:
            return loc.count(_plain(m.group(1)), f) if m.group(1) else f[0]

        text = re.sub(
            rf"({_NUM})\s*(?:{alias})\b|\b(?:{alias})\b", spoken, text, flags=re.IGNORECASE
        )
    return text


def _plain(number: str) -> str:
    return number.replace(",", "")


def _spoken_date_parts(
    year: int, month: int, day: int, loc: SpeechLocale, today: date | None
) -> str:
    try:
        when = date(year, month, day)
    except ValueError:
        return f"{year}-{month:02d}-{day:02d}"
    if today is not None:
        delta = (when - today).days
        if delta == 0:
            return loc.today
        if delta == 1:
            return loc.tomorrow
        if delta == -1:
            return loc.yesterday
    spoken = f"{when.day} {loc.months[when.month - 1]}"
    if today is None or when.year != today.year:
        spoken += f" {when.year}"
    return spoken


def _spoken_date(match: re.Match[str], loc: SpeechLocale, today: date | None) -> str:
    return _spoken_date_parts(
        int(match.group(1)), int(match.group(2)), int(match.group(3)), loc, today
    )


def _spoken_datetime(match: re.Match[str], loc: SpeechLocale, today: date | None) -> str:
    day = _spoken_date_parts(
        int(match.group(1)), int(match.group(2)), int(match.group(3)), loc, today
    )
    return f"{day} {loc.time(int(match.group(4)), int(match.group(5)))}"


def _join_lines(text: str, loc: SpeechLocale) -> str:
    """List items / lines become separate spoken sentences."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    joined = ""
    for line in lines:
        if joined and not joined.endswith(_TERMINAL):
            joined += loc.sentence_end
        joined += (" " if joined else "") + line
    return joined


def _strip_symbols(text: str) -> str:
    """Drop every Unicode symbol (emoji, currency and math signs left after the rewrites above),
    brackets and markup characters that TTS would read out or mangle."""
    kept = []
    for ch in text:
        category = unicodedata.category(ch)
        if ch in _STRIP_CHARS or category.startswith("S") or category in ("Cs", "Co"):
            kept.append(" ")
        else:
            kept.append(ch)
    return "".join(kept)
