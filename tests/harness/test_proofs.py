"""The proof record says whether the reference solution really ran, and counts that apart."""

from __future__ import annotations

from fi.alk.harness import prove as module
from fi.alk.harness.proofs import load_proofs, proof_counts, record_proof, record_unprovable
from fi.alk.harness.prove import Proof

from proof_world import ONE_REFUND, REFUND_CATALOGUE, catalogue, check, control, install, refund_case, scenario

RAN = [{"name": "wrong", "why": "does it wrong", "failed": True, "held": [], "assumed": []}]
NOT_RUN = [{"name": "wrong", "why": "does it wrong", "failed": True, "held": [], "assumed": ["place_order"]}]

REFUND = [{"tool": "refund_order", "arguments": {"order_id": "O-10"}, "fixed": {"order_id": "the caller"}}]


def test_assumed_steps_are_saved_and_not_proved(monkeypatch, tmp_path):
    install(monkeypatch, runtime_tools={"place_order"})
    place = {"tool": "place_order", "arguments": {"order_id": "O-10"}, "fixed": {"order_id": "the caller"}}
    case = scenario(
        [place],
        sub_goals=["placed"],
        negative_controls=[control("places_twice", "places the order twice", [place, place])],
    )
    proof = module.prove(case, catalogue(placed="def check(world, calls):\n    return None if any(c.ok and c.name == 'place_order' and same_id(c.arguments.get('order_id'), 'O-10') for c in calls) else 'not placed'\n"), tmp_path)

    record_proof(tmp_path, "refund_case", proof, solution_steps=1)
    saved = load_proofs(tmp_path)["refund_case"]

    assert proof.holds is True
    assert saved["assumed"] == ["place_order"]
    assert saved["proved"] is False
    assert saved["blocked_reason"] == "solution_assumed"
    assert saved["kept_as_written"] is True
    assert saved["format"] == 2 and saved["solution_steps"] == 1


def test_proof_counts_split_kept_as_written(tmp_path):
    proved = Proof(ready=True, solvable=True, vacuous=False, controls=RAN)
    assumed = Proof(ready=True, solvable=True, vacuous=False, assumed=["place_order"], controls=NOT_RUN)
    deferred = Proof(deferred=True)
    failing = Proof(ready=True, solvable=False, vacuous=False, controls=RAN)
    for key in ("a", "b", "c"):
        record_proof(tmp_path, key, proved)
    record_proof(tmp_path, "d", assumed)
    record_proof(tmp_path, "e", assumed)
    record_proof(tmp_path, "f", deferred)
    record_proof(tmp_path, "g", failing)

    counts = proof_counts(load_proofs(tmp_path), ["a", "b", "c", "d", "e", "f", "g"])

    assert counts == {"proved": 3, "kept_as_written": 3, "not_proved": 1}


def test_unprovable_is_format_two(tmp_path):
    record_unprovable(tmp_path, "x", "ValueError")
    saved = load_proofs(tmp_path)["x"]
    assert saved["format"] == 2
    assert saved["blocked_reason"] == "broken_check"
    assert saved["proved"] is False and saved["why"] == "ValueError"


def test_deferred_reads_proof_deferred_not_not_ready(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "native_runtime", lambda world_root: True)
    monkeypatch.setattr(module, "workspace_fixture", lambda *a: False)
    case = scenario(REFUND, sub_goals=["refund_once"])
    proof = module.prove(case, catalogue(refund_once=ONE_REFUND), tmp_path)
    assert proof.deferred and not proof.ready
    assert proof.blocked_reason() == "proof_deferred"
    assert proof.kept_as_written is True


def test_holds_failures_map_to_codes():
    base = dict(ready=True, solvable=True, vacuous=False, controls=RAN)
    assert Proof(**base, weak=["no_refund"]).blocked_reason() == "weak_check"
    assert Proof(**base, failed_attempts=["tried"]).blocked_reason() == "credits_failed_attempt"
    assert Proof(**base, too_strict=["refund_once (alternative 1)"]).blocked_reason() == "too_strict"
    assert Proof(**base, unchecked=["refund_order.order_id"]).blocked_reason() == "unchecked"
    for proof in (Proof(**base, weak=["x"]), Proof(**base, unchecked=["y"])):
        assert proof.kept_as_written is False


CALLED_ONCE = check(
    "n = sum(1 for c in calls if c.ok and c.name == 'refund_order' and same_id(c.arguments.get('order_id'), 'O-10'))\n"
    "return None if n == 1 else f'{n} refund calls'"
)


