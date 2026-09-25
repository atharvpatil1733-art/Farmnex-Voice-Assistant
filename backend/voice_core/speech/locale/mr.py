from __future__ import annotations

from voice_core.speech.locale.base import SpeechLocale

_HOURS = ("बारा", "एक", "दोन", "तीन", "चार", "पाच", "सहा", "सात", "आठ", "नऊ", "दहा", "अकरा")


def _period(hour: int) -> str:
    if 4 <= hour <= 11:
        return "सकाळी"
    if 12 <= hour <= 15:
        return "दुपारी"
    if 16 <= hour <= 19:
        return "संध्याकाळी"
    return "रात्री"


def _time(hour: int, minute: int) -> str:
    h12 = hour % 12
    word = _HOURS[h12]
    if minute == 0:
        spoken = f"{word} वाजता"
    elif minute == 30:
        special = {1: "दीड", 2: "अडीच"}.get(h12)
        spoken = f"{special} वाजता" if special else f"साडे{word} वाजता"
    elif minute == 15:
        spoken = f"सव्वा {word} वाजता"
    elif minute == 45:
        spoken = f"पावणे {_HOURS[(h12 + 1) % 12]} वाजता"
    else:
        spoken = f"{word} वाजून {minute} मिनिटांनी"
    return f"{_period(hour)} {spoken}"


LOCALE = SpeechLocale(
    currency=lambda amount: f"{amount} रुपये",
    per_unit=lambda unit: unit,
    count=lambda amount, forms: f"{amount} {forms[0]}",
    months=(
        "जानेवारी",
        "फेब्रुवारी",
        "मार्च",
        "एप्रिल",
        "मे",
        "जून",
        "जुलै",
        "ऑगस्ट",
        "सप्टेंबर",
        "ऑक्टोबर",
        "नोव्हेंबर",
        "डिसेंबर",
    ),
    today="आज",
    tomorrow="उद्या",
    yesterday="काल",
    time=_time,
    percent="टक्के",
    and_word="आणि",
    sentence_end="।",
)
