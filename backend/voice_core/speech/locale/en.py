from __future__ import annotations

from voice_core.speech.locale.base import SpeechLocale


def _plural(amount: str, forms: tuple[str, str]) -> str:
    return f"{amount} {forms[0] if amount == '1' else forms[1]}"


def _period(hour: int) -> str:
    if 4 <= hour <= 11:
        return "in the morning"
    if 12 <= hour <= 15:
        return "in the afternoon"
    if 16 <= hour <= 19:
        return "in the evening"
    return "at night"


def _time(hour: int, minute: int) -> str:
    h12 = hour % 12 or 12
    clock = f"{h12}" if minute == 0 else f"{h12}:{minute:02d}"
    return f"{clock} {_period(hour)}"


LOCALE = SpeechLocale(
    currency=lambda amount: _plural(amount, ("rupee", "rupees")),
    per_unit=lambda unit: f"per {unit}",
    count=_plural,
    months=(
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
    today="today",
    tomorrow="tomorrow",
    yesterday="yesterday",
    time=_time,
    percent="percent",
    and_word="and",
    sentence_end=".",
)
