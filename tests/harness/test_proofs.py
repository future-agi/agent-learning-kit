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
        Proof(
            ready=True, solvable=True, vacuous=False, case_goals=["own_goal"],
            controls=[{"name": "shared", "why": "", "ran": True, "failed": True, "held": [], "failing": ["shared_step"],
                       "assumed": []}],
        ),
    ]

    for proof in refused:
        said = proof.why()
        assert any(said.startswith(one) for one in quoted), said


def test_each_saved_proof_says_whether_a_submit_refuses_it(tmp_path):
    record_proof(tmp_path, "uncontrolled", Proof(ready=True, solvable=True, vacuous=False, controls=[]))
    record_proof(tmp_path, "assumed", Proof(ready=True, solvable=True, vacuous=False, assumed=["x"], controls=RAN))
    record_unprovable(tmp_path, "crashed", "ValueError")

    saved = load_proofs(tmp_path)

    assert {key: one["refused"] for key, one in saved.items()} == {"uncontrolled": True, "assumed": False, "crashed": True}


def test_the_builder_s_status_never_counts_states_and_shows_only_this_chat_s_proofs(tmp_path):
    """The build's proofs may not be what the Scenarios tab shows: the status gives no counts, and scenario_status
    lists only the proofs made in this chat, with their reasons."""
    import asyncio
    import json

    from fi.alk.harness.builder import suite_server
    from fi.alk.harness.hosted_chat_entrypoint import _status_note
    from fi.alk.harness.proofs import proof_key
    from fi.alk.harness.scenario import Scenario

    kept = [Scenario(name=name) for name in ("refund_ok", "refund_late", "refund_twice")]
    proved = Proof(ready=True, solvable=True, vacuous=False, controls=RAN)
    for one in kept:
        record_proof(tmp_path, proof_key(one), proved, solution_steps=1)
    # Made by the build, long before this chat.
    older = {key: {**entry, "proved_at": "2000-01-01T00:00:00Z"} for key, entry in load_proofs(tmp_path).items()}
    (tmp_path / "proofs.json").write_text(json.dumps(older), encoding="utf-8")

    note = _status_note(tmp_path, kept, "published")
    assert "proved" not in note and "3 scenarios" in note
    assert "This chat put its changes under Unsaved changes." in note
    assert "No changes in this chat." in _status_note(tmp_path, kept, "none")
    status = next(one.handler for one in suite_server(tmp_path, kept).tools if one.name == "scenario_status")
    said = asyncio.run(status({}))["content"][0]["text"]
    assert said.splitlines()[0].startswith("3 scenarios. Each one's state is the one its row on the Scenarios tab")
    assert said.splitlines()[1] == "No scenario was proved in this chat."
    # Proved here, in this chat: shown with the proof's own reason.
    record_unprovable(tmp_path, "refund-late", "its check reads a table the world lacks")
    assumed = Proof(ready=True, solvable=True, vacuous=False, assumed=["place_order"], controls=NOT_RUN)
    record_proof(tmp_path, "refund-twice", assumed, solution_steps=1)
    said = asyncio.run(status({}))["content"][0]["text"]
    assert "refund_ok" not in said
    assert "refund_late: not proved, broken_check: its check reads a table the world lacks" in said
    assert "refund_twice: kept as written, assumed place_order" in said
    one = asyncio.run(status({"names": ["refund_late", "gone"]}))["content"][0]["text"]
    assert "refund_twice" not in one and "refund_late: not proved" in one and "not in the suite: gone" in one

def test_a_search_that_finds_nothing_in_the_suite_points_at_the_removed_cases():
    from fi.alk.harness.hosted_chat_entrypoint import _with_removed_hint

    empty = {"total_scenarios": 7, "matching": 0, "rows": []}
    assert "find_scenarios(removed: true)" in _with_removed_hint(empty, {"names": ["lookup_order"]})["note"]
    assert "note" not in _with_removed_hint({**empty, "matching": 1}, {"names": ["lookup_order"]})
    assert "note" not in _with_removed_hint(empty, {"numbers": [40]})


