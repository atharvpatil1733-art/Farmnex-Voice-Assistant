from __future__ import annotations

import pytest

from voice_core.agent.loop import _match_lexicon, _normalize


@pytest.mark.parametrize(
    "utterance",
    ["हाँ", "हां", "हो", "होय", "ठीक है", "yes", "Yes.", "ok!", "OK", "confirm"],
)
def test_affirmations_are_matched(utterance: str) -> None:
    assert _match_lexicon(utterance, {}) == "yes"


@pytest.mark.parametrize("utterance", ["नाही", "नहीं", "नको", "no", "No!", "don't", "cancel"])
def test_refusals_are_matched(utterance: str) -> None:
    assert _match_lexicon(utterance, {}) == "no"


@pytest.mark.parametrize(
    "utterance",
    [
        "हूँ",  # "am" — collapsed onto "ह" (= "हाँ"/"हो") before the normalization fix
        "यह",  # "this"
        "हल",  # "solution"
        "No wait, the second one",  # a real g-012 utterance: must reach the LLM, not the gate
        "मुझे नहीं पता",  # "I don't know" — contains a no-word but is not a refusal
    ],
)
def test_non_confirmations_fall_through_to_the_llm(utterance: str) -> None:
    """Golden rule 4: only an explicit whole-utterance yes/no may resolve a PendingAction.
    Anything else must return None so the turn goes to the LLM instead of executing a write."""
    assert _match_lexicon(utterance, {}) is None


def test_normalize_preserves_devanagari_combining_marks() -> None:
    """Regression: `[^\\w\\s]` stripped Unicode marks because Python's `\\w` excludes them,
    so "हाँ", "हो" and "हूँ" all became bare "ह" and matched each other."""
    assert _normalize("हाँ") != _normalize("हो")
    assert _normalize("हाँ") != _normalize("हूँ")
    assert _normalize("हाँ") == "हाँ"


def test_normalize_still_strips_punctuation_and_case() -> None:
    assert _normalize("  OK!!  ") == "ok"
    assert _normalize("Yes, please.") == "yes please"


def test_pack_supplied_extra_words_are_honoured() -> None:
    assert _match_lexicon("चालू करा", {"yes": ["चालू करा"]}) == "yes"
    assert _match_lexicon("नको रे", {"no": ["नको रे"]}) == "no"


@pytest.mark.parametrize("utterance", ["", "?", "...", "👍"])
def test_symbol_only_input_never_confirms_even_with_symbol_lexicon_entries(utterance: str) -> None:
    """A pack entry like "👍" normalizes to "", which must not make empty input a "yes"."""
    assert _match_lexicon(utterance, {"yes": ["👍", "✓"], "no": ["✗"]}) is None
