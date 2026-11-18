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


def test_letting_both_participants_update_an_order_reaches_what_the_world_permits(tmp_path):
    """The live miss: asked to let both workers mark orders, the builder changed the tool's values and description and
    said it was done, while each worker's allowed tools stayed as they were. The one tool that changes who may use a
    tool must change what the world then permits for each."""
    from fi.alk.harness.amend import set_worker_tools
    from fi.alk.harness.bundle_author_v2 import _worker_permissions

    contract = AgentContract.model_validate(
        {
            "agent_name": "returns",
            "summary": "Returns desk.",
            "tools": [{"name": name, "description": name, "args": ["order"]}
                      for name in ("get_order", "issue_refund", "update_order_status")],
            "real_use_cases": ["A customer asks for a refund."],
            "workers": [
                {"id": "order_lookup", "tools": ["get_order", "update_order_status"], "allowed_tools": ["get_order"]},
                {"id": "refund", "tools": ["issue_refund"]},
            ],
        }
    )
    _stored(tmp_path, contract, [])

    def permitted() -> dict:
        saved = json.loads((tmp_path / CONTRACT).read_text(encoding="utf-8"))
        return {key: value["allow"] for key, value in _worker_permissions(saved).items()}

    done, said = set_worker_tools(contract, tmp_path, worker="order_lookup",
                                  allowed_tools=["get_order", "update_order_status"], why="it marks returns requested")
    assert done, said
    done, said = set_worker_tools(contract, tmp_path, worker="refund",
                                  allowed_tools=["issue_refund", "update_order_status"], why="it marks orders refunded")
    assert done, said
    assert permitted() == {
        "worker:order_lookup": ["get_order", "update_order_status"],
        "worker:refund": ["issue_refund", "update_order_status"],
    }
    # Never a tool the agent does not have, an unknown participant, or no reason.
    assert not set_worker_tools(contract, tmp_path, worker="refund", allowed_tools=["delete_all"], why="x")[0]
    assert not set_worker_tools(contract, tmp_path, worker="billing", allowed_tools=["get_order"], why="x")[0]
    assert not set_worker_tools(contract, tmp_path, worker="refund", allowed_tools=["get_order"], why=" ")[0]


def test_the_edit_chat_offers_the_participant_tools_change_and_asks_before_it():
    from fi.alk.harness.hosted_chat_entrypoint import _ASKS_ALONE, _NEEDS_CONFIRMATION, _WRITING_TOOLS
    from fi.alk.harness.scenario_tools import TOOL_NAMES

    assert "set_worker_tools" in TOOL_NAMES
    assert "set_worker_tools" in _NEEDS_CONFIRMATION & _ASKS_ALONE & _WRITING_TOOLS
