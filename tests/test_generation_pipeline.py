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
            "target_failure": "The agent adds the wrong item or size",
            "why_it_matters": "A wrong order reaches a paying customer",
            "goal": "A medium latte is ordered and confirmed",
        }
    ]
}

SCENARIO = {
    "id": "latte-medium",
    "use_case": "Order a single item",
    "situation": "The caller wants one medium latte and confirms",
    "target_failure": "The agent adds the wrong item or size, or never confirms the order",
    "why_it_matters": "A wrong order reaches a paying customer",
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
            {
                "nodes": [
                    {
                        "use_case": "Order a single item",
                        "description": "One item ordered and confirmed",
                        "count": 1,
                        "angles": ["plain single-item success"],
                    }
                ]
            },
            ROWS,
            ROWS,  # plan review returns the same surviving plans
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


def test_validator_survives_malformed_definition_shapes():
    """Model JSON is arbitrary; the validator reports problems, never raises."""
    contract = AgentContract.model_validate(CONTRACT)
    bad = json.loads(json.dumps(SCENARIO))
    bad["sub_goals"].append(
        {
            "name": "no_extra_items",
            "milestone": "nothing else ordered",
            "checkpoint": {
                "kind": "absent",
                "detail": "no other tool fires",
                "deterministic": True,
                "definition": {"no_tool_call": ["add_item", "list_order"]},
            },
        }
    )
    problems = validate_scenario(bad, contract)
    assert any("absent-tool-not-a-single-name" in p for p in problems)


def test_oracle_rejects_internally_contradictory_scenario():
    from fi.alk.generation.oracle import oracle_problems

    assert oracle_problems(SCENARIO) == []  # the fixture predicts a run it passes

    broken = json.loads(json.dumps(SCENARIO))
    # State checkpoint asserts a state the seed plus declared updates never produce.
    broken["environment"]["mock_responses"]["add_item"]["state_updates"] = {
        "order": {"items": ["latte_M"], "confirmed": False}
    }
    problems = oracle_problems(broken)
    assert any("order_confirmed" in p for p in problems)

    contradiction = json.loads(json.dumps(SCENARIO))
    # An absent checkpoint forbids the very call another checkpoint requires.
    contradiction["sub_goals"].append(
        {
            "name": "no_add_item_call",
            "milestone": "contradicts the required call",
            "checkpoint": {
                "kind": "absent",
                "detail": "add_item never fires",
                "deterministic": True,
                "definition": {"no_tool_call": "add_item"},
            },
        }
    )
    problems = oracle_problems(contradiction)
    assert any("no_add_item_call" in p for p in problems)


def test_contract_normalizes_benign_shape_variance():
    payload = json.loads(json.dumps(CONTRACT))
    payload["grading_notes"] = ["line one", "line two"]
    payload["hard_constraints"] = "a single rule as a bare string"
    payload["one_liner"] = ["joined", "sentence"]
    contract = AgentContract.model_validate(payload)
    assert contract.grading_notes == "line one\nline two"
    assert contract.hard_constraints == ["a single rule as a bare string"]
    assert "joined" in contract.one_liner


def test_validator_rejects_disallowed_arg_value():
    contract = AgentContract.model_validate(CONTRACT)
    bad = json.loads(json.dumps(SCENARIO))
    bad["sub_goals"][0]["checkpoint"]["definition"]["args_equal"]["size"] = "XL"
    problems = validate_scenario(bad, contract)
    assert any("arg-value-not-allowed:size=XL" in p for p in problems)


def test_validator_rejects_pinned_value_absent_from_contract():
    contract = AgentContract.model_validate(CONTRACT)
    bad = json.loads(json.dumps(SCENARIO))
    bad["sub_goals"][0]["checkpoint"]["definition"] = {
        "tool": "list_order",
        "args_equal": {"order_ref": "def2x"},
    }
    problems = validate_scenario(bad, contract)
    assert any("pinned-value-not-in-contract" in p for p in problems)


def test_tool_call_args_min_count_requires_multiple_calls():
    from fi.alk.generation.checks import evaluate_checkpoint

    definition = {
        "tool": "add_item",
        "args_equal": {"item_id": "latte"},
        "min_count": 2,
    }
    one = [{"name": "add_item", "arguments": {"item_id": "latte"}}]
    passed, reason = evaluate_checkpoint("tool_call_args", definition, tool_calls=one)
    assert passed is False and "1 of 2" in reason
    passed, _ = evaluate_checkpoint("tool_call_args", definition, tool_calls=one * 2)
    assert passed is True
    # call_nth (the shape models produce unprompted) behaves as min_count
    legacy = {"tool": "add_item", "args_equal": {"item_id": "latte"}, "call_nth": 2}
    passed, _ = evaluate_checkpoint("tool_call_args", legacy, tool_calls=one)
    assert passed is False


def test_two_subgoal_refusal_scenario_is_valid():
    contract = AgentContract.model_validate(CONTRACT)
    lean = json.loads(json.dumps(SCENARIO))
    lean["sub_goals"] = [
        {
            "name": "no_item_ordered",
            "milestone": "nothing is added",
            "checkpoint": {
                "kind": "absent",
                "detail": "no add_item call",
                "deterministic": True,
                "definition": {"no_tool_call": "add_item"},
            },
        },
        {
            "name": "unavailability_declared",
            "milestone": "the caller is told",
            "checkpoint": {
                "kind": "judge",
                "detail": "agent states the item is unavailable",
                "deterministic": False,
                "definition": {
                    "rubric": "Did the agent state the requested item is unavailable?"
                },
            },
        },
    ]
    assert validate_scenario(lean, contract) == []


def test_exactly_n_scenarios_never_more(agent_repo, tmp_path):
    """Two viable candidates, n=1: planning renormalizes to the target, so exactly one
    scenario is planned, materialized, and delivered — never more."""
    second_row = dict(
        ROWS["rows"][0], id="latte-large", situation="The caller wants one large latte"
    )
    two_rows = {"rows": [ROWS["rows"][0], second_row]}
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
            {
                "nodes": [
                    {
                        "use_case": "Order a single item",
                        "description": "d",
                        "count": 1,
                        "angles": ["single item"],
                    }
                ]
            },
            two_rows,
            two_rows,  # plan review echoes both survivors
            SCENARIO,
            VERDICT,
            {"gaps": [], "near_duplicates": []},
        ]
    )
    config = GenerationConfig(n=1, out_dir=str(tmp_path / "out"))
    config.max_workers = 1
    result = generate(RepoFolderSource(path=agent_repo), llm, config)
    assert len(result.records) == 1
    assert (
        not llm.responses
    )  # every queued response consumed, none needed beyond the plan


