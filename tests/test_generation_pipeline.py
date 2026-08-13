"""Offline pipeline test: fake LLM, real validators, real emission, real goal machine."""

from __future__ import annotations

import json

import pytest

from fi.alk.generation import (
    AgentContract,
    FakeLLMClient,
    GenerationConfig,
    RepoFolderSource,
    ToolSpec,
    generate,
    smoke_manifest,
    to_alk_scenario,
    validate_scenario,
)

CONTRACT = {
    "agent": "cafe-order",
    "one_liner": "Takes cafe orders over voice.",
    "modality": "voice",
    "conversational": True,
    "hard_constraints": ["A combo requires a drink."],
    "tools": [
        {
            "name": "add_item",
            "args": ["item_id", "size"],
            "arg_values": {"item_id": ["latte", "mocha"], "size": ["M", "L"]},
            "description": "Add an item to the order.",
        },
        {
            "name": "list_order",
            "args": [],
            "arg_values": {},
            "description": "Read back the order.",
        },
    ],
    "data_schema": {"menu": {"latte": {"price": 4.5}, "mocha": {"price": 5.0}}},
    "base_environment": {"summary": "empty order", "seed": {"order": {"items": []}}},
    "real_use_cases": ["Order a latte -> add_item(item_id=latte)"],
    "signature_cases": ["Unknown item is declined"],
    "grading_notes": "The order state carries the truth.",
    "anti_hallucination": ["remove_item (does not exist)", "add_to_cart (wrong name)"],
}

CATALOG = {
    "catalog": [
        {
            "name": "item_added",
            "description": "The requested item is in the order with the right attributes.",
            "default_kind": "tool_call_args",
            "definition_template": {
                "tool": "add_item",
                "args_equal": {"item_id": "<FILL>", "size": "<FILL>"},
            },
        },
        {
            "name": "order_confirmed",
            "description": "The final order was read back.",
            "default_kind": "state",
            "definition_template": {"must": {"order.confirmed": True}},
        },
    ]
}

ROWS = {
    "rows": [
        {
            "id": "latte-medium",
            "use_case": "Order a single item",
            "situation": "The caller wants one medium latte and confirms",
            "why_distinct": "Plain single-item success path",
            "goal": "A medium latte is ordered and confirmed",
        }
    ]
}

SCENARIO = {
    "id": "latte-medium",
    "use_case": "Order a single item",
    "situation": "The caller wants one medium latte and confirms",
    "goal": "A medium latte is ordered and confirmed",
    "description": "A caller orders one medium latte, nothing else. The menu has lattes and mochas; "
    "the order starts empty and the agent must add the right item at the right size.",
    "agent_input": "You are calling a cafe. You want one latte, medium. If asked anything else, "
    "decline politely.",
    "facts": [{"key": "size", "value": "M", "disclosure": "on_request"}],
    "persona": {"name": "Sam"},
    "environment": {
        "seed": {"order": {"items": [], "confirmed": False}},
        "mock_responses": {
            "add_item": {
                "content": "added latte size M",
                "state_updates": {"order": {"items": ["latte_M"], "confirmed": True}},
            }
        },
    },
    "sub_goals": [
        {
            "name": "item_added",
            "milestone": "The latte is added at the requested size",
            "checkpoint": {
                "kind": "tool_call_args",
                "detail": "add_item called with item_id=latte, size=M",
                "deterministic": True,
                "definition": {
                    "tool": "add_item",
                    "args_equal": {"item_id": "latte", "size": "M"},
                },
            },
        },
        {
            "name": "order_confirmed",
            "milestone": "The order ends confirmed",
            "checkpoint": {
                "kind": "state",
                "detail": "order.confirmed is true",
                "deterministic": True,
                "definition": {"must": {"order.confirmed": True}},
            },
        },
        {
            "name": "price_conveyed",
            "milestone": "The caller hears the price",
            "checkpoint": {
                "kind": "conveyed",
                "detail": "the agent states the latte price",
                "deterministic": True,
                "definition": {"must_include_any": ["4.5", "4.50"]},
            },
        },
    ],
    "expected_outcome": {
        "world_state": "The order holds one medium latte and is confirmed",
        "must_convey": ["4.5"],
        "forbidden": ["adding any second item"],
    },
    "max_reasonable_turns": 6,
}

VERDICT = {
    "verdict": "accept",
    "scores": {"worth": 4, "real": 5, "grounded": 5, "checkable": 4, "separation": 5},
    "problems": [],
    "fix_hints": "",
}


@pytest.fixture()
def agent_repo(tmp_path):
    repo = tmp_path / "cafe-agent"
    repo.mkdir()
    (repo / "README.md").write_text("# Cafe order agent\nTakes cafe orders.")
    (repo / "tools.py").write_text("def add_item(item_id, size):\n    ...\n")
    return str(repo)


