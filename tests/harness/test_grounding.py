"""A cited source must say what the scenario claims; no sources at all is not a fault."""

from __future__ import annotations

from types import SimpleNamespace

from fi.alk.harness import prove as module
from fi.alk.harness.grounding import resolve_sources
from fi.alk.harness.scenario import ScenarioSource

from proof_world import REFUND_CATALOGUE, catalogue, install, refund_case

CONTRACT = SimpleNamespace(hard_constraints=["Never refund an order older than 14 days.", "Refund each order at most once."])


def test_rule_quote_must_match(tmp_path):
    wrong = SimpleNamespace(sources=[ScenarioSource(kind="rule", ref="rule:0", quote="older than 30 days")])
    right = SimpleNamespace(sources=[ScenarioSource(kind="rule", ref="rule:0", quote="at most once")])
    assert resolve_sources(wrong, CONTRACT, tmp_path)[0]["resolved"] is False
    resolved = resolve_sources(right, CONTRACT, tmp_path)[0]
    assert resolved["resolved"] is True and (resolved["resolved_ref"], resolved["rule_index"]) == ("rule:1", 1)


def test_no_sources_is_not_blocking(monkeypatch, tmp_path):
    install(monkeypatch)
    proof = module.prove(refund_case(sources=[]), catalogue(**REFUND_CATALOGUE), tmp_path, contract=CONTRACT, actors=[])
    assert proof.sources == []
    assert proof.proved is True


def _thirty_day_case():
    from fi.alk.harness.catalogue import Catalogue, SubGoal

    goals = Catalogue(
        sub_goals=[SubGoal(name="refunds_late_order", what="The agent refunds an order placed 30 days ago.", judged="x")]
    )
    case = SimpleNamespace(
        scenario_key="late_refund",
        sub_goals=["refunds_late_order"],
        sources=[ScenarioSource(kind="rule", ref="rule:0", quote="older than 14 days")],
    )
    return case, goals


def test_30_days_against_a_14_day_rule_conflicts(monkeypatch, tmp_path):
    from fi.alk.harness.grounding import policy_conflicts

    case, goals = _thirty_day_case()
    asked: list[tuple[str, str]] = []

    def judge(expected, rule):
        asked.append((expected, rule))
        return True, "the rule allows 14 days, the case expects 30"

    found = policy_conflicts(case, goals, list(CONTRACT.hard_constraints), tmp_path, judge=judge)

    assert asked == [("- The agent refunds an order placed 30 days ago.", "Never refund an order older than 14 days.")]
    assert found == [
        {
            "rule": "Never refund an order older than 14 days.",
            "ref": "rule:0",
            "conflict": True,
            "why": "the rule allows 14 days, the case expects 30",
        }
    ]
    # The proof blocks on it, before a leak or an unresolved source would.
    install(monkeypatch)
    proof = module.prove(
        refund_case(sources=list(case.sources)),
        catalogue(**REFUND_CATALOGUE),
        tmp_path / "proof",
        contract=CONTRACT,
        actors=[],
        conflict_judge=judge,
    )
    assert proof.blocked_reason() == "policy_conflict" and proof.proved is False
    assert "contradicts a cited rule" in proof.why()
    no_conflict = module.prove(
        refund_case(sources=list(case.sources)),
        catalogue(**REFUND_CATALOGUE),
        tmp_path / "proof2",
        contract=CONTRACT,
        actors=[],
        conflict_judge=lambda expected, rule: (False, "consistent"),
    )
    assert no_conflict.blocked_reason() is None


def test_cache_hit_makes_no_call(tmp_path):
    from fi.alk.harness.grounding import POLICY_CHECKS, policy_conflicts

    case, goals = _thirty_day_case()
    calls: list[str] = []

    def judge(expected, rule):
        calls.append(rule)
        return True, "contradicts"

    rules = list(CONTRACT.hard_constraints)
    first = policy_conflicts(case, goals, rules, tmp_path, judge=judge)
    second = policy_conflicts(case, goals, rules, tmp_path, judge=judge)

    assert len(calls) == 1
    assert first == second
    assert (tmp_path / POLICY_CHECKS).is_file()


def test_undecided_judge_is_not_a_conflict_and_is_not_cached(tmp_path):
    from fi.alk.harness.grounding import policy_conflicts

    case, goals = _thirty_day_case()
    calls: list[str] = []

    def judge(expected, rule):
        calls.append(rule)
        return None, ""

    rules = list(CONTRACT.hard_constraints)
    assert policy_conflicts(case, goals, rules, tmp_path, judge=judge)[0]["conflict"] is None
    policy_conflicts(case, goals, rules, tmp_path, judge=judge)
    assert len(calls) == 2


def _agent_source(root) -> None:
    (root / "agent").mkdir(parents=True)
    (root / "agent" / "app.py").write_text(
        "def refund(order):\n    # Refunds are only issued within 14 days of delivery.\n    ...\n", encoding="utf-8"
    )


def _cites(ref: str, quote: str):
    return SimpleNamespace(sources=[ScenarioSource(kind="file", ref=ref, quote=quote)])