def test_trace_mining_produces_provenance_pinned_plans(tmp_path):
    from fi.alk.generation.traces import load_traces, mine_traces

    trace = tmp_path / "call_001.txt"
    trace.write_text(
        "USER: one medium latte please\nAGENT: sure, that is 4.5\nUSER: perfect"
    )
    traces = load_traces(str(tmp_path))
    assert traces and traces[0]["ref"] == "call_001.txt"

    contract = AgentContract.model_validate(CONTRACT)
    llm = FakeLLMClient(
        responses=[
            {
                "rows": [
                    {
                        "id": "recreate-call-001",
                        "trace_ref": "call_001.txt",
                        "use_case": "Order a single item",
                        "situation": "A caller orders one medium latte and confirms the price",
                        "target_failure": "The agent misprices or mis-sizes the real order",
                        "why_it_matters": "This exact interaction happened with a real customer",
                        "unique_end_state": "One medium latte ordered at 4.5",
                        "goal": "Recreate the real call correctly",
                    }
                ]
            }
        ]
    )
    plans = mine_traces(contract, traces, llm)
    assert len(plans) == 1
    assert plans[0]["provenance"] == {
        "kind": "production_trace",
        "trace_ref": "call_001.txt",
    }


def test_operator_request_scenarios_come_first_with_provenance(agent_repo, tmp_path):
    requested_scenario = json.loads(json.dumps(SCENARIO))
    requested_scenario["provenance"] = {"kind": "operator_request"}
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
            {
                "tool_calls": [
                    {
                        "id": "c2",
                        "name": "submit_contract",
                        "arguments": {"contract": CONTRACT},
                    }
                ]
            },
            CATALOG,
            ROWS,  # the dedicated request-planning reply
            ROWS,  # blueprint review echoes the survivor
            requested_scenario,
            VERDICT,
            {"gaps": [], "near_duplicates": []},
        ]
    )
    config = GenerationConfig(
        n=1,
        guidance="test single-item ordering accuracy",
        out_dir=str(tmp_path / "out"),
    )
    config.max_workers = 1
    result = generate(RepoFolderSource(path=agent_repo), llm, config)
    assert len(result.records) == 1
    assert result.records[0]["provenance"]["kind"] == "operator_request"
    assert not llm.responses  # request filled N; coverage planning never ran