def test_full_pipeline_offline(agent_repo, tmp_path):
    llm = FakeLLMClient(
        responses=[
            {
                "tool_calls": [
                    {
                        "id": "c1",
                        "name": "submit_contract",
                        "arguments": {"contract": CONTRACT},
                    }
                ]
            },
            CATALOG,
            ROWS,
            SCENARIO,
            VERDICT,
            {"gaps": [], "near_duplicates": []},
        ]
    )
    out = tmp_path / "out"
    result = generate(
        RepoFolderSource(path=agent_repo),
        llm,
        GenerationConfig(n=1, out_dir=str(out)),
    )
    assert len(result.records) == 1
    assert not result.rejected
    record = result.records[0]
    assert record["_review"]["verdict"] == "accept"

    assert (out / "scenarios" / "latte-medium.json").is_file()
    assert (out / "report.md").is_file()
    alk = json.loads((out / "alk" / "latte-medium.json").read_text())
    assert alk["kind"] == "task"
    assert alk["goal"]["states"] == ["item_added", "order_confirmed", "price_conveyed"]
    check_names = {c["name"] for c in alk["verification"]["checks"]}
    assert set(alk["goal"]["states"]) <= check_names
    assert alk["dataset"][0]["knowledge"][0]["disclosure"] == "on_request"


def test_validators_catch_hallucinated_tool():
    contract = AgentContract(
        agent="a",
        tools=[ToolSpec(name="add_item", args=["item_id"])],
        real_use_cases=["x"],
        anti_hallucination=["remove_item (does not exist)"],
    )
    bad = dict(SCENARIO)
    bad = json.loads(json.dumps(bad).replace("add_item", "remove_item"))
    problems = validate_scenario(bad, contract)
    assert any("unknown-tool" in p or "banned-interface" in p for p in problems)


def test_smoke_manifest_state_checks_fire_through_goal_machine():
    contract = AgentContract.model_validate(CONTRACT)
    manifest = smoke_manifest(SCENARIO, contract)
    world = next(
        e
        for e in manifest["simulation"]["environments"]
        if e["type"] == "world_contract"
    )
    assert world["success_conditions"][0]["name"] == "order_confirmed"

    from fi.simulate.environment import WorldContractEnvironment
    from fi.simulate.simulation import goal_machine
    from fi.simulate.simulation.models import ScenarioGoal, VerificationSpec

    env = WorldContractEnvironment(
        name="w",
        initial_state={"order": {"items": ["latte_M"], "confirmed": True}},
        success_conditions=world["success_conditions"],
    )
    snapshot = env.reset()
    verdict = goal_machine.evaluate_settle(
        ScenarioGoal(states=["order_confirmed"], success_state="order_confirmed"),
        VerificationSpec(checks=manifest["scenario"]["verification"]["checks"]),
        environment_state={
            "world_contract": snapshot.state.get("world_contract", env._summary())
        },
    )
    assert verdict["stop"] == "goal_success"
    assert "order_confirmed" in verdict["states_reached"]


def test_alk_scenario_is_typed_and_content_addressed():
    contract = AgentContract.model_validate(CONTRACT)
    scenario = to_alk_scenario(SCENARIO, contract)
    assert scenario.kind == "task"
    assert scenario.version and scenario.version.startswith("sha256:")
    assert scenario.constraints.declared_tools == ["add_item"]


def test_record_drives_runtime_mock_and_python_checks_directly():
    """The golden-artifact property: a generated record feeds the real runtime mock builder and the
    pure-Python checker with no translation step in between."""
    from fi.simulate.environments.chat import _mock_world_from_config

    from fi.alk.generation.checks import evaluate_scenario

    # 1. The record's environment block IS the runtime mock config, verbatim.
    world = _mock_world_from_config(
        {
            "mock_tools": SCENARIO["environment"]["mock_responses"],
            "tool_initial_state": SCENARIO["environment"]["seed"],
        }
    )
    assert world is not None
    world.reset()

    # 2. The agent under test calls a tool; the mock answers and mutates world state.
    result = world.handle_tool_call(
        {"id": "c1", "name": "add_item", "arguments": {"item_id": "latte", "size": "M"}}
    )
    assert result is not None and result.success

    # 3. Run evidence (tool-call log, transcript, final state) feeds plain-Python checks.
    tool_calls = [{"name": "add_item", "arguments": {"item_id": "latte", "size": "M"}}]
    verdicts = evaluate_scenario(
        SCENARIO,
        tool_calls=tool_calls,
        transcript_turns=["That is one medium latte, 4.5 total. Anything else?"],
        final_state=world.state,
    )
    by_name = {v.name: v for v in verdicts}
    assert by_name["item_added"].passed is True
    assert by_name["order_confirmed"].passed is True
    assert by_name["price_conveyed"].passed is True

    # 4. Wrong arguments fail the argument checkpoint: the check tests values, not activity.
    wrong = evaluate_scenario(
        SCENARIO,
        tool_calls=[
            {"name": "add_item", "arguments": {"item_id": "mocha", "size": "L"}}
        ],
        transcript_turns=[],
        final_state=world.state,
    )
    assert {v.name: v for v in wrong}["item_added"].passed is False


def test_validator_rejects_transposed_identifier():
    contract = AgentContract.model_validate(CONTRACT)
    bad = json.loads(json.dumps(SCENARIO))
    bad["sub_goals"][0]["checkpoint"]["definition"]["args_equal"]["item_id"] = (
        "item_latte_big"
    )
    problems = validate_scenario(bad, contract)
    assert any("unknown-id" in p for p in problems)