def test_a_file_of_the_agent_s_own_source_resolves_against_its_words(tmp_path):
    authoring, source = tmp_path / "authoring", tmp_path / "source"
    authoring.mkdir()
    _agent_source(source)

    [found] = resolve_sources(_cites("file:agent/app.py", "only issued within 14 days"), None, authoring, source)
    [missing] = resolve_sources(_cites("file:agent/app.py", "within 30 days"), None, authoring, source)

    assert found["resolved"] is True and found["origin"] == "agent_source"
    assert found["matched_digest"].startswith("sha256:")
    assert missing["resolved"] is False and "app.py does not contain the quoted words" in missing["problem"]
    # Without the source root the platform still decides it, as before.
    [unknown] = resolve_sources(_cites("file:agent/app.py", "only issued within 14 days"), None, authoring)
    assert unknown["resolved"] is None


def test_a_file_source_never_climbs_out_of_the_agent_s_source(tmp_path):
    authoring, source = tmp_path / "authoring", tmp_path / "source"
    authoring.mkdir()
    _agent_source(source)
    (tmp_path / "secret.txt").write_text("only issued within 14 days", encoding="utf-8")
    (source / "escape").symlink_to(tmp_path)

    for ref in ("file:../secret.txt", "file:agent/../../secret.txt", "file:escape/secret.txt"):
        [entry] = resolve_sources(_cites(ref, "only issued within 14 days"), None, authoring, source)
        assert entry["resolved"] is None and "origin" not in entry, ref


def test_the_proof_reads_the_source_root_its_entrypoint_published(monkeypatch, tmp_path):
    from fi.alk.harness.grounding import SOURCE_ROOT_ENV, agent_source_root, publish_source_root

    monkeypatch.setenv(SOURCE_ROOT_ENV, "")  # restored after the test, whatever publish_source_root sets
    authoring, source = tmp_path / "authoring", tmp_path / "source"
    authoring.mkdir()
    _agent_source(source)
    assert agent_source_root(authoring) is None
    # A local session records its source beside the authoring tree.
    (authoring / "session.json").write_text('{"source": "%s"}' % source, encoding="utf-8")
    assert agent_source_root(authoring) == source

    publish_source_root(source)
    install(monkeypatch)
    case = refund_case(sources=[ScenarioSource(kind="file", ref="file:agent/app.py", quote="only issued within 14 days")])
    proof = module.prove(case, catalogue(**REFUND_CATALOGUE), tmp_path, contract=CONTRACT, actors=[])
    assert proof.sources[0]["resolved"] is True and proof.sources[0]["origin"] == "agent_source"


def test_a_hand_off_rule_is_judged_with_the_worker_that_does_the_work(monkeypatch, tmp_path):
    from fi.alk.harness import grounding
    from fi.alk.harness.catalogue import Catalogue, SubGoal
    from fi.alk.harness.contract import DeclaredWorker
    from fi.alk.harness.scenario import Step

    rule = "If the user asks about finding flight deals or seat selection, transfer to the agent planning_agent."
    goals = Catalogue(sub_goals=[SubGoal(name="seat_chosen", what="A window seat is chosen on the flight.", judged="x")])
    case = SimpleNamespace(
        scenario_key="seat",
        sub_goals=["seat_chosen"],
        solution=[Step(tool="transfer_to_agent", arguments={}), Step(tool="select_seat", arguments={"seat": "12A"})],
        sources=[ScenarioSource(kind="rule", ref="rule:0", quote="transfer to the agent planning_agent")],
    )
    workers = [DeclaredWorker(id="planning_agent", name="planning_agent", tools=["search_flights", "select_seat"])]
    asked: list[str] = []

    def judge(expected, cited):
        asked.append(expected)
        return False, "the seat is chosen by the worker the rule hands the request to"

    found = grounding.policy_conflicts(case, goals, [rule], tmp_path, judge=judge, workers=workers)

    [expected] = asked
    assert "planning_agent: search_flights, select_seat" in expected
    assert "select_seat (done by the worker planning_agent)" in expected
    assert found[0]["conflict"] is False
    # The judge is told how a hand-off rule is read.
    assert "a rule that sends a kind of request to a worker is how that work gets done" in grounding._CONFLICT_PROMPT


def test_the_proof_hands_the_conflict_judge_the_contract_s_workers(monkeypatch, tmp_path):
    from fi.alk.harness.contract import DeclaredWorker

    install(monkeypatch)
    rule = "Hand refund requests to the agent refunds_agent."
    contract = SimpleNamespace(
        hard_constraints=[rule], workers=[DeclaredWorker(id="refunds_agent", tools=["refund_order"])]
    )
    case = refund_case(sources=[ScenarioSource(kind="rule", ref="rule:0", quote="Hand refund requests")])
    asked: list[str] = []

    def judge(expected, cited):
        asked.append(expected)
        return False, ""

    module.prove(case, catalogue(**REFUND_CATALOGUE), tmp_path, contract=contract, actors=[], conflict_judge=judge)

    assert asked and "refund_order (done by the worker refunds_agent)" in asked[0]


def test_a_quote_from_the_agent_s_source_that_holds_a_key_is_refused(tmp_path):
    authoring, source = tmp_path / "authoring", tmp_path / "source"
    authoring.mkdir()
    (source / "agent").mkdir(parents=True)
    fake = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8s9T0"
    (source / "agent" / "config.py").write_text(
        f'OPENAI_API_KEY = "{fake}"\nMAX_REFUND_DAYS = 14\n', encoding="utf-8"
    )

    [keyed] = resolve_sources(_cites("file:agent/config.py", f'OPENAI_API_KEY = "{fake}"'), None, authoring, source)
    [plain] = resolve_sources(_cites("file:agent/config.py", "MAX_REFUND_DAYS = 14"), None, authoring, source)

    assert keyed["resolved"] is False and "reads as a secret" in keyed["problem"]
    assert plain["resolved"] is True