# ---------------------------------------------------------------------------
# Environment selection
# ---------------------------------------------------------------------------


def test_unsupported_environment_is_refused_by_name():
    from fi.alk.generation import environments

    for key in ("browser", "computer_use", "code", "telepathy"):
        with pytest.raises(NotImplementedError) as excinfo:
            environments.resolve(key)
        message = str(excinfo.value)
        assert "voice" in message and "chat" in message


def test_supported_environments_resolve_case_insensitively():
    from fi.alk.generation import environments

    assert environments.resolve("VOICE").key == "voice"
    assert environments.resolve(" chat ").alk_plugin == "chat"


def test_modality_disagreement_warns_without_overriding_the_choice():
    from fi.alk.generation import environments

    assert not environments.modality_mismatch(environments.CHAT, "data_sql")
    warning = environments.modality_mismatch(environments.VOICE, "browser")
    assert "browser" in warning and "voice" in warning


# ---------------------------------------------------------------------------
# Trace exploration and amplification
# ---------------------------------------------------------------------------


def _odd_trace_folder(root, shallow: int = 240):
    """A folder nobody documented: nested session dirs, a flat archive, mixed formats."""
    for index in range(shallow):
        day = f"2026-08-{(index % 28) + 1:02d}"
        session = root / "sessions" / day / f"sess_{index:04d}"
        session.mkdir(parents=True, exist_ok=True)
        failed = index % 40 == 0
        (session / "transcript.json").write_text(
            json.dumps(
                {
                    "id": f"sess_{index:04d}",
                    "status": "error" if failed else "completed",
                    "turns": [
                        {"role": "user", "text": "one large coffee"},
                        {
                            "role": "agent",
                            "text": "sorry, I did not catch that"
                            if failed
                            else "one large coffee, that is 3.5",
                        },
                    ],
                }
            )
        )
    archive = root / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    (archive / "old_call.log").write_text(
        "USER: I asked for no onions\nAGENT: added onions\nUSER: that is wrong, again, no onions"
    )
    return root


def test_trace_explorer_reads_an_unknown_layout_and_puts_failures_first(tmp_path):
    from fi.alk.generation.traces import explore_traces

    root = _odd_trace_folder(tmp_path / "traces")
    llm = FakeLLMClient(
        responses=[
            {
                "tool_calls": [
                    {"id": "t1", "name": "list_dir", "arguments": {"path": ""}}
                ]
            },
            {
                "tool_calls": [
                    {
                        "id": "t2",
                        "name": "submit_selection",
                        "arguments": {
                            "format_notes": "one json transcript per session folder",
                            "total_seen": 241,
                            "selected": [
                                {
                                    "path": "sessions/2026-08-02/sess_0001/transcript.json",
                                    "outcome": "succeeded",
                                    "why": "the common happy path",
                                },
                                {
                                    "path": "archive/old_call.log",
                                    "outcome": "failed",
                                    "why": "the agent ignored a stated exclusion",
                                },
                            ],
                        },
                    }
                ]
            },
        ]
    )
    selected = explore_traces(str(root), llm)
    assert len(selected) == 2
    # Failing interactions lead, whatever order the model submitted them in.
    assert selected[0]["outcome"] == "failed"
    assert selected[0]["ref"] == "archive/old_call.log"
    assert "no onions" in selected[0]["text"]


