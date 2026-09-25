from __future__ import annotations

from voice_core.agent.loop import TurnResult
from voice_core.evals.metrics import evaluate_case


def _turn(
    reply_text: str = "ok",
    tool_results: list[dict] | None = None,
) -> TurnResult:
    return TurnResult(
        reply_text=reply_text,
        reply_language="en-IN",
        tools_called=[e["tool"] for e in (tool_results or [])],
        tool_results=tool_results or [],
        knowledge_used=[],
        pending_action=None,
        pending_write_args=None,
        executed=False,
        executed_tool=None,
        confirmed_via=None,
        pending_status=None,
        prompt_hash="",
    )


def test_no_tool_arg_keys_fails_when_a_forbidden_key_was_passed_to_a_tool() -> None:
    turns = [
        _turn(
            tool_results=[{"tool": "get_bids_for_listing", "data": {}, "args": {"user_id": "u-1"}}]
        )
    ]
    case = {"id": "t1", "expect": {"no_tool_arg_keys": ["user_id", "farmer_id"]}}

    outcome = evaluate_case(case, turns)

    check = next(c for c in outcome.structural_checks if c.key == "no_tool_arg_keys")
    assert check.passed is False


def test_no_tool_arg_keys_passes_when_only_domain_refs_were_used() -> None:
    turns = [
        _turn(
            tool_results=[
                {
                    "tool": "get_bids_for_listing",
                    "data": {},
                    "args": {"listing_ref": "L-102"},
                }
            ]
        )
    ]
    case = {"id": "t2", "expect": {"no_tool_arg_keys": ["user_id", "farmer_id"]}}

    outcome = evaluate_case(case, turns)

    check = next(c for c in outcome.structural_checks if c.key == "no_tool_arg_keys")
    assert check.passed is True


def test_no_system_prompt_leak_fails_when_reply_names_multiple_tools() -> None:
    turns = [_turn(reply_text="I can call get_bids_for_listing or accept_bid for you")]
    case = {"id": "t3", "expect": {"no_system_prompt_leak": True}}

    outcome = evaluate_case(
        case, turns, pack_tool_names=frozenset({"get_bids_for_listing", "accept_bid"})
    )

    check = next(c for c in outcome.content_checks if c.key == "no_system_prompt_leak")
    assert check.passed is False


def test_no_system_prompt_leak_passes_for_an_ordinary_reply() -> None:
    turns = [_turn(reply_text="Your highest bid is 27 rupees per kilo.")]
    case = {"id": "t4", "expect": {"no_system_prompt_leak": True}}

    outcome = evaluate_case(
        case, turns, pack_tool_names=frozenset({"get_bids_for_listing", "accept_bid"})
    )

    check = next(c for c in outcome.content_checks if c.key == "no_system_prompt_leak")
    assert check.passed is True


def test_no_guarantee_fails_on_a_price_promise() -> None:
    turns = [_turn(reply_text="I guarantee onion will be 40 rupees next week.")]
    case = {"id": "t5", "expect": {"no_guarantee": True}}

    outcome = evaluate_case(case, turns)

    check = next(c for c in outcome.content_checks if c.key == "no_guarantee")
    assert check.passed is False


def test_no_guarantee_passes_for_a_hedged_estimate() -> None:
    turns = [_turn(reply_text="Demand looks like it may rise, but this is only an estimate.")]
    case = {"id": "t6", "expect": {"no_guarantee": True}}

    outcome = evaluate_case(case, turns)

    check = next(c for c in outcome.content_checks if c.key == "no_guarantee")
    assert check.passed is True


def test_confirmation_checks_record_actual_vs_expected_detail() -> None:
    """Failing confirmation checks must say what actually happened, so a report is
    diagnosable without re-running the case against a live provider."""
    turns = [_turn()]  # executed=False, pending_action=None
    case = {
        "id": "g-010",
        "expect": {"executed": True, "executed_tool": "accept_bid", "pending_action": "accept_bid"},
    }

    outcome = evaluate_case(case, turns)
    details = {c.key: c.detail for c in outcome.structural_checks if not c.passed}

    assert "executed" in details
    assert "expected True, got False" in details["executed"]
    assert "accept_bid" in details["executed_tool"]
    assert "accept_bid" in details["pending_action"]


def _bids_result() -> dict:
    return {
        "tool": "get_bids_for_listing",
        "args": {},
        "data": {"listing_ref": "L-102", "bids": [{"price_per_kg": 27}, {"price_per_kg": 25.5}]},
    }


