"""Automatic reply-language switching (SPEC §9).

Explicit requests ("मराठीत बोला", "speak English") are handled by the LLM calling the
`set_preferred_language` core tool, or by the client's `language.set` — both call `set()` and
are persisted by the caller. This module only decides *automatic* switches from STT language
detection, deliberately conservatively: a wrong flip is worse than a missed one.
"""

from __future__ import annotations

import unicodedata

MIN_CONFIDENCE = 0.8
MIN_WORDS = 3
CONSECUTIVE_TURNS = 2
_DEVANAGARI_LANGUAGES = frozenset({"hi-IN", "mr-IN"})


def _devanagari_share(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    devanagari = sum(1 for ch in letters if "DEVANAGARI" in unicodedata.name(ch, ""))
    return devanagari / len(letters)


class LanguageTracker:
    def __init__(self, session_language: str, supported: tuple[str, ...]) -> None:
        self.session_language = session_language
        self._supported = supported
        self._candidate: str | None = None
        self._streak = 0

    def set(self, language: str) -> None:
        """Explicit switch (tool call or client chip)."""
        self.session_language = language
        self._candidate = None
        self._streak = 0

    def observe(self, text: str, detected: str | None, confidence: float) -> str | None:
        """Feed one user utterance. Returns the new session language if this turn switches it."""
        if detected is None or not self._counts_as_other_language(text, detected, confidence):
            self._candidate = None
            self._streak = 0
            return None

        if detected == self._candidate:
            self._streak += 1
        else:
            self._candidate = detected
            self._streak = 1

        if self._streak >= CONSECUTIVE_TURNS:
            self.set(detected)
            return detected
        return None

    def _counts_as_other_language(self, text: str, detected: str, confidence: float) -> bool:
        if detected == self.session_language:
            return False
        if detected not in self._supported or confidence < MIN_CONFIDENCE:
            return False
        if len(text.split()) < MIN_WORDS:
            return False
        # Script sanity check: code-mixed Hinglish/Manglish transcribed mostly in Devanagari is
        # not English, whatever the detector says.
        devanagari = _devanagari_share(text)
        if detected in _DEVANAGARI_LANGUAGES:
            return devanagari >= 0.5
        return devanagari < 0.5
