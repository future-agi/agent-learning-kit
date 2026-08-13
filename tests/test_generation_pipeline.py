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
