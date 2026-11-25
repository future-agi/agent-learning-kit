from types import SimpleNamespace

import pytest

from fi.alk.harness import prove as module
from fi.alk.harness.catalogue import Catalogue, SubGoal
from fi.alk.harness.world.runtime import Call


@pytest.mark.parametrize(
    "condition,call,accepted",
    [
        ("c.arguments.get('method')", Call("select", {"method": "card"}), False),
        (
            "c.ok and c.arguments.get('method')",
            Call("select", {"method": "card"}),
            True,
        ),
        (
            "c.refused and c.arguments.get('method')",
            Call("select", {"method": "card"}, ok=False, refused=True),
            True,
        ),
        (
            "not c.ok and c.arguments.get('method')",
            Call("select", {"method": "card"}, ok=False, refused=True),
            False,
        ),
    ],
)
def test_proof_requires_outcome_not_just_attempt(
    monkeypatch, tmp_path, condition, call, accepted
):
    world = SimpleNamespace(close=lambda: None)
    outcome = SimpleNamespace(ok=True, said="", broken=False)
    monkeypatch.setattr(module, "prepared", lambda *a: (world, outcome, outcome))
    monkeypatch.setattr(
        module,
        "_run",
        lambda *a, with_solution: (world, [call] if with_solution else [], [], []),
    )
    catalogue = Catalogue(
        sub_goals=[
            SubGoal(
                name="selected",
                what="Select payment",
                check=(
                    "def check(world, calls):\n"
                    f'    return None if any({condition} for c in calls) else "not achieved"\n'
                ),
            )
        ]
    )
    proof = module.prove(
        SimpleNamespace(
            fixture={}, sub_goals=["selected"], solution=(), setup_code="", ready_code=""
        ),
        catalogue,
        tmp_path,
    )
    assert proof.holds is accepted
    assert bool(proof.failed_attempts) is not accepted
    if not accepted:
        assert "every tool attempt crashes" in proof.why()
    assert call.error == ""  # Mutation never edits the original evidence.


def test_record_code_mutation_preserves_format_and_other_arguments():
    from fi.alk.harness.scenario import SOURCE_FIXTURE, Step

    case = {"starting_facts": [{"field": "expected_action", "value": "escalate_ACORN-59"}]}
    step = Step(tool="escalate_case", arguments={"case_id": "R-1", "code": "ACORN-59"},
                fixed={"code": "record: approved expected_action"})
    scenario = SimpleNamespace(fixture={SOURCE_FIXTURE: case}, solution=[step])
    [(label, [mutated], corrected)] = module._with_one_wrong(scenario)
    assert (label, corrected) == ("escalate_case.code", False)
    assert mutated.arguments == {"case_id": "R-1", "code": "ACORN-50"}
    assert step.arguments["code"] == "ACORN-59"
