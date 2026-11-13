"""An edit to the contract writes back the edit, and no empty fields the stored contract never had."""

from __future__ import annotations

import json

from fi.alk.harness.amend import CONTRACT, add_rule
from fi.alk.harness.contract import AgentContract


def _stored(tmp_path, contract: AgentContract, drop: list[tuple[str, ...]]) -> dict:
    data = contract.model_dump()
    for path in drop:
        node = data
        for key in path[:-1]:
            node = node[key]
        node.pop(path[-1], None)
    (tmp_path / CONTRACT).write_text(json.dumps(data), encoding="utf-8")
    return data


def test_adding_a_rule_does_not_add_an_empty_loads_to_the_data_store(tmp_path):
    contract = AgentContract.model_validate(
        {
            "agent_name": "deals",
            "summary": "Applies discounts.",
            "tools": [
                {
                    "name": "apply_discount",
                    "description": "Apply a discount.",
                    "args": ["deal_id"],
                }
            ],
            "data_store": {"engine": "sqlite", "database": "deals.db"},
            "real_use_cases": ["A customer asks for a discount on a deal."],
        }
    )
    before = _stored(tmp_path, contract, [("data_store", "loads")])
    assert "loads" not in before["data_store"]

    done, said = add_rule(
        contract,
        tmp_path,
        rule="Confirm the company name before any discount.",
        why="asked",
    )
    assert done, said
    after = json.loads((tmp_path / CONTRACT).read_text(encoding="utf-8"))
    assert "loads" not in after["data_store"]
    assert (
        after["hard_constraints"][-1] == "Confirm the company name before any discount."
    )
    assert after["data_store"]["database"] == "deals.db"


def test_a_field_the_contract_already_had_is_kept_even_when_empty(tmp_path):
    contract = AgentContract.model_validate(
        {
            "agent_name": "deals",
            "summary": "Applies discounts.",
            "tools": [
                {
                    "name": "apply_discount",
                    "description": "Apply a discount.",
                    "args": ["deal_id"],
                }
            ],
            "data_store": {"engine": "sqlite", "database": "deals.db"},
            "real_use_cases": ["A customer asks for a discount on a deal."],
        }
    )
    _stored(tmp_path, contract, [])
    done, said = add_rule(contract, tmp_path, rule="Never apply over 20%.", why="asked")
    assert done, said
    after = json.loads((tmp_path / CONTRACT).read_text(encoding="utf-8"))
    assert after["data_store"]["loads"] == ""
