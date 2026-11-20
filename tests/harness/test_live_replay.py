"""After a held-case round, the next live pass proves again only the cases the round changed, unless something the
cases share changed: then it proves them all, as before."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fi.alk.harness import authoring_runtime_validation as validation
from fi.alk.harness import live_proof
from fi.alk.harness.catalogue import Catalogue, SubGoal, save_catalogue
from fi.alk.harness.scenario import Scenario
from fi.alk.harness.scenario_tools import write_scenarios

CHECK = "def check(world, calls):\n    return None\n"


def _authoring(root: Path) -> Path:
    goals = Catalogue(
        sub_goals=[
            SubGoal(name="looked_up", what="The order is looked up.", check=CHECK),
            SubGoal(name="refunded", what="The refund is made.", check=CHECK),
        ]
    )
    save_catalogue(goals, root)
    (root / "contract.json").write_text(json.dumps({"agent": "shop"}), encoding="utf-8")
    write_scenarios(
        [
            Scenario(name="lookup", scenario_key="k-lookup", instruction="Look it up.", sub_goals=["looked_up"]),
            Scenario(name="refund", scenario_key="k-refund", instruction="Refund it.", sub_goals=["refunded"]),
            Scenario(name="other", scenario_key="k-other", instruction="Look it up too.", sub_goals=["looked_up"]),
        ],
        root,
        goals,
    )
    return root


def _edit(root: Path, name: str, instruction: str) -> None:
    path = root / "scenarios" / name / "scenario.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["instruction"] = instruction
    path.write_text(json.dumps(body), encoding="utf-8")


def test_only_the_cases_a_round_changed_or_added_are_proved_again(tmp_path):
    root = _authoring(tmp_path)
    before = validation.proof_inputs(root)

    _edit(root, "refund", "Refund it, the payment failed.")
    # A sub-goal only the changed case names may change with it.
    catalogue = json.loads((root / "sub_goals.json").read_text(encoding="utf-8"))
    for goal in catalogue["sub_goals"]:
        if goal["name"] == "refunded":
            goal["what"] = "No refund is claimed when the payment fails."
    (root / "sub_goals.json").write_text(json.dumps(catalogue), encoding="utf-8")
    # Run records and the derived index never count as a change.
    (root / "proofs.json").write_text("{}", encoding="utf-8")
    (root / "scenarios.json").write_text("[]", encoding="utf-8")

    assert validation.cases_to_replay(before, validation.proof_inputs(root)) == {"k-refund"}


def test_a_shared_change_proves_every_case_again(tmp_path):
    root = _authoring(tmp_path)
    before = validation.proof_inputs(root)
    _edit(root, "refund", "Refund it, the payment failed.")
    (root / "contract.json").write_text(json.dumps({"agent": "shop", "notes": "changed"}), encoding="utf-8")

    assert validation.cases_to_replay(before, validation.proof_inputs(root)) is None


def test_a_sub_goal_an_unchanged_case_names_proves_every_case_again(tmp_path):
    root = _authoring(tmp_path)
    before = validation.proof_inputs(root)
    catalogue = json.loads((root / "sub_goals.json").read_text(encoding="utf-8"))
    for goal in catalogue["sub_goals"]:
        if goal["name"] == "looked_up":
            goal["what"] = "The order is looked up by its number."
    (root / "sub_goals.json").write_text(json.dumps(catalogue), encoding="utf-8")

    assert validation.cases_to_replay(before, validation.proof_inputs(root)) is None


def _play(root: Path, monkeypatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    async def prove_live(**kwargs):
        seen["keys"] = kwargs.get("keys")
        return {}

    monkeypatch.setattr(live_proof, "prove_live", prove_live)
    asyncio.run(
        validation.play_this_attempt(
            authoring=root, contract=None, runtime=None, provider=None, factory=None, work=root, seed=0
        )
    )
    return seen


def test_the_next_live_pass_plays_only_the_changed_cases_and_keeps_the_others(tmp_path, monkeypatch):
    root = _authoring(tmp_path)
    live_proof.write_live(root, {"k-lookup": {"assumed": []}, "k-refund": {"assumed": []}, "k-other": {"assumed": []}})
    before = validation.proof_inputs(root)
    _edit(root, "refund", "Refund it, the payment failed.")
    validation.mark_replay(root, before)

    seen = _play(root, monkeypatch)

    assert seen["keys"] == {"k-refund"}
    assert set(live_proof.load_live(root)) == {"k-lookup", "k-other"}
    assert not (root / validation.REPLAY_FILE).exists()
    # With no round before it, the next pass proves every case and keeps no earlier result.
    assert _play(root, monkeypatch)["keys"] is None and live_proof.load_live(root) == {}


def test_a_shared_change_after_the_round_proves_every_case(tmp_path, monkeypatch):
    root = _authoring(tmp_path)
    live_proof.write_live(root, {"k-lookup": {"assumed": []}})
    before = validation.proof_inputs(root)
    _edit(root, "refund", "Refund it, the payment failed.")
    validation.mark_replay(root, before)
    # An environment repair between the round and the next pass.
    (root / "contract.json").write_text(json.dumps({"agent": "shop", "notes": "repaired"}), encoding="utf-8")

    assert _play(root, monkeypatch)["keys"] is None and live_proof.load_live(root) == {}