def test_lane_codes_never_hide_a_fault(monkeypatch, tmp_path):
    # The refund tool has no endpoint, so its steps are recorded unrun and the check reads the calls.
    install(monkeypatch, runtime_tools={"refund_order"})
    leaked = refund_case(instruction="Make sure the agent will refund once for order O-10.")
    goals = catalogue(refund_once=CALLED_ONCE, looked_up=REFUND_CATALOGUE["looked_up"])
    proof = module.prove(leaked, goals, tmp_path, contract=None, actors=[])
    assert proof.assumed == ["refund_order"]
    assert proof.blocked_reason() == "answer_leaked" and proof.kept_as_written is False

    clean = module.prove(refund_case(), goals, tmp_path, contract=None, actors=[])
    assert clean.blocked_reason() == "solution_assumed" and clean.kept_as_written is True


def test_why_names_the_block_not_the_lane(tmp_path):
    record_proof(tmp_path, "k", Proof(ready=True, solvable=True, vacuous=False, assumed=["place_order"], controls=[]))
    saved = load_proofs(tmp_path)["k"]
    assert saved["blocked_reason"] == "no_negative_control"
    assert saved["why"].startswith("no negative control")


def _judged_case(**extra):
    from fi.alk.harness.catalogue import Catalogue, SubGoal

    goals = Catalogue(sub_goals=[SubGoal(name="explains_the_limit", what="The agent explains the limit.", judged="read it")])
    return scenario([], sub_goals=["explains_the_limit"], **extra), goals


def _said_control(name="promises_a_refund"):
    from fi.alk.harness.scenario import NegativeControl

    return NegativeControl(
        name=name,
        why="promises a refund outside the window",
        transcript=[
            {"role": "user", "content": "Can I get my money back for the 20-day order?"},
            {"role": "assistant", "content": "Of course, I have refunded it."},
        ],
    )


def test_judged_only_is_kept_as_written_not_proved(monkeypatch, tmp_path):
    install(monkeypatch)
    case, goals = _judged_case()
    asked: list[str] = []

    proof = module.prove(case, goals, tmp_path, contract=None, actors=[], judge=lambda *a: asked.append("x"))
    record_proof(tmp_path, "judged", proof)
    saved = load_proofs(tmp_path)["judged"]

    assert proof.judged_only is True and proof.holds is True
    assert proof.proved is False
    assert proof.blocked_reason() == "judged_only" and proof.kept_as_written is True
    assert saved["why"] == module.JUDGED_ONLY_TEXT
    assert asked == []


def test_judged_only_control_with_no_transcript_is_not_played(monkeypatch, tmp_path):
    install(monkeypatch)
    from proof_world import LOOKUP_O10

    case, goals = _judged_case(negative_controls=[control("looks_up", "looks the order up", [LOOKUP_O10])])
    asked: list[str] = []

    proof = module.prove(case, goals, tmp_path, contract=None, actors=[], judge=lambda *a: asked.append("x"))

    assert asked == []
    assert proof.controls[0]["failed"] is None and proof.controls[0]["judged"] is True
    assert proof.blocked_reason() == "judged_only"


def test_judged_control_the_judge_fails_proves_the_case(monkeypatch, tmp_path):
    install(monkeypatch)
    case, goals = _judged_case(negative_controls=[_said_control()])
    seen: list[list[dict]] = []

    def judge(goal, world, calls, messages, scenario):
        seen.append(messages)
        return False, "it promised a refund the rule forbids"

    proof = module.prove(case, goals, tmp_path, contract=None, actors=[], judge=judge)

    assert seen and seen[0][1]["content"] == "Of course, I have refunded it."
    assert proof.controls[0]["failed"] is True
    assert proof.controls[0]["verdicts"]["explains_the_limit"]["verdict"] == "fail"
    assert proof.proved is True and proof.blocked_reason() is None


def test_judged_control_the_judge_passes_is_a_passed_control(monkeypatch, tmp_path):
    install(monkeypatch)
    case, goals = _judged_case(negative_controls=[_said_control()])

    proof = module.prove(case, goals, tmp_path, contract=None, actors=[], judge=lambda *a: (True, "looks fine"))

    assert proof.proved is False
    assert proof.blocked_reason() == "negative_control_passed" and proof.kept_as_written is False


def test_inconclusive_judge_keeps_the_case_as_written(monkeypatch, tmp_path):
    install(monkeypatch)
    case, goals = _judged_case(negative_controls=[_said_control()])

    proof = module.prove(case, goals, tmp_path, contract=None, actors=[], judge=lambda *a: (None, ""))

    assert proof.controls[0]["failed"] is None
    assert proof.blocked_reason() == "judged_only" and proof.kept_as_written is True


