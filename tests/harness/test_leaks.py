"""What the caller, the agent or an actor sees must not give the answer away."""

from __future__ import annotations

import json
from types import SimpleNamespace

from fi.alk.harness.catalogue import Catalogue, SubGoal
from fi.alk.harness.leaks import leaks

WORLD = {"orders": {"O-123": {"total": 18.0}}}


def _case(instruction="", sub_goals=("refund_once",), **extra):
    values = dict(
        instruction=instruction,
        sub_goals=list(sub_goals),
        tests="",
        variables={},
        persona=None,
        negative_controls=[],
        setup_code="",
        actors=[],
    )
    values.update(extra)
    return SimpleNamespace(**values)


def _catalogue(**goals):
    return Catalogue(sub_goals=[SubGoal(name=name, **fields) for name, fields in goals.items()])


def _check(body):
    return "def check(world, calls):\n" + "".join(f"    {line}\n" for line in body.splitlines())


def test_instruction_repeating_sub_goal_text_leaks():
    catalogue = _catalogue(refund_once={"what": "The agent issues exactly one refund for the delayed order and confirms it"})
    case = _case("Please make sure you issues exactly one refund for the delayed order and confirms it today.")
    found = leaks(case, catalogue, None, WORLD)
    assert [(one.who, one.field, one.grader_field) for one in found] == [
        ("simulated person", "instruction", "sub_goal:refund_once.what")
    ]


def test_order_id_in_world_is_not_a_leak():
    catalogue = _catalogue(refund_once={"check": _check("return None if calls and calls[0].arguments.get('id') == 'O-123' else 'no'")})
    assert leaks(_case("My order is O-123."), catalogue, None, WORLD) == []


def test_expected_total_not_in_world_leaks():
    catalogue = _catalogue(refund_once={"check": _check("return None if world.data['refunded'] == 42.50 else 'wrong'")})
    found = leaks(_case("I expect 42.50 back."), catalogue, None, WORLD)
    assert [(one.grader_field, one.excerpt) for one in found] == [("check literal", "42.50")]


def test_actor_brief_leak():
    judged = "The agent explains the fourteen day refund window before refusing politely"
    catalogue = _catalogue(explains_window={"judged": judged})
    case = _case("Ask for a refund.", sub_goals=("explains_window",), actors=[{"id": "clerk"}])
    actors = [{"id": "clerk", "name": "Clerk", "instructions": f"Make sure {judged}."}]
    found = leaks(case, catalogue, None, WORLD, actors)
    assert [one.who for one in found] == ["actor"]
    assert found[0].grader_field == "sub_goal:explains_window.judged"


def test_initial_message_repeating_instruction_leaks():
    instruction = "You bought a lamp twenty days ago and it arrived broken so you want a refund"
    persona = {"name": "Dana", "initial_message": "Hi, I bought a lamp twenty days ago and it arrived broken."}
    case = _case(instruction, persona=persona, sub_goals=())
    found = leaks(case, Catalogue(), SimpleNamespace(conversational=True), WORLD)
    assert ("tested agent", "persona.initial_message", "instruction") in [
        (one.who, one.field, one.grader_field) for one in found
    ]


def test_single_word_literal_is_not_a_leak():
    single = _catalogue(refund_once={"check": _check("return None if world.data['status'] == 'refunded' else 'no'")})
    assert leaks(_case("I want it refunded."), single, None, WORLD) == []
    coded = _catalogue(refund_once={"check": _check("return None if world.data['ref'] == 'REF-2201' else 'no'")})
    found = leaks(_case("My reference should be REF-2201."), coded, None, WORLD)
    assert [one.grader_field for one in found] == ["check literal"]


def test_unrestorable_world_skips_world_rule():
    what = "The agent issues exactly one refund for the delayed order and confirms it"
    catalogue = _catalogue(
        refund_once={"what": what, "check": _check("return None if world.data['ref'] == 'REF-2201' else 'no'")}
    )
    case = _case(f"Make sure he {what}; my code is REF-2201 and I want a refund once.")
    found = {one.grader_field for one in leaks(case, catalogue, None, None)}
    assert found == {"sub_goal:refund_once.what", "sub_goal:refund_once.name"}


