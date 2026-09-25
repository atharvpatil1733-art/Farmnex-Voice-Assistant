"""Split streamed assistant text into sentences for per-sentence TTS (SPEC §6 step 6).

Boundaries: । ॥ ? ! newline, and "." when followed by whitespace — except after a digit that
continues ("25.5", "10.30") or a known abbreviation ("Rs.", "Dr."). A run longer than
`max_chars` without a boundary is force-flushed at the last comma or space.
"""

from __future__ import annotations

DEFAULT_MAX_CHARS = 180
_HARD_ENDS = frozenset("।॥?!")
_ABBREVIATIONS = frozenset(
    {"rs", "dr", "mr", "mrs", "ms", "no", "st", "vs", "approx", "etc", "sr", "jr"}
)


class SentenceSegmenter:
    def __init__(self, max_chars: int = DEFAULT_MAX_CHARS) -> None:
        self._buffer = ""
        self._max_chars = max_chars

    def feed(self, delta: str) -> list[str]:
        """Add streamed text; return any sentences that are now complete."""
        self._buffer += delta
        out: list[str] = []
        while True:
            cut = self._find_boundary()
            if cut is None:
                break
            out.extend(self._emit(cut))
        while len(self._buffer) > self._max_chars:
            out.extend(self._emit(self._force_cut()))
        return out

    def flush(self) -> list[str]:
        """End of stream: return whatever remains (split if over the length limit)."""
        out: list[str] = []
        while len(self._buffer) > self._max_chars:
            out.extend(self._emit(self._force_cut()))
        out.extend(self._emit(len(self._buffer)))
        return out

    def _emit(self, cut: int) -> list[str]:
        sentence = self._buffer[:cut].strip()
        self._buffer = self._buffer[cut:].lstrip()
        return [sentence] if sentence else []

    def _find_boundary(self) -> int | None:
        buf = self._buffer
        for i, ch in enumerate(buf):
            if ch == "\n":
                return i + 1
            if ch in _HARD_ENDS:
                return i + 1
            if ch == ".":
                if i + 1 >= len(buf):
                    return None  # can't tell yet: "25." may continue as "25.5"
                nxt = buf[i + 1]
                if nxt.isdigit() or not nxt.isspace():
                    continue
                if _word_before(buf, i).lower() in _ABBREVIATIONS:
                    continue
                return i + 1
        return None

    def _force_cut(self) -> int:
        window = self._buffer[: self._max_chars]
        for sep in (",", " "):
            pos = window.rfind(sep)
            if pos > 0:
                return pos + 1
        return self._max_chars


def _word_before(text: str, index: int) -> str:
    start = index
    while start > 0 and text[start - 1].isalpha():
        start -= 1
    return text[start:index]


def split_sentences(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
    segmenter = SentenceSegmenter(max_chars)
    return segmenter.feed(text) + segmenter.flush()
