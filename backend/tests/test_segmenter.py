from __future__ import annotations

from collections.abc import AsyncIterator

from voice_core.speech.segmenter import SentenceSegmenter, split_sentences


def test_splits_on_danda_and_latin_punctuation() -> None:
    assert split_sentences("आपकी बोली आई है। सबसे ऊँची 27 रुपये है। ठीक?") == [
        "आपकी बोली आई है।",
        "सबसे ऊँची 27 रुपये है।",
        "ठीक?",
    ]
    assert split_sentences("Your bid is in. Anything else? Great!") == [
        "Your bid is in.",
        "Anything else?",
        "Great!",
    ]


def test_does_not_split_inside_numbers_times_or_abbreviations() -> None:
    assert split_sentences("Price is 25.5 rupees. Pickup at 10.30 tomorrow.") == [
        "Price is 25.5 rupees.",
        "Pickup at 10.30 tomorrow.",
    ]
    assert split_sentences("It costs Rs. 40 per kilo. Call Dr. Rao.") == [
        "It costs Rs. 40 per kilo.",
        "Call Dr. Rao.",
    ]


def test_newline_is_a_boundary_and_blank_parts_are_dropped() -> None:
    assert split_sentences("पहला\n\nदूसरा") == ["पहला", "दूसरा"]


def test_long_run_without_punctuation_is_force_flushed_at_a_space() -> None:
    text = "बहुत " * 60  # 300 chars, no sentence end
    parts = split_sentences(text, max_chars=180)
    assert len(parts) >= 2
    assert all(len(p) <= 180 for p in parts)
    assert " ".join(parts).split() == text.split()


async def _stream(chunks: list[str]) -> AsyncIterator[str]:
    for chunk in chunks:
        yield chunk


async def test_streaming_emits_first_sentence_before_the_stream_ends() -> None:
    segmenter = SentenceSegmenter()
    emitted: list[str] = []
    # Feed deltas one by one; the first sentence must be out as soon as its end is seen
    # and the next character shows it isn't a decimal/abbreviation.
    for delta in ["आपकी बोली ", "आई है। ", "सबसे ऊँची ", "27 रुपये"]:
        emitted += segmenter.feed(delta)
        if delta.startswith("सबसे"):
            assert emitted == ["आपकी बोली आई है।"]
    emitted += segmenter.flush()
    assert emitted == ["आपकी बोली आई है।", "सबसे ऊँची 27 रुपये"]


async def test_decimal_split_across_deltas_is_not_a_boundary() -> None:
    segmenter = SentenceSegmenter()
    out: list[str] = []
    for delta in ["Price is 25", ".", "5 rupees", ". Done"]:
        out += segmenter.feed(delta)
    out += segmenter.flush()
    assert out == ["Price is 25.5 rupees.", "Done"]
