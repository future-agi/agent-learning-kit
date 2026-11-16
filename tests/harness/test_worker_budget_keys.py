"""A budget per worker names only the contract's workers; whether it can be held is the trial's to say."""

from __future__ import annotations

import json

from fi.alk.harness import call_runner as cr
from fi.alk.harness.scenario import Scenario
from fi.alk.harness.scenario_tools import _budgets_for_unknown_workers


def _scenario(budget):
    return Scenario.model_validate({"name": "s", "instruction": "Ask for a refund.", "worker_budget_seconds": budget})


def test_a_budget_for_a_worker_the_contract_does_not_declare_is_refused(tmp_path):
    separate = {"binding": "separate", "address_env": "X_URL"}
    (tmp_path / "contract.json").write_text(
        json.dumps({"workers": [{"id": "refund", **separate}, {"id": "order_lookup", **separate}]})
    )
    assert _budgets_for_unknown_workers(_scenario({"refnud": 2}), tmp_path)
    assert not _budgets_for_unknown_workers(_scenario({"refund": 2}), tmp_path)
    # One number for all workers names none.
    assert not _budgets_for_unknown_workers(_scenario(30), tmp_path)


def test_a_budget_a_shared_worker_cannot_be_held_to_is_kept_and_its_trial_is_held(tmp_path):
    shared = {"id": "refund", "binding": "shared"}
    separate = {"id": "lookup", "binding": "separate", "address_env": "LOOKUP_URL"}
    (tmp_path / "contract.json").write_text(json.dumps({"workers": [shared, separate]}))
    (tmp_path / "generated" / "tool-proxy").mkdir(parents=True)
    (tmp_path / "generated" / "tool-proxy" / "door.json").write_text('{"worker_deadlines": "per_worker"}')
    assert not _budgets_for_unknown_workers(_scenario({"refund": 2}), tmp_path)
    *_, notices = cr.trial_worker_deadlines({"worker_budget_seconds": {"refund": 2}}, tmp_path)
    assert [one["code"] for one in notices] == [cr.WORKER_LIMITS_NOT_ENFORCED]
    *_, notices = cr.trial_worker_deadlines({"worker_budget_seconds": {"lookup": 2}}, tmp_path)
    assert notices == []


def test_a_budget_per_worker_is_kept_when_the_contract_cannot_be_read(tmp_path):
    assert not _budgets_for_unknown_workers(_scenario({"refund": 2}), tmp_path)
    (tmp_path / "contract.json").write_text("{not json")
    assert not _budgets_for_unknown_workers(_scenario({"refund": 2}), tmp_path)