def test_trace_explorer_refuses_paths_outside_the_folder(tmp_path):
    from fi.alk.generation.traces import explore_traces

    root = _odd_trace_folder(tmp_path / "traces", shallow=2)
    (tmp_path / "secret.txt").write_text("not a trace")
    llm = FakeLLMClient(
        responses=[
            {
                "tool_calls": [
                    {
                        "id": "t1",
                        "name": "submit_selection",
                        "arguments": {
                            "format_notes": "n/a",
                            "total_seen": 3,
                            "selected": [
                                {
                                    "path": "../secret.txt",
                                    "outcome": "failed",
                                    "why": "escaping the sandbox",
                                }
                            ],
                        },
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "id": "t2",
                        "name": "submit_selection",
                        "arguments": {
                            "format_notes": "flat archive",
                            "total_seen": 3,
                            "selected": [
                                {
                                    "path": "archive/old_call.log",
                                    "outcome": "failed",
                                    "why": "stated exclusion ignored",
                                }
                            ],
                        },
                    }
                ]
            },
        ]
    )
    selected = explore_traces(str(root), llm)
    assert len(selected) == 1
    assert selected[0]["ref"] == "archive/old_call.log"


def test_failing_traces_are_marked_for_amplification_and_fenced(tmp_path):
    from fi.alk.generation.traces import amplify_plans, mine_traces

    contract = AgentContract.model_validate(CONTRACT)
    traces = [
        {
            "ref": "archive/old_call.log",
            "text": "USER: no onions\nAGENT: added onions",
            "outcome": "failed",
            "why": "stated exclusion ignored",
        },
        {
            "ref": "sessions/ok.json",
            "text": "USER: one coffee\nAGENT: one coffee, 3.5",
            "outcome": "succeeded",
            "why": "happy path",
        },
    ]
    llm = FakeLLMClient(
        responses=[
            {
                "rows": [
                    {
                        "id": "recreate-onion",
                        "trace_ref": "archive/old_call.log",
                        "use_case": "Order with an exclusion",
                        "situation": "A caller states an exclusion the agent must honour",
                        "target_failure": "The agent drops the stated exclusion",
                        "why_it_matters": "A real customer received the wrong food",
                        "unique_end_state": "The item is ordered without the excluded ingredient",
                        "goal": "Order the item as stated",
                    },
                    {
                        "id": "recreate-coffee",
                        "trace_ref": "sessions/ok.json",
                        "use_case": "Order a single item",
                        "situation": "A caller orders one coffee",
                        "target_failure": "The agent misprices the order",
                        "why_it_matters": "This interaction happens constantly",
                        "unique_end_state": "One coffee ordered at 3.5",
                        "goal": "Order one coffee",
                    },
                ]
            },
            {
                "rows": [
                    {
                        "id": "exclusion-different-item",
                        "use_case": "Order with an exclusion",
                        "situation": "The same exclusion is stated against a different item",
                        "target_failure": "The agent drops the stated exclusion",
                        "why_it_matters": "The same weakness reaches every item on the menu",
                        "unique_end_state": "The other item is ordered without the ingredient",
                        "goal": "Order the other item as stated",
                    }
                ]
            },
        ]
    )
    plans = mine_traces(contract, traces, llm)
    assert [p["amplify"] for p in plans] == [True, False]

    neighbours = amplify_plans(contract, plans, llm, per_plan=1)
    assert len(neighbours) == 1
    assert neighbours[0]["provenance"] == {
        "kind": "trace_amplified",
        "trace_ref": "archive/old_call.log",
        "amplifies": "recreate-onion",
    }