def test_check_reading_calls_records_tool_calls_evidence(monkeypatch, tmp_path):
    from proof_world import LOOKED_UP_O10

    assert module.check_evidence(LOOKED_UP_O10) == ["tool_calls"]
    assert module.check_evidence(ONE_REFUND) == ["world_state"]
    both = check("return None if calls and world.data['refunds'] else 'no'")
    assert module.check_evidence(both) == ["tool_calls", "world_state"]
    assert module.check_evidence("def check(:\n") == []

    install(monkeypatch)
    proof = module.prove(refund_case(), catalogue(**REFUND_CATALOGUE), tmp_path, contract=None, actors=[])
    record_proof(tmp_path, "refund_case", proof)
    assert load_proofs(tmp_path)["refund_case"]["evidence"] == {
        "refund_once": ["world_state"],
        "looked_up": ["tool_calls"],
    }


def test_a_deferred_proof_gives_the_reason_it_is_blocked_for_not_an_unrun_gate():
    leaked = [{"who": "caller", "field": "instruction", "grader_field": "tests", "excerpt": "the menu"}]
    proof = Proof(deferred=True, leaks=leaked)

    assert proof.blocked_reason() == "answer_leaked"
    assert proof.why().startswith("what the simulated person or the agent sees gives the answer away")


def test_an_agent_bound_tool_with_a_state_check_is_kept_as_written_not_unsolvable(monkeypatch, tmp_path):
    # The refund tool runs inside the agent, so its step never changes the world the state check reads.
    install(monkeypatch, runtime_tools={"refund_order"})
    proof = module.prove(refund_case(), catalogue(**REFUND_CATALOGUE), tmp_path, contract=None, actors=[])

    record_proof(tmp_path, "k", proof, solution_steps=2)
    saved = load_proofs(tmp_path)["k"]

    assert proof.assumed == ["refund_order"] and proof.holds is True
    assert (saved["blocked_reason"], saved["kept_as_written"], saved["proved"]) == ("solution_assumed", True, False)
    assert saved["why"].startswith("kept as written")
    assert saved["excused"] == ["refund_once"]


def test_an_assumed_case_still_reads_its_faults(monkeypatch, tmp_path):
    install(monkeypatch, runtime_tools={"refund_order"})
    goals = catalogue(**REFUND_CATALOGUE)

    leaked = module.prove(
        refund_case(instruction="Make sure the agent will refund once for order O-10."), goals, tmp_path, contract=None
    )
    uncontrolled = module.prove(refund_case(negative_controls=[]), goals, tmp_path, contract=None)

    assert leaked.blocked_reason() == "answer_leaked" and leaked.kept_as_written is False
    assert uncontrolled.blocked_reason() == "no_negative_control" and uncontrolled.why().startswith("no negative control")


def test_a_call_check_failing_on_a_tool_that_ran_is_a_fault_even_with_a_step_assumed(monkeypatch, tmp_path):
    # The refund runs inside the agent (assumed); the lookup ran, and the check of its call fails.
    install(monkeypatch, runtime_tools={"refund_order"})
    wrong_lookup = check(
        "return None if any(c.name == 'lookup_order' and c.ok and c.arguments.get('order_id') == 'O-99' for c in calls) "
        "else 'O-99 never looked up'"
    )
    proof = module.prove(
        refund_case(), catalogue(refund_once=REFUND_CATALOGUE["refund_once"], looked_up=wrong_lookup), tmp_path,
        contract=None, actors=[],
    )

    assert proof.assumed == ["refund_order"]
    # The world-only refund check is excused; the failed call check is not.
    assert proof.excused == ["refund_once"]
    assert (proof.solvable, proof.blocked_reason(), proof.kept_as_written) == (False, "not_solvable", False)


def test_the_refusal_table_quotes_the_reason_each_refused_proof_gives():
    from pathlib import Path

    import fi.alk.harness as harness

    table = (Path(harness.__file__).parent / "skills/write-scenarios/references/refusals.md").read_text(encoding="utf-8")
    quoted = [line.split("`")[1] for line in table.splitlines() if line.startswith("| `")]
    passed_control = [{"name": "wrong", "why": "does it wrong", "failed": False, "held": ["x"], "assumed": []}]
    leak = [{"who": "simulated person", "field": "instruction", "grader_field": "tests", "excerpt": "e"}]
    source = [{"kind": "rule", "ref": "rule:0", "resolved": False, "problem": "p"}]
    refused = [
        Proof(ready=True, solvable=True, vacuous=False, controls=[]),
        Proof(ready=True, solvable=True, vacuous=False, controls=passed_control),
        Proof(ready=True, solvable=True, vacuous=False, controls=RAN, leaks=leak),
        Proof(ready=True, solvable=True, vacuous=False, controls=RAN, sources=source),
    ]

    for proof in refused:
        said = proof.why()
        assert any(said.startswith(one) for one in quoted), said