def test_single_word_sub_goal_name_is_not_a_leak():
    assert leaks(_case("I would like to ask for a refund.", sub_goals=("refund",)), _catalogue(refund={}), None, WORLD) == []
    found = leaks(_case("Just refund once, please.", sub_goals=("refund_once",)), _catalogue(refund_once={}), None, WORLD)
    assert [one.grader_field for one in found] == ["sub_goal:refund_once.name"]


def test_failure_message_is_not_grader_only():
    said = _catalogue(refund_once={"check": _check("if world.data['refunds']:\n    return None\nreturn 'no refund was issued'")})
    assert leaks(_case("I am worried no refund was issued."), said, None, WORLD) == []
    raised = _catalogue(refund_once={"check": _check("if not world.data['refunds']:\n    raise AssertionError('no refund was issued')")})
    assert leaks(_case("I am worried no refund was issued."), raised, None, WORLD) == []
    expected = _catalogue(refund_once={"check": _check("if world.data['total'] == '42.50 refunded':\n    return None\nreturn 'no'")})
    found = leaks(_case("It should say 42.50 refunded."), expected, None, WORLD)
    assert [one.grader_field for one in found] == ["check literal"]


def _step(tool, **arguments):
    return SimpleNamespace(tool=tool, arguments=arguments)


def test_solution_argument_in_the_opening_is_a_leak():
    catalogue = _catalogue(refund_once={"what": "The agent refunds the order."})
    case = _case(
        "Ask for your money back.",
        persona={"name": "Asha", "initial_message": "Please refund 37.25 as store credit for my order."},
        solution=[_step("refund_order", order_id="O-123", amount=37.25)],
    )
    found = leaks(case, catalogue, None, WORLD)
    assert [(one.who, one.field, one.grader_field, one.excerpt) for one in found] == [
        ("simulated person", "persona.initial_message", "scenario.solution", "37.25"),
        ("tested agent", "persona.initial_message", "scenario.solution", "37.25"),
    ]


def test_customer_fact_in_both_is_not_a_leak():
    catalogue = _catalogue(refund_once={"what": "The agent refunds the order."})
    case = _case(
        "Ask for your money back for the order on your receipt.",
        variables={"receipt": "RX-4417"},
        persona={"name": "Asha", "initial_message": "Hi, my receipt number is RX-4417."},
        solution=[_step("refund_order", receipt="RX-4417", note="the customer asked nicely today")],
    )
    assert leaks(case, catalogue, None, WORLD) == []


def test_solution_amount_written_either_way_or_nested_is_a_leak():
    catalogue = _catalogue(refund_once={"what": "The agent refunds the order."})
    short = _case(
        "Ask for your money back.",
        persona={"name": "Asha", "initial_message": "Please refund 37.5 to my card."},
        solution=[_step("refund_order", order_id="O-123", amount=37.5)],
    )
    nested = _case(
        "Ask for your money back.",
        persona={"name": "Asha", "initial_message": "Swap it for item SKU-889 please."},
        solution=[_step("exchange_order", order_id="O-123", items=[{"sku": "SKU-889", "qty": 1}])],
    )

    assert [one.excerpt for one in leaks(short, catalogue, None, WORLD)] == ["37.5", "37.5"]
    assert [one.excerpt for one in leaks(nested, catalogue, None, WORLD)] == ["sku 889", "sku 889"]


def test_visible_text_repeating_an_evaluator_only_attached_file_leaks(tmp_path):
    from fi.alk.harness.actors import KNOWLEDGE_ROLES

    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge" / "answers.md").write_text("The right refund for this order is eighteen pounds exactly.", encoding="utf-8")
    case = _case(instruction="I want my refund; the right refund for this order is eighteen pounds exactly.")
    catalogue = _catalogue(refund_once={"what": "one refund"})

    assert leaks(case, catalogue, world_root=tmp_path) == []
    (tmp_path / KNOWLEDGE_ROLES).write_text(json.dumps({"knowledge/answers.md": "evaluator_only"}), encoding="utf-8")
    [leak] = leaks(case, catalogue, world_root=tmp_path)
    assert leak.grader_field == "evaluator_only:knowledge/answers.md"