def test_report_names_the_environment_and_the_open_questions():
    from fi.alk.generation import environments
    from fi.alk.generation.emit import render_report

    contract = AgentContract.model_validate(CONTRACT)
    record = json.loads(json.dumps(SCENARIO))
    record["provenance"] = {"kind": "production_trace", "trace_ref": "call_001.txt"}
    report = render_report(
        contract,
        [],
        [record],
        [],
        {"usd": 0.1},
        open_questions=["Assumed refunds are out of scope for this suite"],
        environment=environments.VOICE,
    )
    assert "environment: **voice**" in report
    assert "Assumed refunds are out of scope" in report
    assert "production_trace" in report
    # Voice cannot grade world state today, and the report has to say so rather than imply it can.
    assert "cannot grade yet" in report and "state" in report


def test_plan_fields_survive_materialization_even_when_the_model_omits_them():
    """The plan owns why_it_matters and target_failure; a record must never lose them.

    These fields are the scenario's stated reason to exist, and the validators require them. When
    materialization depended on the model echoing them back, an unrelated prompt change silently
    turned every scenario into a rejection.
    """
    from fi.alk.generation.pipeline import GenerationConfig, materialize_row

    contract = AgentContract.model_validate(CONTRACT)
    plan = {
        "id": "carried-plan",
        "use_case": "Order a single item",
        "situation": "A caller orders one item and states a size",
        "target_failure": "The agent drops the stated size",
        "why_it_matters": "The customer is handed the wrong drink",
        "unique_end_state": "One large coffee ordered",
        "goal": "Order one large coffee",
        "provenance": {"kind": "production_trace", "trace_ref": "call_001.txt"},
    }
    stripped = json.loads(json.dumps(SCENARIO))
    for field in ("why_it_matters", "target_failure", "provenance"):
        stripped.pop(field, None)

    llm = FakeLLMClient(responses=[stripped, VERDICT])
    record, reason = materialize_row(
        contract, plan, [], llm, GenerationConfig(critic_enabled=True)
    )
    assert record is not None, reason
    assert record["why_it_matters"] == "The customer is handed the wrong drink"
    assert record["target_failure"] == "The agent drops the stated size"
    assert record["provenance"]["kind"] == "production_trace"


def test_composed_argument_values_are_not_required_to_exist_in_the_contract():
    """A query the agent writes is authored per scenario; a contract cannot list it.

    The grounding rule exists to catch transposed identifiers. Applied to composed text it made
    every query-writing agent ungeneratable: each scenario was rejected for pinning the very SQL
    the test exists to check.
    """
    from fi.alk.generation.validators import validate_scenario

    contract = AgentContract.model_validate(
        {
            **CONTRACT,
            "conversational": False,
            "tools": [
                {
                    "name": "sql_db_query",
                    "args": ["query"],
                    "arg_values": {},
                    "description": "Run a SQL query",
                },
                {
                    "name": "sql_db_list_tables",
                    "args": [],
                    "arg_values": {},
                    "description": "List tables",
                },
            ],
        }
    )
    record = json.loads(json.dumps(SCENARIO))
    record["facts"] = []
    record["sub_goals"] = [
        {
            "name": "listed_the_tables",
            "milestone": "The agent inspects the schema",
            "checkpoint": {
                "kind": "tool_call_args",
                "deterministic": True,
                "detail": "The agent listed the tables",
                # A tool with no parameters is asserted by the call alone.
                "definition": {"tool": "sql_db_list_tables"},
            },
        },
        {
            "name": "ran_the_expected_query",
            "milestone": "The agent runs the query",
            "checkpoint": {
                "kind": "tool_call_args",
                "deterministic": True,
                "detail": "The agent ran the expected query",
                "definition": {
                    "tool": "sql_db_query",
                    "args_equal": {
                        "query": "SELECT BillingCountry, SUM(Total) FROM Invoice GROUP BY BillingCountry"
                    },
                },
            },
        },
    ]
    problems = validate_scenario(record, contract)
    assert not [p for p in problems if "pinned-value-not-in-contract" in p], problems
    assert not [p for p in problems if "tool_call_args-without-args" in p], problems


