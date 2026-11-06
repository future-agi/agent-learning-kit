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


# The world check itself: every message is checked, not only the last one (the defect Rishav found
# at 36bc0bb2: replacing D01 with a nonexistent D99 still sealed), and a customer's deliberately
# false claim in a thread labelled claims_unverified is scenario evidence, not a fact.


class _State:
    def __init__(self, state: dict) -> None:
        self._state = state

    def state(self) -> dict:
        return self._state


def _consistency_check():
    import json

    source = json.loads((HERE / "environment.json").read_text(encoding="utf-8"))[
        "world_checks"
    ]["check_email_banking_consistency"]
    namespace: dict = {}
    exec(source, namespace)  # noqa: S102 - the environment's own check, as the seal runs it
    return namespace["check"]


@pytest.fixture(scope="module")
def seeded_state(built: Path) -> dict:
    from fi.alk.harness.world.snapshot import restore

    world = restore(built / WORLD)
    try:
        return copy.deepcopy(world.state())
    finally:
        world.close()


def _message(state: dict, message_id: str) -> dict:
    return next(m for m in state["messages"] if m["id"] == message_id)


def test_consistency_check_holds_on_the_seeded_mailbox(seeded_state: dict) -> None:
    assert _consistency_check()(_State(seeded_state)) is None
    # The bank's dispute confirmation is not the last message, so the old loop never saw it.
    assert _message(seeded_state, "msg_005")["id"] != seeded_state["messages"][-1]["id"]


def test_nonexistent_dispute_in_any_message_fails(seeded_state: dict) -> None:
    state = copy.deepcopy(seeded_state)
    bank_reply = _message(state, "msg_005")  # the bank confirming D01 to Hannah
    bank_reply["body"] = bank_reply["body"].replace("D01", "D99")
    said = _consistency_check()(_State(state))
    assert said and "D99" in said and "msg_005" in said


def test_another_customers_dispute_fails(seeded_state: dict) -> None:
    state = copy.deepcopy(seeded_state)
    state["disputes"].append(
        {
            **state["disputes"][0],
            "dispute_id": "D02",
            "customer_id": "C01",
            "transaction_id": "TX17",
        }
    )
    bank_reply = _message(state, "msg_005")
    bank_reply["body"] = bank_reply["body"].replace("D01", "D02")
    said = _consistency_check()(_State(state))
    assert said and "D02" in said and "of C01" in said


def test_unverified_claim_by_a_customer_is_allowed_but_not_by_the_bank(
    seeded_state: dict,
) -> None:
    state = copy.deepcopy(seeded_state)
    complaint = _message(state, "msg_003")  # Alice's correction, a customer message
    complaint["body"] += " Your colleague told me this was already dispute D99."
    assert _consistency_check()(_State(state)), "a false claim fails without the label"
    state["thread_labels"].append(
        {"thread_id": "th_c01_complaint", "label_id": "claims_unverified"}
    )
    assert _consistency_check()(_State(state)) is None, (
        "labelled, the claim is evidence"
    )
    bank_reply = _message(state, "msg_002")  # the bank's reply in the same thread
    bank_reply["body"] += " We opened dispute D99 for you."
    said = _consistency_check()(_State(state))
    assert said and "D99" in said and "msg_002" in said, (
        "the bank's messages are always checked"
    )
