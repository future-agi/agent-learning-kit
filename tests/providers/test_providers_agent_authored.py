from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from fi.alk.harness.contract import AgentContract
from fi.alk.providers.agent import AgentBuildError, build_authored_agent
from fi.alk.providers.setup_hash import compute_setup_hash

AUTHORED = Path(__file__).parent / "fixtures" / "authored"


def _contract(**fields: object) -> dict:
    return AgentContract.model_validate({"agent": "test-agent", **fields}).model_dump(
        mode="json"
    )


def _only_tool(
    *, spec: dict | None = None, entrypoints: list[dict] | None = None
) -> dict:
    contract = _contract(
        tools=[{"name": "lookup", **(spec or {})}], tool_entrypoints=entrypoints or []
    )
    return build_authored_agent(contract)["tools"][0]


@pytest.mark.parametrize(
    "case",
    [
        "chat_source",
        "voice_source",
        "no_prompt",
        "no_tools",
        "service_tool",
        "traceai_instrumented",
        "opentelemetry_instrumented",
    ],
)
def test_recorded_contracts_give_expected_agent(case: str) -> None:
    recorded = json.loads((AUTHORED / f"{case}.json").read_text())

    agent = build_authored_agent(recorded["contract"])

    assert agent == recorded["agent"]
    assert agent["kind"] == "source"
    assert agent["provider_ref"] is None
    assert [unit["shape"] for unit in agent["behaviour"]] == ["external"]


@pytest.mark.parametrize(
    ("entrypoints", "expected_kind"),
    [
        ([{"tool": "lookup", "mode": "service"}], "http"),
        ([{"tool": "lookup", "mode": "import"}], "builtin"),
        ([{"tool": "lookup", "mode": "construct"}], "builtin"),
        ([{"tool": "lookup", "mode": "unreachable"}], "unknown"),
        ([{"tool": "lookup", "mode": "grpc"}], "unknown"),
        ([{"tool": "other", "mode": "service"}], "unknown"),
        ([], "unknown"),
        (
            [
                {"tool": "lookup", "mode": "service"},
                {"tool": "lookup", "mode": "import"},
            ],
            "http",
        ),
    ],
    ids=[
        "service",
        "import",
        "construct",
        "unreachable",
        "unknown_word",
        "other_tool",
        "no_entry",
        "first_entry_wins",
    ],
)
def test_authored_tool_kind(entrypoints: list[dict], expected_kind: str) -> None:
    tool = _only_tool(
        entrypoints=[{**entry, "endpoint": "/lookup"} for entry in entrypoints]
    )

    assert tool["kind"] == expected_kind
    assert tool["endpoint"] is None


def test_authored_tool_parameters() -> None:
    assert _only_tool(spec={"args": []})["parameters"] is None
    assert _only_tool(
        spec={
            "args": ["order_id", "region"],
            "arg_values": {"region": ["eu", "us"], "order_id": "A-1"},
        }
    )["parameters"] == {
        "type": "object",
        "properties": {"order_id": {}, "region": {"enum": ["eu", "us"]}},
        "required": ["order_id", "region"],
    }


@pytest.mark.parametrize("modality", ["sms", "", ["voice", "chat"]])
def test_authored_rejects_modality(modality: object) -> None:
    with pytest.raises(AgentBuildError) as raised:
        build_authored_agent(_contract(modality=modality))

    assert str(raised.value) == "agent_invalid"
    assert raised.value.detail == "modality"


def test_authored_is_pure_and_hash_is_stable_for_one_document() -> None:
    contract = json.loads((AUTHORED / "voice_source.json").read_text())["contract"]
    before = copy.deepcopy(contract)

    first = build_authored_agent(contract)
    second = build_authored_agent(contract)

    assert contract == before
    assert first == second
    assert first["setup_hash"] == compute_setup_hash(first)


def test_authored_reads_the_redacted_document() -> None:
    agent = build_authored_agent(
        _contract(
            tools=[
                {
                    "name": "login",
                    "args": ["user", "password"],
                    "arg_values": {"password": ["hunter2", "s3cret"]},
                }
            ],
            dependencies=[{"name": "db", "reached": {"dsn_env": "ORDERS_DSN"}}],
            data_schema={"accounts": {"api_key": "sk-live-1", "columns": ["id"]}},
            base_environment={"accounts": [{"id": 1}]},
        )
    )
    stored = agent["extension"]["contract"]

    assert agent["tools"][0]["parameters"]["properties"]["password"] == {}
    assert stored["tools"][0]["arg_values"]["password"] == "[redacted]"
    assert stored["dependencies"][0]["reached"]["dsn_env"] == "ORDERS_DSN"
    assert stored["data_schema"] == {
        "accounts": {"api_key": "[redacted]", "columns": ["id"]}
    }
    assert "base_environment" not in stored
    assert "hunter2" not in json.dumps(agent)