def test_identifier_shaped_values_are_still_grounded():
    """The original guard must survive: a transposed id is still caught."""
    from fi.alk.generation.validators import validate_scenario

    contract = AgentContract.model_validate(CONTRACT)
    record = json.loads(json.dumps(SCENARIO))
    for sub_goal in record["sub_goals"]:
        definition = sub_goal["checkpoint"].get("definition") or {}
        if definition.get("args_equal"):
            key = sorted(definition["args_equal"])[0]
            definition["args_equal"][key] = "combo_not_a_real_id"
            break
    problems = validate_scenario(record, contract)
    assert any("combo_not_a_real_id" in p for p in problems), problems


def test_a_repeated_identical_failure_stops_the_repair_loop():
    """Rewrites that return the same complaint never recover, and the chain is serial.

    Four attempts on a scenario that fails identically each time is the single largest cost in a
    run: eight model calls spent to reject one scenario, in a chain that cannot be parallelised.
    """
    from fi.alk.generation.pipeline import GenerationConfig, materialize_row

    contract = AgentContract.model_validate(CONTRACT)
    broken = json.loads(json.dumps(SCENARIO))
    broken["sub_goals"][0]["checkpoint"]["definition"] = {
        "tool": "order_combo_meal",
        "args_equal": {"meal_id": "combo_not_a_real_id"},
    }
    # Enough responses queued for the full four attempts; the loop must not consume them all.
    llm = FakeLLMClient(responses=[json.loads(json.dumps(broken)) for _ in range(4)])
    plan = {"id": "p", "target_failure": "x", "why_it_matters": "y"}
    record, reason = materialize_row(
        contract, plan, [], llm, GenerationConfig(critic_enabled=True)
    )
    assert record is None
    assert "same problem twice" in reason
    assert llm.usage.calls == 2, f"stopped after {llm.usage.calls} calls, expected 2"


def test_a_quantity_checkpoint_passes_the_run_it_predicts():
    """min_count asserts several identical calls, so the predicted run must contain several.

    Predicting one call made every quantity scenario contradict itself: three of one run's
    fourteen rejections were correct scenarios failing an oracle that under-predicted.
    """
    from fi.alk.generation.oracle import oracle_problems, predicted_evidence

    record = json.loads(json.dumps(SCENARIO))
    record["sub_goals"] = [
        {
            "name": "two_combos_added",
            "milestone": "Two identical combos are ordered",
            "checkpoint": {
                "kind": "tool_call_args",
                "deterministic": True,
                "detail": "order_combo_meal called twice",
                "definition": {
                    "tool": "order_combo_meal",
                    "args_equal": {"meal_id": "combo_big_mac"},
                    "min_count": 2,
                },
            },
        }
    ]
    assert len(predicted_evidence(record)["tool_calls"]) == 2
    assert not oracle_problems(record)


