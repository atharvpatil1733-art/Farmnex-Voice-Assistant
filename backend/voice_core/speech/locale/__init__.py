from __future__ import annotations

from voice_core.speech.locale import en, hi, mr
from voice_core.speech.locale.base import SpeechLocale

_BY_LANGUAGE = {"hi-IN": hi.LOCALE, "mr-IN": mr.LOCALE, "en-IN": en.LOCALE}


def get_locale(language: str) -> SpeechLocale:
    """Tables for a BCP-47 language; English tables for anything not yet covered."""
    return _BY_LANGUAGE.get(language, en.LOCALE)