def test_authored_keeps_the_host_of_a_url_with_a_placeholder_port() -> None:
    agent = build_authored_agent(
        _contract(
            data_schema={
                "braces": "http://{HOST}:{PORT}/v1?token=abc",
                "shell": "http://localhost:${PORT}/v1#frag",
                "numeric": "http://localhost:8080/v1",
            }
        )
    )
    stored = agent["extension"]["contract"]["data_schema"]

    assert stored["braces"] == "http://{host}/v1"
    assert stored["shell"] == "http://localhost/v1"
    assert stored["numeric"] == "http://localhost:8080/v1"


def test_framework_written_beside_contract() -> None:
    contract = _contract()
    framework = {"name": "other", "version": "1.2", "label": "dspy"}

    agent = build_authored_agent(contract, framework=framework)

    assert set(agent["extension"]) == {"contract", "framework"}
    assert agent["extension"]["framework"] == framework
    assert agent["setup_hash"] == compute_setup_hash(agent)
    assert agent["setup_hash"] != build_authored_agent(contract)["setup_hash"]


def test_no_framework_leaves_extension_and_hash() -> None:
    recorded = json.loads((AUTHORED / "chat_source.json").read_text())

    agent = build_authored_agent(recorded["contract"], framework=None)

    assert agent == recorded["agent"]
    assert set(agent["extension"]) == {"contract"}


_ABSENT = object()


def _chat_contract() -> dict:
    # A raw dict, not through AgentContract, so a model of any shape reaches
    # build_authored_agent unchanged.
    return copy.deepcopy(
        json.loads((AUTHORED / "chat_source.json").read_text())["contract"]
    )


@pytest.mark.parametrize(
    "model",
    [_ABSENT, None, "gpt-4o", {"name": "  "}],
    ids=["absent", "none", "raw-string", "blank-name"],
)
def test_no_model_leaves_the_document_and_hash(model: object) -> None:
    recorded = json.loads((AUTHORED / "chat_source.json").read_text())
    contract = _chat_contract()
    expected = copy.deepcopy(recorded["agent"])
    if model is not _ABSENT:
        contract["model"] = model
        # The stored contract echoes the value whatever its shape, so only that
        # echo and the hash over it may differ from the recorded document.
        expected["extension"]["contract"]["model"] = model
        expected["setup_hash"] = compute_setup_hash(expected)

    agent = build_authored_agent(contract)

    assert agent["model"] is None
    assert agent == expected


def test_model_goes_to_the_document() -> None:
    def built(model: dict) -> dict:
        return build_authored_agent({**_chat_contract(), "model": model})

    without = build_authored_agent(_chat_contract())
    saved = {"provider": "  openai ", "name": " gpt-4o ", "path": "agent.py", "line": 9}
    agent = built(saved)

    assert agent["model"] == {
        "provider": "openai",
        "name": "gpt-4o",
        "temperature": None,
        "max_tokens": None,
    }
    assert agent["extension"]["contract"]["model"] == saved
    assert set(agent["extension"]) == {"contract"}
    assert agent["setup_hash"] == compute_setup_hash(agent)
    assert agent["setup_hash"] != without["setup_hash"]

    for provider in ("", "   ", None):
        assert built({"provider": provider, "name": "gpt-4o"})["model"] == {
            "provider": None,
            "name": "gpt-4o",
            "temperature": None,
            "max_tokens": None,
        }

    long = built({"provider": "p" * 300, "name": "m" * 300, "path": "a.py", "line": 1})
    assert long["model"]["provider"] == "p" * 255
    assert long["model"]["name"] == "m" * 255

    with_url = built(
        {
            "provider": "openai",
            "name": "https://u:p@host.example:8443/v1/m?token=abc",
            "path": "agent.py",
            "line": 9,
        }
    )
    assert with_url["model"]["name"] == "https://host.example:8443/v1/m"
    assert with_url["extension"]["contract"]["model"] == {
        "provider": "openai",
        "name": "https://host.example:8443/v1/m",
        "path": "agent.py",
        "line": 9,
    }
    assert "token=abc" not in json.dumps(with_url)
    assert "u:p@" not in json.dumps(with_url)
