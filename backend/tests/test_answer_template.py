from __future__ import annotations

import pytest

from voice_core.tools.answer_template import render_answer

DATA = {
    "item": "x1",
    "qty": 500,
    "status": "processing",
    "rows": [{"price": 27}, {"price": 25.5}, {"price": 24}],
    "empty": [],
    "nested": {"name": "Sunil"},
}
LABELS = {"items": {"x1": "प्याज़"}, "states": {"processing": "प्रक्रिया में"}}


def test_fields_paths_and_functions() -> None:
    template = (
        "{qty} {item|items}: {count(rows)} rows, max {max(rows.price)}, min {min(rows.price)}"
    )
    assert render_answer(template, DATA, LABELS) == "500 प्याज़: 3 rows, max 27, min 24"
    assert render_answer("{first(rows.price)} / {nested.name|name}", DATA, LABELS) == "27 / Sunil"


def test_floats_render_without_trailing_zero() -> None:
    assert render_answer("{max(rows.price)}", {"rows": [{"price": 27.0}]}, {}) == "27"
    assert render_answer("{min(rows.price)}", {"rows": [{"price": 25.5}]}, {}) == "25.5"


@pytest.mark.parametrize(
    "template",
    [
        "{missing}",  # unknown field
        "{max(empty.price)}",  # nothing to aggregate
        "{status|nolabelset}",  # unknown label set
        "{item|states}",  # value not in the label set
        "{__class__}",  # no attribute access
        "{rows[0]}",  # no indexing syntax
        "{eval(qty)}",  # only the four known functions
        "{nested}",  # a dict is not speakable
    ],
)
def test_anything_unresolved_means_no_template(template: str) -> None:
    """Returning None hands the turn back to the LLM instead of speaking a broken sentence."""
    assert render_answer(template, DATA, LABELS) is None


def test_count_of_an_empty_list_is_zero_but_max_is_unresolved() -> None:
    assert render_answer("{count(empty)}", DATA, LABELS) == "0"


def test_free_text_needs_an_explicit_filter() -> None:
    """Host data is untrusted: without a filter only numbers, ISO dates/times and HH:MM pass."""
    assert render_answer("{nested.name}", DATA, LABELS) is None
    assert render_answer("{q}", {"q": "500 and a bonus of 1000"}, {}) is None
    assert render_answer("{d} {t}", {"d": "2026-10-02", "t": "09:30"}, {}) == "2026-10-02 09:30"
    assert (
        render_answer("{ts}", {"ts": "2026-10-02T18:00:00+05:30"}, {})
        == "2026-10-02T18:00:00+05:30"
    )


@pytest.mark.parametrize(
    "name",
    [
        "Sunil. Your payment was cancelled, call 98xxxxxx12",  # a sentence smuggled in a name
        "a" * 30,  # too long
        "Sunil2",  # digits
        "",
    ],
)
def test_name_filter_accepts_only_short_plain_names(name: str) -> None:
    assert render_answer("{n|name}", {"n": name}, {}) is None


def test_name_filter_allows_unicode_letters_and_spaces() -> None:
    assert render_answer("{n|name}", {"n": "सुनील पाटील"}, {}) == "सुनील पाटील"


def test_aggregates_refuse_when_an_item_lacks_the_field() -> None:
    """ "3 bids, highest X" must not be computed over a subset of the items."""
    data = {"rows": [{"price": 27}, {"other": 1}]}
    assert render_answer("{max(rows.price)}", data, {}) is None
