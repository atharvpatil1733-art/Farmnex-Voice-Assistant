from __future__ import annotations

from voice_core.agent.language import LanguageTracker

LANGS = ("hi-IN", "mr-IN", "en-IN")


def tracker(language: str = "hi-IN") -> LanguageTracker:
    return LanguageTracker(session_language=language, supported=LANGS)


def test_two_confident_multiword_turns_switch_the_session() -> None:
    t = tracker()
    assert t.observe("What is my highest offer today", "en-IN", 0.95) is None
    assert t.observe("And when will the truck come", "en-IN", 0.92) == "en-IN"
    assert t.session_language == "en-IN"


def test_one_turn_is_not_enough() -> None:
    t = tracker()
    assert t.observe("What is my highest offer", "en-IN", 0.95) is None
    assert t.session_language == "hi-IN"


def test_streak_resets_when_the_user_returns_to_the_session_language() -> None:
    t = tracker()
    t.observe("What is my highest offer", "en-IN", 0.95)
    t.observe("मेरी बोली कितनी आई है", "hi-IN", 0.95)
    assert t.observe("And when will the truck come", "en-IN", 0.95) is None
    assert t.session_language == "hi-IN"


def test_short_utterances_never_switch() -> None:
    """Single English words inside Hindi/Marathi speech ("OK", "payment") never switch."""
    t = tracker()
    for _ in range(3):
        assert t.observe("OK thanks", "en-IN", 0.99) is None
    assert t.session_language == "hi-IN"


def test_low_confidence_never_switches() -> None:
    t = tracker("mr-IN")
    for _ in range(3):
        assert t.observe("माझा माल कधी जाणार आहे", "hi-IN", 0.6) is None
    assert t.session_language == "mr-IN"


def test_code_mixed_devanagari_is_not_taken_as_english() -> None:
    """Hinglish transcribed in Devanagari with a few English words stays in the session
    language even if the detector says en-IN."""
    t = tracker()
    for _ in range(3):
        assert t.observe("मेरा payment कब आएगा भाई", "en-IN", 0.9) is None
    assert t.session_language == "hi-IN"


def test_different_languages_in_a_row_do_not_add_up() -> None:
    t = tracker()
    t.observe("What is my highest offer", "en-IN", 0.95)
    assert t.observe("माझा माल कधी जाणार आहे", "mr-IN", 0.95) is None
    assert t.session_language == "hi-IN"


def test_unsupported_detected_language_is_ignored() -> None:
    t = tracker()
    for _ in range(3):
        assert t.observe("நான் எப்போது பணம் பெறுவேன்", "ta-IN", 0.99) is None
    assert t.session_language == "hi-IN"


def test_explicit_set_resets_the_streak() -> None:
    t = tracker()
    t.observe("What is my highest offer", "en-IN", 0.95)
    t.set("mr-IN")
    assert t.session_language == "mr-IN"
    assert t.observe("What is my highest offer", "en-IN", 0.95) is None