def test_a_handle_the_scenario_mocks_into_existence_is_groundable():
    """An order handle cannot be in the contract, but the mock that creates it makes it checkable."""
    from fi.alk.generation.validators import validate_scenario

    contract = AgentContract.model_validate(CONTRACT)
    tool = contract.tools[0].name
    record = json.loads(json.dumps(SCENARIO))
    record["environment"] = {
        "seed": {},
        "mock_responses": {
            tool: {
                "content": "added",
                "state_updates": {"order": [{"order_id": "combo_handle_1"}]},
            }
        },
    }
    record["sub_goals"] = record["sub_goals"][:1] + [
        {
            "name": "handle_used",
            "milestone": "The agent acts on the item it already added",
            "checkpoint": {
                "kind": "tool_call_args",
                "deterministic": True,
                "detail": "acts on the handle its own earlier call created",
                "definition": {
                    "tool": tool,
                    "args_equal": {"order_id": "combo_handle_1"},
                },
            },
        }
    ]
    problems = validate_scenario(record, contract)
    assert not [p for p in problems if "combo_handle_1" in p], problems


def test_a_handle_no_mock_creates_is_still_refused():
    """The relaxation is earned by declaring the source, not by naming something plausible."""
    from fi.alk.generation.validators import validate_scenario

    contract = AgentContract.model_validate(CONTRACT)
    tool = contract.tools[0].name
    record = json.loads(json.dumps(SCENARIO))
    record["environment"] = {"seed": {}, "mock_responses": {}}
    record["sub_goals"] = record["sub_goals"][:1] + [
        {
            "name": "handle_used",
            "milestone": "The agent acts on an item",
            "checkpoint": {
                "kind": "tool_call_args",
                "deterministic": True,
                "detail": "acts on an undeclared handle",
                "definition": {
                    "tool": tool,
                    "args_equal": {"order_id": "invented_handle_9"},
                },
            },
        }
    ]
    problems = validate_scenario(record, contract)
    assert any("invented_handle_9" in p for p in problems), problems


def test_traces_are_loaded_from_nested_folders(tmp_path):
    """Recordings are usually filed under dated subdirectories, not at the top level.

    A flat listing returned almost nothing, and because this is the fallback when exploration
    does not submit, an entire run silently lost its grounding without erroring.
    """
    from fi.alk.generation.traces import load_traces

    nested = tmp_path / "sessions" / "2026-08-17" / "sess_0097"
    nested.mkdir(parents=True)
    (nested / "transcript.json").write_text(
        '{"turns": [{"role": "user", "text": "hi"}]}'
    )
    (tmp_path / "README.txt").write_text("call exports")

    traces = load_traces(str(tmp_path))
    refs = {t["ref"] for t in traces}
    assert "sessions/2026-08-17/sess_0097/transcript.json" in refs, refs


def test_a_plan_citing_an_unsupplied_recording_is_dropped():
    """Provenance must be verifiable, or 'grounded in production' means nothing.

    A run whose trace explorer failed produced three scenarios citing invented recordings named
    'hypothetical_trace_1'. They were reported as recreating real calls.
    """
    from fi.alk.generation.traces import mine_traces

    contract = AgentContract.model_validate(CONTRACT)
    traces = [
        {
            "ref": "archive/real_call.log",
            "text": "USER: one coffee",
            "outcome": "failed",
        }
    ]
    llm = FakeLLMClient(
        responses=[
            {
                "rows": [
                    {
                        "id": "real-one",
                        "trace_ref": "archive/real_call.log",
                        "use_case": "Order a single item",
                        "situation": "A caller orders one coffee",
                        "target_failure": "The agent misprices the order",
                        "why_it_matters": "It happened to a real customer",
                        "unique_end_state": "One coffee ordered",
                        "goal": "Order one coffee",
                    },
                    {
                        "id": "invented-one",
                        "trace_ref": "hypothetical_trace_2_change_of_mind",
                        "use_case": "Order a single item",
                        "situation": "A caller changes their mind",
                        "target_failure": "The agent keeps the original item",
                        "why_it_matters": "Invented provenance",
                        "unique_end_state": "The new item is ordered",
                        "goal": "Swap the item",
                    },
                ]
            }
        ]
    )
    plans = mine_traces(contract, traces, llm)
    assert [p["id"] for p in plans] == ["real-one"]
    assert plans[0]["provenance"]["trace_ref"] == "archive/real_call.log"
