from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from fi.alk.harness.amend import add_rule
from fi.alk.harness.contract import AgentContract, Runtime
from fi.alk.harness.tools import accept_contract, contract_tools
from fi.alk.harness.understand import load, open_stage
from fi.alk.harness.sources import resolve


def payload(**fields):
    return {
        "agent": "pair",
        "modality": "chat",
        "real_use_cases": ["order"],
        "tools": [{"name": "add", "args": ["item_id"]}],
        **fields,
    }


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for name in ("a", "b"):
        (root / "agents" / name).mkdir(parents=True)
        (root / "agents" / name / "agent.py").write_text("x = 1\n")
    (root / "agents" / "b" / "traced.py").write_text("import traceai_openai\n")
    return root


def _saved(destination: Path) -> dict:
    return json.loads((destination / "contract.json").read_text())


def _submit(server, arguments: dict, destination: Path) -> dict:
    """What the model's call does; a first thin submission is sent back once, then accepted."""
    result = {}
    for _ in range(2):
        result = asyncio.run(server.tools[0].handler(arguments))
        if (destination / "contract.json").exists():
            break
    return result


def test_save_defaults_workdir_and_records_folder(tmp_path, repo):
    result = accept_contract(
        payload(), tmp_path / "out", source_root=repo, subfolder="agents/a"
    )

    assert not result.get("is_error")
    runtime = _saved(tmp_path / "out")["runtime"]
    assert runtime["source_subfolder"] == "agents/a"
    assert runtime["workdir"] == "agents/a"


def test_save_refuses_workdir_outside(tmp_path, repo):
    result = accept_contract(
        payload(runtime={"language": "python", "workdir": "agents/b"}),
        tmp_path / "out",
        source_root=repo,
        subfolder="agents/a",
    )

    assert result.get("is_error")
    assert "runtime_workdir_outside_subfolder" in result["content"][0]["text"]
    assert not (tmp_path / "out" / "contract.json").exists()


def test_no_subfolder_contract_dump_unchanged(tmp_path, repo):
    assert "source_subfolder" not in Runtime().model_dump()
    assert "source_subfolder" not in AgentContract(agent="x", runtime=Runtime()).model_dump()["runtime"]
    assert "source_subfolder" not in json.dumps(Runtime.model_json_schema())

    for subfolder in (None, ""):
        destination = tmp_path / f"out-{subfolder!r}"
        accept_contract(
            payload(runtime={"language": "python", "source_subfolder": "agents/z"}),
            destination,
            source_root=repo,
            subfolder=subfolder,
        )
        runtime = _saved(destination)["runtime"]
        assert "source_subfolder" not in runtime
        assert runtime["workdir"] == ""


def test_amend_keeps_recorded_folder(tmp_path, repo):
    accept_contract(payload(), tmp_path, source_root=repo, subfolder="agents/a")
    contract = load(tmp_path)

    added, _ = add_rule(contract, tmp_path, rule="Never share secrets", why="policy")

    assert added
    assert _saved(tmp_path)["runtime"]["source_subfolder"] == "agents/a"


def test_instrumentation_reads_agent_folder(tmp_path, repo):
    own = tmp_path / "own"
    accept_contract(payload(), own, source_root=repo, subfolder="agents/a")
    assert _saved(own)["instrumentation"] == "none"

    whole = tmp_path / "whole"
    accept_contract(payload(), whole, source_root=repo)
    assert _saved(whole)["instrumentation"] == "traceai"


def test_save_on_missing_folder_saves_nothing(tmp_path, repo):
    destination = tmp_path / "out"
    server = contract_tools(destination, source_root=repo, subfolder="agents/zzz")

    result = _submit(server, payload(), destination)

    assert result.get("is_error")
    assert "subfolder_not_found" in result["content"][0]["text"]
    assert not (destination / "contract.json").exists()


def test_save_with_no_source_root_works(tmp_path):
    result = accept_contract(payload(), tmp_path, subfolder="agents/a")

    assert not result.get("is_error")
    assert "source_subfolder" not in (_saved(tmp_path).get("runtime") or {})


def test_no_subfolder_null_runtime_stays_null(tmp_path, repo):
    source = resolve("repo", name="demo", root=str(repo), subfolder="")
    stage, destination = open_stage(source, out=tmp_path / "out")

    _submit(stage.spec.servers["contract"], payload(), destination)

    assert _saved(destination).get("runtime") is None


@pytest.mark.parametrize("subfolder", ["agents/a", ""])
def test_open_stage_saves_the_folder(tmp_path, repo, subfolder):
    source = resolve("repo", name="demo", root=str(repo), subfolder=subfolder)
    stage, destination = open_stage(source, out=tmp_path / "out")

    _submit(
        stage.spec.servers["contract"],
        payload(runtime={"language": "python"}),
        destination,
    )

    runtime = _saved(destination)["runtime"]
    if subfolder:
        assert runtime["source_subfolder"] == "agents/a"
        assert runtime["workdir"] == "agents/a"
    else:
        assert "source_subfolder" not in runtime
        assert runtime["workdir"] == ""


