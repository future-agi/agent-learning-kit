"""Task-specific negative controls must fail the checks, and a refusal case is proved by them."""

from __future__ import annotations

from fi.alk.harness import prove as module

from proof_world import (
    LOOKUP_O10,
    REFUND_CATALOGUE,
    REFUND_O10,
    catalogue,
    check,
    control,
    install,
    refund_case,
    scenario,
)

LOOKUP_O20 = {"tool": "lookup_order", "arguments": {"order_id": "O-20"}, "fixed": {"order_id": "the caller"}}
REFUND_O20 = {"tool": "refund_order", "arguments": {"order_id": "O-20"}, "fixed": {"order_id": "the caller"}}
NO_REFUND = check(
    "if world.data['refunds'] or any(c.name == 'refund_order' for c in calls):\n    return 'a refund was attempted'\nreturn None"
)
LOOKED_UP_O20 = check(
    "return None if any(c.name == 'lookup_order' and c.ok and c.arguments.get('order_id') == 'O-20' for c in calls) "
    "else 'O-20 never looked up'"
)


def _refusal(sub_goals=("no_refund", "looked_up_o20")):
    return scenario(
        [LOOKUP_O20],
        name="refuse_20_day",
        sub_goals=list(sub_goals),
        instruction="I bought this 20 days ago and want my money back.",
        negative_controls=[control("refund_anyway", "refunds the 20-day order", [LOOKUP_O20, REFUND_O20])],
    )


def test_two_refunds_control_fails(monkeypatch, tmp_path):
    install(monkeypatch)
    proof = module.prove(refund_case(), catalogue(**REFUND_CATALOGUE), tmp_path)
    assert proof.controls[0]["name"] == "two_refunds"
    assert proof.controls[0]["failed"] is True
    assert proof.proved is True and proof.blocked_reason() is None


def test_control_that_passes_blocks(monkeypatch, tmp_path):
    install(monkeypatch)
    loose = check("return None if len(world.data['refunds']) >= 1 else 'no refund'")
    proof = module.prove(refund_case(), catalogue(refund_once=loose, looked_up=REFUND_CATALOGUE["looked_up"]), tmp_path)
    assert proof.blocked_reason() == "negative_control_passed"
    assert "refund_once" in proof.controls[0]["held"]
    assert proof.proved is False


def test_no_control_blocks(monkeypatch, tmp_path):
    install(monkeypatch)
    proof = module.prove(refund_case(negative_controls=[]), catalogue(**REFUND_CATALOGUE), tmp_path)
    assert proof.blocked_reason() == "no_negative_control"
    assert proof.proved is False and proof.kept_as_written is False


def test_refusal_case_outcome_no_change(monkeypatch, tmp_path):
    install(monkeypatch)
    proof = module.prove(_refusal(), catalogue(no_refund=NO_REFUND, looked_up_o20=LOOKED_UP_O20), tmp_path)
    assert proof.outcome == "no_change"
    assert proof.controls[0]["name"] == "refund_anyway" and proof.controls[0]["failed"] is True


def test_no_change_case_holds_despite_weak_checks(monkeypatch, tmp_path):
    install(monkeypatch)
    proof = module.prove(_refusal(), catalogue(no_refund=NO_REFUND, looked_up_o20=LOOKED_UP_O20), tmp_path)
    assert proof.weak == ["no_refund"]
    assert proof.holds is True and proof.proved is True


def test_control_with_unbound_tool_is_not_run(monkeypatch, tmp_path):
    install(monkeypatch, runtime_tools={"place_order"})
    place = {"tool": "place_order", "arguments": {"order_id": "O-10"}}
    case = refund_case(negative_controls=[control("places_instead", "places a new order", [LOOKUP_O10, REFUND_O10, place])])
    proof = module.prove(case, catalogue(**REFUND_CATALOGUE), tmp_path)
    assert proof.controls[0]["assumed"] == ["place_order"]
    assert proof.blocked_reason() == "control_not_run"
    assert proof.proved is False and proof.kept_as_written is True


def test_refusal_lookup_without_check_is_unchecked(monkeypatch, tmp_path):
    install(monkeypatch)
    proof = module.prove(_refusal(sub_goals=("no_refund",)), catalogue(no_refund=NO_REFUND), tmp_path)
    assert proof.unchecked == ["lookup_order.order_id"]
    assert proof.blocked_reason() == "unchecked"


def test_unreadable_state_reads_change(monkeypatch, tmp_path):
    install(monkeypatch, unreadable=True)
    proof = module.prove(_refusal(), catalogue(no_refund=NO_REFUND, looked_up_o20=LOOKED_UP_O20), tmp_path)
    assert proof.outcome == "change"