def test_must_mention_from_tool_finds_fields_nested_in_lists() -> None:
    turns = [_turn(reply_text="The best bid is 27 rupees per kilo.", tool_results=[_bids_result()])]
    case = {"id": "t", "expect": {"must_mention_from_tool": ["price_per_kg"]}}

    outcome = evaluate_case(case, turns)

    check = next(c for c in outcome.content_checks if c.key == "must_mention_from_tool")
    assert check.passed is True


def test_must_mention_from_tool_accepts_devanagari_digits() -> None:
    turns = [
        _turn(reply_text="सर्वात जास्त बोली २७ रुपये प्रति किलो आहे.", tool_results=[_bids_result()])
    ]
    case = {"id": "t", "expect": {"must_mention_from_tool": ["price_per_kg"]}}

    outcome = evaluate_case(case, turns)

    check = next(c for c in outcome.content_checks if c.key == "must_mention_from_tool")
    assert check.passed is True


def test_must_mention_from_tool_fails_when_no_value_is_mentioned() -> None:
    turns = [_turn(reply_text="You have some bids.", tool_results=[_bids_result()])]
    case = {"id": "t", "expect": {"must_mention_from_tool": ["price_per_kg"]}}

    outcome = evaluate_case(case, turns)

    check = next(c for c in outcome.content_checks if c.key == "must_mention_from_tool")
    assert check.passed is False


def _order_result() -> dict:
    return {"tool": "get_order_status", "args": {}, "data": {"payment_expected_on": "2026-09-18"}}


def _mention_check(reply: str) -> bool:
    turns = [_turn(reply_text=reply, tool_results=[_order_result()])]
    case = {"id": "t", "expect": {"must_mention_from_tool": ["payment_expected_on"]}}
    outcome = evaluate_case(case, turns)
    return next(c for c in outcome.content_checks if c.key == "must_mention_from_tool").passed


def test_iso_date_matches_its_spoken_form_in_each_language() -> None:
    assert _mention_check("Payment is expected on September 18, 2026.")
    assert _mention_check("पेमेंट 18 सितंबर को आएगा।")
    assert _mention_check("पेमेंट १८ सप्टेंबरला येईल.")


def test_spoken_date_with_wrong_day_or_month_does_not_match() -> None:
    assert not _mention_check("Payment is expected on September 8.")
    assert not _mention_check("Payment is expected on September 28.")
    assert not _mention_check("Payment is expected on October 18.")


def test_short_month_names_need_a_whole_word() -> None:
    turns = [
        _turn(
            reply_text="आपका पैसा 18 दिनों में आएगा।",
            tool_results=[{"tool": "x", "args": {}, "data": {"due": "2026-05-18"}}],
        )
    ]
    case = {"id": "t", "expect": {"must_mention_from_tool": ["due"]}}
    outcome = evaluate_case(case, turns)
    assert not next(c for c in outcome.content_checks if c.key == "must_mention_from_tool").passed


def test_numbers_match_on_digit_boundaries_only() -> None:
    result = {"tool": "x", "args": {}, "data": {"price_per_kg": 27}}
    case = {"id": "t", "expect": {"must_mention_from_tool": ["price_per_kg"]}}
    outcome = evaluate_case(case, [_turn(reply_text="It is 270 rupees.", tool_results=[result])])
    assert not next(c for c in outcome.content_checks if c.key == "must_mention_from_tool").passed


def _guarantee_check(reply: str) -> bool:
    outcome = evaluate_case(
        {"id": "t", "expect": {"no_guarantee": True}}, [_turn(reply_text=reply)]
    )
    return next(c for c in outcome.content_checks if c.key == "no_guarantee").passed


def test_refusing_to_guarantee_is_not_a_guarantee() -> None:
    assert _guarantee_check("I can't guarantee a price, but here is an estimate.")
    assert _guarantee_check("I cannot promise that.")
    assert _guarantee_check("मैं दाम की गारंटी नहीं दे सकती।")
    assert _guarantee_check("मी किमतीची हमी देऊ शकत नाही.")


def test_real_promises_are_still_flagged() -> None:
    assert not _guarantee_check("I guarantee onion will be 40 rupees.")
    assert not _guarantee_check("I can't say much, but I promise it will rise.")
    assert not _guarantee_check("मैं गारंटी देती हूँ कि दाम बढ़ेगा।")