def test_find_scenarios_flags_a_removed_case_the_query_names_exactly():
    from fi.alk.harness.hosted_chat_entrypoint import _removed_matching, _with_removed_hint

    removed = {"removed": [
        {"scenario_key": "lookup_order_1046", "name": "lookup_order_1046", "suite_version": "v6", "content": {}},
        {"scenario_key": "other_case", "name": "other_case", "suite_version": "v5", "content": {}},
    ]}
    query = {"names": ["lookup_order_1046"]}
    found = _removed_matching(removed, query)
    assert [one["scenario_key"] for one in found] == ["lookup_order_1046"]
    # The copy the builder wrote is in the suite; the original is shown beside it, flagged as removed.
    answer = _with_removed_hint({"matching": 1, "rows": [{"number": 8, "name": "lookup_order_1046"}]}, query, found)
    [flagged] = answer["removed_matches"]
    assert "it is not in the suite" in flagged["removed"]
    assert 'restored_from {"key": "lookup_order_1046"} and as_it_was true' in flagged["removed"]
    assert _removed_matching(removed, {"search": "lookup"}) == []


def test_every_builder_reply_closes_with_the_state_read_from_the_files(tmp_path):
    from fi.alk.harness.hosted_chat_entrypoint import _status_line
    from fi.alk.harness.scenario import Scenario

    kept = [Scenario(name="a"), Scenario(name="b")]
    record_proof(tmp_path, "a", Proof(ready=True, solvable=True, vacuous=False, controls=RAN), solution_steps=1)
    assert _status_line(tmp_path, kept, "unsaved") == (
        "Status: 2 scenarios. Changes in this chat are not yet under Unsaved changes."
    )
    assert _status_line(tmp_path, kept, "pending") == (
        "Status: 2 scenarios. Changes from this chat are under Unsaved changes; Validate, then Save to keep them as "
        "a new version."
    )
    assert _status_line(tmp_path, kept, "published") == (
        "Status: 2 scenarios. This chat put its changes under Unsaved changes."
    )
    assert _status_line(tmp_path, kept, "clear") == "Status: 2 scenarios."
    assert _status_line(tmp_path, kept, "none") == "Status: 2 scenarios. No changes in this chat."

def test_the_status_line_reads_zero_scenarios_and_says_nothing_about_saving_when_unknown(tmp_path):
    from fi.alk.harness.hosted_chat_entrypoint import _status_line

    assert _status_line(tmp_path, [], "unsaved") == (
        "Status: 0 scenarios. Changes in this chat are not yet under Unsaved changes."
    )
    assert _status_line(tmp_path, [], "unknown") == "Status: 0 scenarios."
    assert _status_line(tmp_path, [], "pending", 2).endswith("except 2 scenarios that could not be applied.")

def test_the_builder_reads_the_proofs_the_platform_shows_not_the_offline_ones(tmp_path):
    """The validation proved the cases on the live runtime; the chat's own offline proofs had them kept as written."""
    import json

    from fi.alk.harness.proofs import VALIDATED_PROOFS, load_shown_proofs, proof_entry
    from fi.alk.harness.scenario import Scenario

    kept = [Scenario(name="lookup"), Scenario(name="refund")]
    assumed = Proof(ready=True, solvable=True, vacuous=False, assumed=["get_order"], controls=NOT_RUN)
    for one in kept:
        record_proof(tmp_path, one.name, assumed, solution_steps=1)
    offline = load_proofs(tmp_path)
    live = {
        one.name: {**proof_entry(Proof(ready=True, solvable=True, vacuous=False, controls=RAN), 1), "live": True,
                   "proved_at": "2999-01-01T00:00:00Z"}
        for one in kept
    }
    (tmp_path / VALIDATED_PROOFS).write_text(json.dumps(live), encoding="utf-8")

    assert all(entry["kept_as_written"] for entry in offline.values())
    shown = load_shown_proofs(tmp_path)
    assert all(shown[one.name]["proved"] for one in kept)
    # A case proved here after the validation (edited since) reads its own, newer proof.
    newer = {**offline["refund"], "proved_at": "3000-01-01T00:00:00Z"}
    (tmp_path / "proofs.json").write_text(json.dumps({**offline, "refund": newer}), encoding="utf-8")
    assert load_shown_proofs(tmp_path)["refund"]["kept_as_written"] is True
    assert load_shown_proofs(tmp_path)["lookup"]["proved"] is True


