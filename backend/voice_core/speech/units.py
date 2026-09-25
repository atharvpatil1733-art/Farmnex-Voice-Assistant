"""Measurement units the normalizer should speak. Which units exist is app-specific, so they
come from the domain pack (`pack.yaml → speech_units`); the core only knows how to apply them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SpokenUnit:
    aliases: tuple[str, ...]  # written forms, e.g. ("x", "xs")
    words: dict[str, tuple[str, str]]  # language -> (singular, plural)

    def forms(self, language: str) -> tuple[str, str] | None:
        return self.words.get(language) or self.words.get("en-IN")


def parse_units(raw: Any) -> tuple[SpokenUnit, ...]:
    """Parse the pack's list of {aliases: [...], <lang>: word | [singular, plural]}."""
    if not isinstance(raw, list):
        raise ValueError("speech_units must be a list")
    units: list[SpokenUnit] = []
    for entry in raw:
        if not isinstance(entry, dict) or not entry.get("aliases"):
            raise ValueError("each speech_units entry needs a non-empty 'aliases' list")
        words: dict[str, tuple[str, str]] = {}
        for language, value in entry.items():
            if language == "aliases":
                continue
            if isinstance(value, str):
                words[language] = (value, value)
            elif isinstance(value, list) and len(value) == 2:
                words[language] = (str(value[0]), str(value[1]))
            else:
                raise ValueError(f"speech_units {language}: use a word or [singular, plural]")
        units.append(SpokenUnit(aliases=tuple(str(a) for a in entry["aliases"]), words=words))
    return tuple(units)
