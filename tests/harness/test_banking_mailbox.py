"""Banking's support mailbox (Wave A stage 1): the emails are linked to the banking records, the
existing dispute lives in the world, and the mailbox scenarios' checks reject a wrong target (the
transaction the customer first named, another card, another thread) rather than only crediting
the call."""

from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest

from fi.alk import environments
from fi.alk.environments import designed
from fi.alk.harness.catalogue import load_catalogue
from fi.alk.harness.checks import reference_of, run_check
from fi.alk.harness.folder import read_folder
from fi.alk.harness.prove import _run
from fi.alk.harness.releases import WORLD

HERE = Path(environments.__file__).parent / "banking"
COMPLAINT = "email_complaint_correction_pickup_lock_and_dispute"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("banking")
    designed.build_environment(HERE, root / "environment")
    shutil.copytree(root / "environment" / WORLD, root / WORLD)
    return root


def _grade(built: Path, key: str, swap: dict[str, str]) -> dict[str, str | None]:
    suite = HERE / "suite"
    scenario = read_folder(suite, key)
    assert scenario is not None
    steps = [copy.deepcopy(step) for step in scenario.solution]
    for step in steps:
        for name, value in swap.items():
            if name in step.arguments:
                step.arguments[name] = value
    world, calls, _refused, _ = _run(
        scenario, built / WORLD, with_solution=True, steps=steps
    )
    try:
        catalogue = load_catalogue(suite)
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


def test_mailbox_is_linked_to_the_records(built: Path) -> None:
    from fi.alk.harness.world.snapshot import restore

    world = restore(built / WORLD)
    try:
        state = world.state()
        customers = {c["email"] for c in state["customers"]}
        assert state["threads"] and state["messages"]
        assert all(
            m["sender"] in customers or m["sender"].endswith("@apexhorizonbank.com")
            for m in state["messages"]
        ), "every email is from one of the bank's customers or from the bank"
        assert any(m["sender"] in customers for m in state["messages"])
        by_thread: dict[str, int] = {}
        for message in state["messages"]:
            by_thread[message["thread_id"]] = by_thread.get(message["thread_id"], 0) + 1
        assert max(by_thread.values()) >= 3, "no multi-message thread with a correction"
        assert [d["dispute_id"] for d in state["disputes"]] == ["D01"]
    finally:
        world.close()


def test_reference_solution_holds(built: Path) -> None:
    assert all(said is None for said in _grade(built, COMPLAINT, {}).values())


@pytest.mark.parametrize(
    ("swap", "failing"),
    [
        (
            {"transaction_id": "TX01"},
            "dispute_filed_and_reported",
        ),  # the charge she first named
        ({"transaction_id": "TX19"}, "dispute_filed_and_reported"),
        ({"card_id": "CARD02"}, "card_locked_upon_confirmation"),
    ],
)
def test_wrong_target_is_rejected(
    built: Path, swap: dict[str, str], failing: str
) -> None:
    said = _grade(built, COMPLAINT, swap)
    assert said[failing], (swap, said)
    assert "done wrong" in said[failing]


def test_draft_on_another_thread_is_rejected(built: Path) -> None:
    said = _grade(
        built, "draft_reply_to_customer_charge_inquiry", {"replyToMessageId": "msg_001"}
    )
    assert (
        said["draft_created_for_review"]
        and "thread" in said["draft_created_for_review"]
    )
