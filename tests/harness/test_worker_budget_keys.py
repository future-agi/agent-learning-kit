"""A budget per worker names only the contract's workers."""

from __future__ import annotations

import json

from fi.alk.harness.scenario import Scenario
from fi.alk.harness.scenario_tools import _budgets_for_unknown_workers


def _scenario(budget):
    return Scenario.model_validate({"name": "s", "instruction": "Ask for a refund.", "worker_budget_seconds": budget})


def test_a_budget_for_a_worker_the_contract_does_not_declare_is_refused_with_the_declared_ones(tmp_path):
    (tmp_path / "contract.json").write_text(json.dumps({"workers": [{"id": "refund"}, {"id": "order_lookup"}]}))
    said = _budgets_for_unknown_workers(_scenario({"refnud": 2}), tmp_path)
    assert "refnud is not one of the contract's workers" in said and "order_lookup, refund" in said
    assert _budgets_for_unknown_workers(_scenario({"refund": 2}), tmp_path) == ""
    # One number for all workers names none.
    assert _budgets_for_unknown_workers(_scenario(30), tmp_path) == ""