def _compose_stack(root: Path, location: str, builds: str) -> dict:
    (root / location).parent.mkdir(parents=True, exist_ok=True)
    (root / location).write_text(
        f"services:\n  agent:\n    build: {builds}\n    command: [python, agent.py]\n"
    )
    return {
        "language": "python",
        "execution": "compose",
        "compose_file": location,
        "service": "agent",
    }


@pytest.mark.parametrize(
    ("location", "builds"),
    [("compose.yaml", "agents/b"), ("agents/a/compose.yaml", "../b")],
)
def test_save_refuses_a_control_service_building_a_sibling(tmp_path, repo, location, builds):
    runtime = _compose_stack(repo, location, builds)

    result = accept_contract(
        payload(runtime=runtime), tmp_path / "out", source_root=repo, subfolder="agents/a"
    )

    assert result.get("is_error")
    assert "runtime_compose_service_outside_subfolder" in result["content"][0]["text"]
    assert not (tmp_path / "out" / "contract.json").exists()


def test_save_keeps_a_control_service_building_the_folder(tmp_path, repo):
    runtime = _compose_stack(repo, "compose.yaml", "agents/a")

    result = accept_contract(
        payload(runtime=runtime), tmp_path / "out", source_root=repo, subfolder="agents/a"
    )

    assert not result.get("is_error")
    assert _saved(tmp_path / "out")["runtime"]["source_subfolder"] == "agents/a"


def _compose_with_declared_port(root: Path, location: str, builds: str) -> dict:
    (root / location).parent.mkdir(parents=True, exist_ok=True)
    (root / location).write_text(
        f"services:\n  agent:\n    build: {builds}\n    command: [python, agent.py]\n"
        '    ports: ["${AGENT_PORT}:8080"]\n'
    )
    (root / "agents" / "a" / "alk.yaml").write_text(
        'runtime:\n  environment:\n    AGENT_PORT: "9000"\n'
    )
    return {
        "language": "python",
        "execution": "compose",
        "compose_file": location,
        "service": "agent",
    }


def test_save_reads_the_values_the_folder_declares(tmp_path, repo):
    runtime = _compose_with_declared_port(repo, "compose.yaml", "agents/a")

    result = accept_contract(
        payload(runtime=runtime), tmp_path / "out", source_root=repo, subfolder="agents/a"
    )

    assert not result.get("is_error"), result


def test_save_still_refuses_a_sibling_with_declared_values(tmp_path, repo):
    runtime = _compose_with_declared_port(repo, "compose.yaml", "agents/b")

    result = accept_contract(
        payload(runtime=runtime), tmp_path / "out", source_root=repo, subfolder="agents/a"
    )

    assert "runtime_compose_service_outside_subfolder" in result["content"][0]["text"]


def test_save_tells_the_model_about_a_bad_alk_yaml(tmp_path, repo):
    (repo / "agents" / "a" / "alk.yaml").write_text("- not an object\n")

    result = accept_contract(
        payload(runtime={"language": "python"}),
        tmp_path / "out",
        source_root=repo,
        subfolder="agents/a",
    )

    assert "runtime_manifest_invalid" in result["content"][0]["text"]
    assert not (tmp_path / "out" / "contract.json").exists()


def test_save_checks_a_tool_in_the_folder_the_run_imports_from(tmp_path, repo):
    (repo / "agent.py").write_text("def lookup():\n    return 1\n")
    (repo / "agents" / "a" / "agent.py").write_text("# def lookup():\n#     return 1\n")
    entry = {"tool": "lookup", "mode": "import", "module": "agent", "callable": "lookup"}

    result = accept_contract(
        payload(tools=[{"name": "lookup"}], tool_entrypoints=[entry]),
        tmp_path / "out",
        source_root=repo,
        subfolder="agents/a",
    )

    assert result.get("is_error"), result
    assert "lookup" in result["content"][0]["text"]
    assert not (tmp_path / "out" / "contract.json").exists()


@pytest.mark.parametrize("shell", ["sh", "/bin/sh"])
def test_save_refuses_a_shell_command_only_under_a_folder(tmp_path, repo, shell):
    runtime = {"language": "python", "command": [shell, "-c", "cd ../b && python agent.py"]}

    refused = accept_contract(
        payload(runtime=runtime), tmp_path / "in", source_root=repo, subfolder="agents/a"
    )
    kept = accept_contract(
        payload(runtime=runtime), tmp_path / "root", source_root=repo
    )

    assert refused.get("is_error")
    assert "runtime_command_shell_forbidden" in refused["content"][0]["text"]
    assert not (tmp_path / "in" / "contract.json").exists()
    assert not kept.get("is_error")
    assert _saved(tmp_path / "root")["runtime"]["command"] == runtime["command"]