def test_a_validated_proof_with_no_time_wins_only_over_an_untimed_local_one(tmp_path):
    import json

    from fi.alk.harness.proofs import VALIDATED_PROOFS, load_shown_proofs

    record_proof(tmp_path, "a", Proof(ready=True, solvable=True, vacuous=False, assumed=["x"], controls=NOT_RUN), solution_steps=1)
    (tmp_path / VALIDATED_PROOFS).write_text(json.dumps({"a": {"proved": True}, "b": {"proved": True}}), encoding="utf-8")
    # The local proof has a time: the untimed validated one does not beat it.
    assert load_shown_proofs(tmp_path)["a"]["kept_as_written"] is True
    # No local proof at all: the validated one is what there is.
    assert load_shown_proofs(tmp_path)["b"] == {"proved": True}
    local = load_proofs(tmp_path)
    (tmp_path / "proofs.json").write_text(json.dumps({"a": {**local["a"], "proved_at": ""}}), encoding="utf-8")
    assert load_shown_proofs(tmp_path)["a"] == {"proved": True}


def test_the_status_reads_the_scenarios_tab_s_own_states_when_the_platform_sends_them(tmp_path):
    """The tab says 4 proved, 2 not proved; the chat's own proofs say kept as written. The tab's are shown."""
    import asyncio

    from fi.alk.harness.builder import suite_server
    from fi.alk.harness.hosted_chat_entrypoint import _status_line
    from fi.alk.harness.scenario import Scenario

    kept = [Scenario(name=f"case_{n}") for n in range(6)]
    assumed = Proof(ready=True, solvable=True, vacuous=False, assumed=["get_order"], controls=NOT_RUN)
    for one in kept:
        record_proof(tmp_path, one.name, assumed, solution_steps=1)
    counts = {"proved": 4, "kept_as_written": 0, "not_proved": 2, "draft": 2}
    assert _status_line(tmp_path, kept, "none", counts=counts) == (
        "Status: 6 scenarios. On the Scenarios tab: 4 proved, 0 kept as written, 2 not proved. No changes in this chat."
    )
    assert _status_line(tmp_path, kept, "none") == "Status: 6 scenarios. No changes in this chat."

    asked = []

    async def tab(names):
        asked.append(names)
        return {
            "counts": counts,
            "rows": [{"number": 3, "name": "case_2", "lifecycle": {"state": "draft", "reason": "not_solvable",
                                                                "reason_text": "Its expected handling did not pass."}}],
        }

    status = next(one.handler for one in suite_server(tmp_path, kept, tab_status=tab).tools if one.name == "scenario_status")
    said = asyncio.run(status({"names": ["case_2"]}))["content"][0]["text"].splitlines()
    assert asked == [["case_2"]]
    assert said[0] == "On the Scenarios tab: 4 proved, 0 kept as written, 2 not proved."
    assert said[1] == "#3 case_2: draft (Its expected handling did not pass.)"
    assert not any("kept as written, assumed" in line for line in said)


def test_a_lookup_the_service_answers_not_found_counts_as_made_and_a_fault_does_not():
    """The 9943 shape: the reference looks up an order that does not exist and the agent's service answers 404 "no
    order 9943". The semantic call keeps the service's refusal, so both kinds of lookup check see the call as made;
    a server fault (5xx) or a transport failure is never read as answered."""
    from types import SimpleNamespace

    from fi.alk.harness.checks import answered
    from fi.alk.harness.prove import play_reference_step
    from fi.alk.harness.world.runtime import Call, refusal_code_for

    def world_answering(status):
        world = SimpleNamespace(calls=[], runtime_tools={"get_order"}, endpoint_for={"get_order": "orders/get"})

        def forward(endpoint, arguments, record=True):
            refused = status >= 400
            return Call(name=endpoint, arguments=dict(arguments), result={"error": "no order 9943"}, ok=not refused,
                        refused=refused, refusal_code=refusal_code_for(status), error='{"error": "no order 9943"}')

        world.forward = forward
        return world

    step = SimpleNamespace(tool="get_order", arguments={"order_id": "9943"}, environment_arguments={})
    world = world_answering(404)
    call = play_reference_step(world, step)
    assert call.refused is True and call.refusal_code == "world_rule"
    assert answered(world.calls, "get_order", order_id="9943") == [call]
    # A server fault is no answer: never read as a lookup made.
    faulted = world_answering(500)
    play_reference_step(faulted, step)
    assert answered(faulted.calls, "get_order", order_id="9943") == []


