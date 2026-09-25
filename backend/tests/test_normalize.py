from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from voice_core.packs.loader import load_pack
from voice_core.speech.normalize import normalize_for_speech

TODAY = date(2026, 9, 25)
PACKS_ROOT = Path(__file__).resolve().parents[2] / "domain_packs"
UNITS = load_pack("farm_marketplace", PACKS_ROOT).speech_units


def n(text: str, language: str) -> str:
    return normalize_for_speech(text, language, today=TODAY, units=UNITS)


def test_units_are_left_alone_when_the_pack_defines_none() -> None:
    assert normalize_for_speech("500 kg", "en-IN", units=()) == "500 kg"


@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        ("₹27/kg", "hi-IN", "27 रुपये किलो"),
        ("₹27/kg", "mr-IN", "27 रुपये किलो"),
        ("₹27/kg", "en-IN", "27 rupees per kilo"),
        ("Rs. 40 per kg", "en-IN", "40 rupees per kilo"),
        ("Rs 1,200", "hi-IN", "1200 रुपये"),
        ("500/-", "mr-IN", "500 रुपये"),
        ("₹25.5", "en-IN", "25.5 rupees"),
    ],
)
def test_currency(text: str, language: str, expected: str) -> None:
    assert n(text, language) == expected


@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        ("500kg प्याज़", "hi-IN", "500 किलो प्याज़"),
        ("500 kg", "mr-IN", "500 किलो"),
        ("5 qtl", "hi-IN", "5 क्विंटल"),
        ("5 quintal", "en-IN", "5 quintals"),
        ("1 quintal", "en-IN", "1 quintal"),
        ("2 tons", "en-IN", "2 tonnes"),
        ("1200 kg soybean", "en-IN", "1200 kilos soybean"),
    ],
)
def test_units(text: str, language: str, expected: str) -> None:
    assert n(text, language) == expected


@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        ("2026-09-18", "hi-IN", "18 सितंबर"),
        ("2026-09-18", "mr-IN", "18 सप्टेंबर"),
        ("2026-09-18", "en-IN", "18 September"),
        ("2026-09-25", "hi-IN", "आज"),
        ("2026-09-26", "mr-IN", "उद्या"),
        ("2026-09-26", "en-IN", "tomorrow"),
        ("2027-01-05", "en-IN", "5 January 2027"),
    ],
)
def test_dates(text: str, language: str, expected: str) -> None:
    assert n(text, language) == expected


@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        ("10:30", "hi-IN", "सुबह साढ़े दस बजे"),
        ("09:00", "hi-IN", "सुबह नौ बजे"),
        ("18:00", "hi-IN", "शाम छह बजे"),
        ("13:15", "hi-IN", "दोपहर सवा एक बजे"),
        ("10:45", "hi-IN", "सुबह पौने ग्यारह बजे"),
        ("01:30", "hi-IN", "रात डेढ़ बजे"),
        ("14:30", "hi-IN", "दोपहर ढाई बजे"),
        ("10:20", "hi-IN", "सुबह दस बजकर 20 मिनट"),
        ("10:30", "mr-IN", "सकाळी साडेदहा वाजता"),
        ("14:30", "mr-IN", "दुपारी अडीच वाजता"),
        ("19:00", "mr-IN", "संध्याकाळी सात वाजता"),
        ("10:30", "en-IN", "10:30 in the morning"),
        ("18:00", "en-IN", "6 in the evening"),
    ],
)
def test_times(text: str, language: str, expected: str) -> None:
    assert n(text, language) == expected


def test_iso_datetime_becomes_spoken_date_and_time() -> None:
    assert n("2026-09-19T18:00:00+05:30", "hi-IN") == "19 सितंबर शाम छह बजे"


def test_markdown_emoji_and_bullets_are_removed() -> None:
    text = "**Great!** 🎉 Here it is:\n- first\n- second"
    assert n(text, "en-IN") == "Great! Here it is: first. second"


def test_urls_and_markdown_links_are_not_read_out() -> None:
    assert n("See https://example.com/x now", "en-IN") == "See now"
    assert n("Open [the help page](https://example.com/help)", "en-IN") == "Open the help page"


def test_hindi_bullets_join_with_danda() -> None:
    assert n("- पहला\n- दूसरा", "hi-IN") == "पहला। दूसरा"


def test_bracketed_ids_are_dropped_other_brackets_kept_as_words() -> None:
    assert n("आपकी लिस्टिंग (L-102) चालू है", "hi-IN") == "आपकी लिस्टिंग चालू है"
    assert n("pickup (return trip) is booked", "en-IN") == "pickup return trip is booked"


def test_percent_and_ampersand() -> None:
    assert n("10% more", "en-IN") == "10 percent more"
    assert n("10% ज़्यादा", "hi-IN") == "10 प्रतिशत ज़्यादा"
    assert n("10% जास्त", "mr-IN") == "10 टक्के जास्त"
    assert n("onion & tomato", "en-IN") == "onion and tomato"


def test_plain_sentence_is_unchanged() -> None:
    sentence = "आपके प्याज़ पर सबसे ऊँची बोली 27 रुपये किलो है।"
    assert n(sentence, "hi-IN") == sentence


def test_unknown_language_falls_back_to_english_tables() -> None:
    assert n("₹5", "ta-IN") == "5 rupees"


LEAKS = [
    "Price in ₹ today",
    "Cost $5 or €3",
    "2 + 2 = 4 → done",
    "Rate ≥ 1500",
    "10 × 20",
    "Visit farmnex.in or mail help@farmnex.in",
    "ftp://x.y/z and https://a.b/c?d=1",
    "Open www.example.com/help now",
    "Wheat kg bhav",
    "₹27/kg & 10% off!! 🎉🌾 **bold** `code` #tag",
    "आपकी बोली ₹27/kg है (L-102) → देखें https://x.in",
]


@pytest.mark.parametrize("text", LEAKS)
@pytest.mark.parametrize("language", ["hi-IN", "mr-IN", "en-IN"])
def test_nothing_unspeakable_reaches_tts(text: str, language: str) -> None:
    """Golden rule 6 as a property: no Unicode symbol, URL scheme, bare domain or @ survives."""
    import unicodedata

    out = n(text, language)
    assert not [ch for ch in out if unicodedata.category(ch).startswith("S")], out
    assert "://" not in out and "@" not in out and "www." not in out, out
    assert ".in" not in out and ".com" not in out, out


def test_bare_unit_alias_is_spoken() -> None:
    assert n("Wheat kg bhav", "en-IN") == "Wheat kilo bhav"
    assert n("कांदा kg भाव", "mr-IN") == "कांदा किलो भाव"
