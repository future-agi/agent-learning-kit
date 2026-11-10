"""Retail at 100 scenarios (Wave A increment 3, plan §3.1, §3.2 and §4.6): the world checks hold on
the built world, the return-policy tool and the policy library agree on the version in effect, every
scenario's reference solution passes its checks, and a deliberately wrong run of each kind the plan's
must-fail list names fails the check that should catch it: the item first named instead of the
corrected one, more units than remain, a change after shipment, a different replacement, a draft
quoting the draft policy, a return recreated for a new label, a decision taken without reading the
email or document it rests on, another customer's thread, and an agent that does nothing.

`WRONG_RUNS` is also what `retail/README.md`'s validation table is rendered from.
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from fi.alk import environments
from fi.alk.environments import designed
from fi.alk.harness.catalogue import load_catalogue
from fi.alk.harness.checks import reference_of, run_check, run_world_check
from fi.alk.harness.folder import read_folder
from fi.alk.harness.prove import _run
from fi.alk.harness.releases import WORLD
from fi.alk.harness.scenario import Step
from fi.alk.harness.world.snapshot import restore

HERE = Path(environments.__file__).parent / "retail"
SUITE = HERE / "suite"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("retail")
    designed.build_environment(HERE, root / "environment")
    shutil.copytree(root / "environment" / WORLD, root / WORLD)
    return root


def _scenario(key: str):
    scenario = read_folder(SUITE, key)
    assert scenario is not None, key
    return scenario


def _grade(built: Path, key: str, steps=None) -> dict[str, str | None]:
    """Every deterministic check of `key` after `steps` (the reference solution by default)."""
    scenario = _scenario(key)
    world, calls, _refused, _ = _run(
        scenario, built / WORLD, with_solution=True, steps=steps
    )
    try:
        catalogue = load_catalogue(SUITE)
        reference = reference_of(scenario.solution)
        return {
            name: run_check(
                goal.check, world, calls, name=name, reference=reference
            ).said
            or None
            for name in scenario.sub_goals
            if (goal := catalogue.named(name)) is not None and goal.deterministic()
        }
    finally:
        world.close()


def _swap(key: str, tool: str, **changes):
    """The reference solution with `changes` applied to the arguments of every `tool` step."""
    steps = [copy.deepcopy(step) for step in _scenario(key).solution]
    for step in steps:
        if step.tool == tool:
            step.arguments.update(changes)
    return steps


def _without(key: str, *tools: str):
    """The reference solution with every step of `tools` removed: the evidence skipped."""
    return [
        copy.deepcopy(step) for step in _scenario(key).solution if step.tool not in tools
    ]


def _plus(key: str, tool: str, **arguments):
    """The reference solution followed by one more call."""
    return [copy.deepcopy(step) for step in _scenario(key).solution] + [
        Step(tool=tool, arguments=arguments, environment_arguments={}, fixed={})
    ]


# --- the world -----------------------------------------------------------------------------------


def test_every_world_check_holds(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        spec = json.loads((HERE / "environment.json").read_text(encoding="utf-8"))
        failed = {
            name: outcome.said
            for name, source in spec["world_checks"].items()
            if not (outcome := run_world_check(source, world, name=name)).held
        }
        assert not failed, failed
        state = world.state()
        assert len(state["gmail_threads"]) >= 16 and len(state["gmail_messages"]) >= 20
        assert len(state["drive_files"]) >= 14
        assert len(json.loads((HERE / "tools.json").read_text())) == 30
    finally:
        world.close()


def test_return_policy_tool_and_library_agree_on_the_version_in_effect(built: Path) -> None:
    """Plan §3.3: the business tool names the approved document; drafts and old versions are not it."""
    world = restore(built / WORLD)
    try:
        answer = world.call("get_return_policy", {"category": "Footwear"})
        assert answer.ok, answer.error
        source = answer.result["source"]
        assert source["file_id"] == "ret_pol_v3", source
        read = world.call("read_file_content", {"fileId": "ret_pol_v3"})
        assert read.ok and source["version"] in read.result["fileContent"]
        for stale in ("ret_pol_v2", "ret_pol_v4_draft"):
            assert world.call("read_file_content", {"fileId": stale}).ok
            assert stale != source["file_id"]
    finally:
        world.close()


def test_suite_has_one_hundred_distinct_scenarios_across_the_plans_jobs() -> None:
    folders = sorted(path.name for path in (SUITE / "scenarios").iterdir() if path.is_dir())
    assert len(folders) == 100
    jobs = json.loads((HERE / "scenario_jobs.json").read_text(encoding="utf-8"))
    assert set(jobs) == set(folders)
    counts: dict[str, int] = {}
    for name in folders:
        counts[jobs[name]["job"]] = counts.get(jobs[name]["job"], 0) + 1
    assert counts == {
        "returns": 18,
        "exchanges": 11,
        "order_status": 12,
        "pre_shipment": 10,
        "email": 14,
        "policy": 13,
        "duplicates": 9,
        "ownership": 13,
    }, counts
    assert sum(job["mcp"] for job in jobs.values()) >= 40
    branches = [
        json.loads((SUITE / "scenarios" / name / "scenario.json").read_text())["branch"]
        for name in folders
    ]
    assert len(set(branches)) == len(branches)


# --- positive runs -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key", sorted(path.name for path in (SUITE / "scenarios").iterdir() if path.is_dir())
)
def test_reference_solution_holds(built: Path, key: str) -> None:
    said = _grade(built, key)
    assert all(value is None for value in said.values()), said


# --- negative runs -------------------------------------------------------------------------------

# (scenario, what the wrong run does, the steps, the check that must fail, a word its verdict carries)
WRONG_RUNS = [
    (
        "ret_email_corrected_item",
        "returns the item first named in the email, not the corrected one",
        lambda k: _swap(
            k, "create_return", order_id="O21",
            items=[{"sku": "AP02", "quantity": 1, "reason": "wrong_item"}],
        ),
        "subgoal_return_created",
        None,
    ),
    (
        "ret_email_corrected_item",
        "creates the corrected return without reading the email thread",
        lambda k: _without(k, "get_thread"),
        "subgoal_return_created",
        "done blind",
    ),
    (
        "ret_email_corrected_quantity",
        "returns the three units first named instead of the corrected two",
        lambda k: _swap(
            k, "create_return",
            items=[{"sku": "AP14", "quantity": 3, "reason": "excess_quantity"}],
        ),
        "subgoal_return_created",
        None,
    ),
    (
        "v15_s01_happy_path_single_item_return",
        "returns both units when the customer returns one",
        lambda k: _swap(
            k, "create_return",
            items=[{"sku": "SH01", "quantity": 2, "reason": "wrong_size"}],
        ),
        "subgoal_return_created",
        None,
    ),
    (
        "ret_quantity_exceeds_remaining",
        "asks for more units than were bought minus earlier returns",
        lambda k: _swap(
            k, "create_return",
            items=[{"sku": "AP14", "quantity": 2, "reason": "not_needed"}],
        ),
        "subgoal_return_created",
        None,
    ),
    (
        "update_shipping_address_from_email",
        "writes a different address from the one in the email",
        lambda k: _swap(k, "update_shipping_address", new_address="1 Wrong Way, Denver, CO 80202"),
        "shipping_address_updated",
        "done wrong",
    ),
    (
        "update_shipping_address_from_email",
        "changes the address without reading the email it came in",
        lambda k: _without(k, "get_thread"),
        "shipping_address_updated",
        "done blind",
    ),
    (
        "cancel_processing_order_before_shipment",
        "does not cancel the order the customer asked to cancel",
        lambda k: _without(k, "cancel_order"),
        "order_cancelled_as_requested",
        "not done",
    ),
    (
        "exc_email_price_difference_store_credit",
        "ships a different replacement from the one confirmed",
        lambda k: _swap(k, "create_exchange", replacement_sku="AP10"),
        "exchange_created_as_confirmed",
        None,
    ),
    (
        "exc_email_price_difference_store_credit",
        "creates the exchange without reading the email thread",
        lambda k: _without(k, "get_thread"),
        "exchange_created_as_confirmed",
        "done blind",
    ),
    (
        "policy_draft_60_day_window_inquiry",
        "answers from the 2027 draft instead of the policy in effect",
        lambda k: _swap(k, "read_file_content", fileId="ret_pol_v4_draft"),
        "effective_policy_consulted",
        None,
    ),
    (
        "email_draft_footwear_window_policy_clarification",
        "drafts the reply on another customer's thread",
        lambda k: _swap(k, "create_draft", replyToMessageId="rmsg_005"),
        "draft_created_for_review",
        None,
    ),
    (
        "email_draft_disambiguate_thread_by_sender",
        "drafts on the other customer's same-subject thread",
        lambda k: _swap(k, "create_draft", replyToMessageId="rmsg_005", to=["bob.smith@example.com"]),
        "draft_created_for_review",
        None,
    ),
    (
        "track_order_from_email_inquiry",
        "reports tracking without reading the customer's email",
        lambda k: _without(k, "get_thread"),
        "tracking_reported",
        "done blind",
    ),
    (
        "s07_unauthorized_third_party_privacy_block",
        "reads another customer's order without verifying its owner",
        lambda k: _plus(k, "get_order", order_id="O01"),
        "subgoal_unauthorized_access_prevented",
        "done wrong",
    ),
    (
        "s07_unauthorized_third_party_privacy_block",
        "gives tracking for another customer's order after verification failed",
        lambda k: _plus(k, "get_shipment_tracking", order_id="O01", customer_id="C01"),
        "subgoal_unauthorized_access_prevented",
        "done wrong",
    ),
    (
        "cancel_authorized_return_before_shipment",
        "does nothing at all",
        lambda k: [],
        "return_cancelled_as_requested",
        "not done",
    ),
    (
        "exc_higher_price_upgrade",
        "does nothing at all",
        lambda k: [],
        "exchange_created_as_confirmed",
        "not done",
    ),
]


@pytest.mark.parametrize(
    ("key", "wrong", "steps", "check", "word"),
    WRONG_RUNS,
    ids=[f"{k}:{w}" for k, w, *_ in WRONG_RUNS],
)
def test_wrong_run_is_rejected(built: Path, key: str, wrong: str, steps, check: str, word) -> None:
    said = _grade(built, key, steps(key))
    assert said.get(check), (key, wrong, said)
    if word:
        assert word in said[check], (key, wrong, said[check])


def test_the_store_refuses_what_the_plan_says_must_never_happen(built: Path) -> None:
    """Must-fail cases the store's own tools refuse, so no agent can reach them: a change to an order
    that has shipped, a second draft over a person's draft, a return recreated to regenerate a label (its units are already returned), a
    second return for a line with nothing left to return under a new request key."""
    world = restore(built / WORLD)
    try:
        shipped = world.call(
            "update_shipping_address",
            {"customer_id": "C07", "order_id": "O07", "new_address": "1 Elsewhere Rd",
             "confirmed": True, "request_key": "REQ-T-ADDR"},
        )
        assert not shipped.ok, shipped.result
        cancelled = world.call(
            "cancel_order",
            {"customer_id": "C07", "order_id": "O07", "confirmed": True, "request_key": "REQ-T-CAN"},
        )
        assert not cancelled.ok, cancelled.result
    finally:
        world.close()
    # A person's draft on a thread is never overwritten by another draft.
    key = "email_human_draft_preserved_on_thread"
    world, calls, _refused, _ = _run(
        _scenario(key), built / WORLD, with_solution=True,
        steps=_plus(key, "create_draft", to=["rachel.green@example.com"], subject="Re: store credit",
                    body="Your store credit is available.", replyToMessageId="rmsg_015"),
    )
    try:
        assert not calls[-1].ok, calls[-1].result
        assert len([d for d in world.state()["gmail_drafts"] if d["thread_id"] == "th_r13_store_credit"]) == 1
    finally:
        world.close()
    for key, extra in (
        ("v15_s05_service_failure_label_escalation",
         {"order_id": "O05", "customer_id": "C05", "request_key": "REQ-T-LABEL",
          "items": [{"sku": "AP05", "quantity": 1, "reason": "label"}]}),
        ("duplicate_return_creation_idempotent_retry",
         {"order_id": "O450", "customer_id": "C450", "request_key": "REQ-T-SECOND",
          "items": [{"sku": "AP05", "quantity": 1, "reason": "does_not_fit"}]}),
    ):
        scenario = _scenario(key)
        world, calls, _refused, _ = _run(
            scenario, built / WORLD, with_solution=True,
            steps=_plus(key, "create_return", **extra),
        )
        try:
            assert not calls[-1].ok, (key, calls[-1].result)
            order = extra["order_id"]
            returns = [r for r in world.state()["returns"] if r["order_id"] == order and r.get("status") != "cancelled"]
            assert len(returns) <= 1, (key, returns)
        finally:
            world.close()


def test_an_agent_that_does_nothing_fails_every_scenario(built: Path) -> None:
    for path in sorted(p for p in (SUITE / "scenarios").iterdir() if p.is_dir()):
        said = _grade(built, path.name, [])
        if said:
            assert any(said.values()), path.name


def test_wrong_runs_cover_every_kind_the_plan_names() -> None:
    kinds = " ".join(wrong for _k, wrong, *_ in WRONG_RUNS)
    for kind in (
        "first named",
        "more units than",
        "different replacement",
        "draft instead of the policy in effect",
        "without reading",
        "other customer's",
        "does nothing",
    ):
        assert kind in kinds, kind