def test_a_reference_that_changes_someone_else_s_record_is_named_from_the_world_s_data(tmp_path):
    """38 of 50 refund cases refunded an order whose holder in the world was not the caller."""
    from fi.alk.harness.scenario import Persona, Scenario
    from fi.alk.harness.scenario_tools import _writes_for_someone_else
    from fi.alk.harness.world import GeneratedWorld
    from fi.alk.harness.world.snapshot import save

    class Shop(GeneratedWorld):
        name = "shop"
        tools = [{"name": "get_order"}, {"name": "issue_refund"}]
        handlers = {"get_order": "def handle(args, db):\n    return {}\n", "issue_refund": "def handle(args, db):\n    return {}\n"}

    world = Shop(":memory:")
    world.connection.executescript(
        "CREATE TABLE orders(order_id TEXT, customer_name TEXT);"
        "INSERT INTO orders VALUES ('1043', 'Ida Novak'), ('1044', 'Ian MacLeod');"
    )
    save(world, tmp_path, notes="test", sequences=[])

    def case(order, steps=("get_order", "issue_refund")):
        return Scenario(
            name="refund",
            persona=Persona(name="Ian MacLeod"),
            solution=[{"tool": tool, "arguments": {"order_id": order}} for tool in steps],
        )

    said = _writes_for_someone_else(case("1043"), tmp_path, {"get_order"})
    assert "issue_refund changes a record that belongs to Ida Novak, not to the caller Ian MacLeod" in said
    # The caller's own order, or a case that only looks the other person's order up and declines: nothing said.
    assert _writes_for_someone_else(case("1044"), tmp_path, {"get_order"}) == ""
    assert _writes_for_someone_else(case("1043", steps=("get_order",)), tmp_path, {"get_order"}) == ""


def test_a_control_only_a_judge_grades_keeps_a_mixed_case_as_written_and_never_holds_it():
    """The 15aca572 shape: a refusal case with coded lookups whose control names only a judged sub-goal. Judges run in
    real runs, never in proofs: the case is kept as written with that reason, not held as negative_control_passed."""
    from types import SimpleNamespace

    from fi.alk.harness.checks import Outcome
    from fi.alk.harness.controls import played_control
    from fi.alk.harness.prove import BlockedReason

    control = SimpleNamespace(name="issues_duplicate_refund", why="refunds twice", must_fail=["refuses_refund_outside_policy"])
    coded = [Outcome("looks_up_order", True), Outcome("checks_existing_refunds", True)]
    judged = played_control(control, coded, [])
    assert judged["judged"] is True and judged["failed"] is None

    proof = Proof(ready=True, solvable=True, vacuous=False, controls=[judged])
    assert proof.blocked_reason() == BlockedReason.JUDGED_ONLY and proof.kept_as_written and not proof.proved
    assert "wrong-way examples (issues_duplicate_refund) are graded only by a judge, which runs in real runs" in proof.why()

    # Beside a control code fails, the judged one is not counted against the case.
    wrong_lookup = SimpleNamespace(name="skips_lookup", why="never looks up", must_fail=["looks_up_order"])
    failed = played_control(wrong_lookup, [Outcome("looks_up_order", False, "not done"), Outcome("checks_existing_refunds", True)], [])
    mixed = Proof(ready=True, solvable=True, vacuous=False, controls=[judged, failed])
    assert mixed.controls_failed and mixed.blocked_reason() is None and mixed.proved
