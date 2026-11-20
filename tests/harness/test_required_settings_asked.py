"""The reading is asked outright for the settings the agent can't run without. A contract that
gives no answer for the process the agent runs as is sent back once; a second one with no answer
is saved as it is, and the settings are then not known. The reading never fails for this."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from fi.alk.harness.tools import accept_contract, contract_tools

SKILL = (
    Path(__file__).resolve().parents[2]
    / "src/fi/alk/harness/skills/understand-agent/SKILL.md"
)
ASKED = "runtime.required_environment: no answer for the process the agent runs as."


def payload(**runtime) -> dict:
    return {
        "agent": "support",
        "modality": "chat",
        "real_use_cases": ["a return"],
        "hard_constraints": ["Never guess an order's status."],
        "tools": [{"name": "look_up_order", "args": ["order_id"]}],
        "runtime": {"language": "python", **runtime},
    }


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "src"
    root.mkdir()
    (root / "agent.py").write_text(
        "def look_up_order(order_id):\n    return order_id\n", encoding="utf-8"
    )
    return root


def submitter(destination: Path, root: Path):
    server = contract_tools(destination, source_root=root)
    tool = next(one for one in server.tools if one.name == "submit_contract")
    return lambda arguments: asyncio.run(tool.handler(dict(arguments)))


def text_of(result: dict) -> str:
    return result["content"][0]["text"]


def saved_environment(destination: Path) -> dict:
    contract = json.loads((destination / "contract.json").read_text(encoding="utf-8"))
    return contract["runtime"]["required_environment"]


@pytest.mark.parametrize(
    "runtime",
    [pytest.param({}, id="left-out"), pytest.param({"required_environment": {}}, id="empty")],
)
def test_no_answer_is_sent_back_once_then_saved_as_not_known(tmp_path, source, runtime):
    destination = tmp_path / "out"
    submit = submitter(destination, source)

    first = submit(payload(**runtime))

    assert first.get("is_error"), first
    assert ASKED in text_of(first)
    assert 'under "agent"' in text_of(first)
    assert '{"agent": []}' in text_of(first)
    assert not (destination / "contract.json").exists()

    second = submit(payload(**runtime))

    assert not second.get("is_error"), second
    assert saved_environment(destination) == {}


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param({"agent": ["OPENAI_API_KEY", "OPENAI_BASE_URL"]}, id="names"),
        pytest.param({"agent": []}, id="none-in-so-many-words"),
    ],
)
def test_an_answer_is_saved_the_first_time(tmp_path, source, answer):
    destination = tmp_path / "out"

    result = submitter(destination, source)(payload(required_environment=answer))

    assert not result.get("is_error"), result
    assert saved_environment(destination) == answer


def test_an_answer_for_another_process_is_no_answer(tmp_path, source):
    result = submitter(tmp_path / "out", source)(
        payload(required_environment={"worker": ["OPENAI_API_KEY"]})
    )

    assert ASKED in text_of(result)


def test_a_compose_service_is_asked_for_by_its_name(tmp_path, source):
    (source / "compose.yaml").write_text(
        "services:\n  api:\n    build: .\n", encoding="utf-8"
    )
    runtime = {"execution": "compose", "compose_file": "compose.yaml", "service": "api"}
    destination = tmp_path / "out"
    submit = submitter(destination, source)

    asked = submit(payload(**runtime, required_environment={"agent": ["OPENAI_API_KEY"]}))

    assert ASKED in text_of(asked)
    assert 'under "api"' in text_of(asked)
    # Asked once, so it isn't asked again; an answer for the service saves either way.
    second = submit(payload(**runtime, required_environment={"api": ["OPENAI_API_KEY"]}))
    assert ASKED not in text_of(second)


def test_asked_with_another_refusal_it_costs_no_call_of_its_own(tmp_path, source):
    # A contract refused for something else carries this question in the same answer, so the
    # next contract, still with no answer, is saved.
    destination = tmp_path / "out"
    submit = submitter(destination, source)
    refused = payload()
    refused["tools"] = [{"name": "look_up_order", "args": ["order_id"], "mode": "nope"}]

    first = submit(refused)

    assert first.get("is_error"), first
    assert ASKED in text_of(first)

    second = submit(payload())

    assert not second.get("is_error"), second
    assert saved_environment(destination) == {}


def test_asked_with_a_first_call_nudge_it_costs_no_call_of_its_own(tmp_path, source):
    destination = tmp_path / "out"
    submit = submitter(destination, source)
    thin = payload()
    del thin["hard_constraints"]

    first = submit(thin)

    assert first.get("is_error"), first
    assert "no hard_constraints" in text_of(first)
    assert ASKED in text_of(first)

    second = submit(thin)

    assert not second.get("is_error"), second
    assert saved_environment(destination) == {}


def test_a_job_that_names_the_folder_is_asked_with_the_first_nudge(tmp_path, source):
    # The save gives such a job a runtime though none was sent, so the question goes too.
    (source / "agents" / "a").mkdir(parents=True)
    (source / "agents" / "a" / "agent.py").write_text("x = 1\n", encoding="utf-8")
    destination = tmp_path / "out"
    server = contract_tools(destination, source_root=source, subfolder="agents/a")
    tool = next(one for one in server.tools if one.name == "submit_contract")
    thin = payload()
    del thin["hard_constraints"], thin["runtime"]

    first = asyncio.run(tool.handler(dict(thin)))

    assert ASKED in text_of(first)
    assert not asyncio.run(tool.handler(dict(thin))).get("is_error")
    assert saved_environment(destination) == {}


def test_a_contract_with_no_runtime_is_not_asked(tmp_path, source):
    contract = payload()
    del contract["runtime"]

    result = submitter(tmp_path / "out", source)(contract)

    assert not result.get("is_error"), result


def test_the_kits_own_gate_does_not_ask(tmp_path, source):
    # Only the reading is asked: the gate the kit calls itself has no reading to answer it.
    result = accept_contract(payload(), tmp_path / "out", source_root=source)

    assert not result.get("is_error"), result


def test_the_field_and_the_reading_ask_for_the_answer_outright(tmp_path):
    server = contract_tools(tmp_path / "out")
    submit = next(one for one in server.tools if one.name == "submit_contract")
    runtime = submit.input_schema["properties"]["runtime"]["properties"]
    description = runtime["required_environment"]["description"]

    assert description.startswith("Always answer:")
    assert '{"agent": []}' in description
    skill = SKILL.read_text(encoding="utf-8")
    assert "**The settings it can't run without.** Always say" in skill
