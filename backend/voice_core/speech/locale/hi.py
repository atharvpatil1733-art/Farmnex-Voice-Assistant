from __future__ import annotations

from voice_core.speech.locale.base import SpeechLocale

_HOURS = ("बारह", "एक", "दो", "तीन", "चार", "पाँच", "छह", "सात", "आठ", "नौ", "दस", "ग्यारह")


def _period(hour: int) -> str:
    if 4 <= hour <= 11:
        return "सुबह"
    if 12 <= hour <= 15:
        return "दोपहर"
    if 16 <= hour <= 19:
        return "शाम"
    return "रात"


def _time(hour: int, minute: int) -> str:
    h12 = hour % 12
    word = _HOURS[h12]
    if minute == 0:
        spoken = f"{word} बजे"
    elif minute == 30:
        special = {1: "डेढ़", 2: "ढाई"}.get(h12)
        spoken = f"{special} बजे" if special else f"साढ़े {word} बजे"
    elif minute == 15:
        spoken = f"सवा {word} बजे"
    elif minute == 45:
        spoken = f"पौने {_HOURS[(h12 + 1) % 12]} बजे"
    else:
        spoken = f"{word} बजकर {minute} मिनट"
    return f"{_period(hour)} {spoken}"


LOCALE = SpeechLocale(
    currency=lambda amount: f"{amount} रुपये",
    per_unit=lambda unit: unit,
    count=lambda amount, forms: f"{amount} {forms[0]}",
    months=(
        "जनवरी",
        "फ़रवरी",
        "मार्च",
        "अप्रैल",
        "मई",
        "जून",
        "जुलाई",
        "अगस्त",
        "सितंबर",
        "अक्टूबर",
        "नवंबर",
        "दिसंबर",
    ),
    today="आज",
    tomorrow="कल",
    yesterday="कल",
    time=_time,
    percent="प्रतिशत",
    and_word="और",
    sentence_end="।",
)
