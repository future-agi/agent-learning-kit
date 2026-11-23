"""Replay: contracts that work today save and bundle byte for byte as before (TH-8800, TH-8810).

``fixtures/replay_th8800/before.json`` was recorded from the kit as it was before the change
(`2a26eb2e`) by ``record()`` below; every case is saved with ``accept_contract`` and then bundled
with ``author_bundle_v2``, as a reading's contract is. A case that works today must come out the
same, and the contracts the change is for must differ exactly as designed. Free: no model.

Re-record only from the kit as it was, never from the current code:
    PYTHONPATH=<old src> python -c "import test_reading_replay as t; t.record(path)"
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import pytest

from test_bundle_author_v2 import (
    BundleAuthorError,
    _authoring,
    _callback,
    _github_job,
    _job,
    _repo,
    author_bundle_v2,
)
from fi.alk.harness.bundle_author_v2 import _CALLBACK_SPAN_ENVIRONMENT
from fi.alk.harness.tools import accept_contract

HERE = Path(__file__).parent
READINGS = HERE / "fixtures" / "readings_12nov"
GOLDEN = HERE / "fixtures" / "replay_th8800" / "before.json"
PROVIDERS = HERE.parent / "providers" / "fixtures" / "authored"
SDK = "examples/harness/openai_agents_support"
DEEP = "examples/harness/deep_agents_support"
TOOLS = ("look_up_order", "start_return", "return_policy")
DEFS = "".join(f"def {name}(order_id=None):\n    return order_id\n" for name in TOOLS)
PROJECT = "[project]\nname='proj'\nversion='1'\n"


def reading(name: str) -> dict:
    """A reading less the quotes and model that cite lines of source we don't hold."""
    body = json.loads((READINGS / f"{name}.json").read_text())
    body.update(system_prompt_excerpt="", system_prompt_from=[], model=None)
    return body


def entries(module: str, mode: str = "import", names=TOOLS) -> list[dict]:
    return [{"tool": n, "mode": mode, "module": module, "callable": n} for n in names]


def payload(tool_entries, runtime=None, **fields) -> dict:
    return {
        "agent": "replay",
        "modality": "chat",
        "real_use_cases": ["order"],
        "data_store": {"kind": "none"},
        "tools": [{"name": e["tool"], "args": ["order_id"]} for e in tool_entries],
        "tool_entrypoints": tool_entries,
        **({"runtime": {"language": "python", **runtime}} if runtime else {}),
        **fields,
    }


def graph(text: dict | str) -> str:
    return text if isinstance(text, str) else json.dumps(text)


ONE_GRAPH = {"graphs": {"support": "./agent.py:graph"}}
CLI = {
    "interface": {"kind": "http", "protocol": "json_template", "port": 2024, "path": "/runs/wait",
                  "health_path": "/ok", "request_template": {"input": {"messages": "{{messages}}"}},
                  "response_path": "messages.-1.content"},
    "execution": "native",
}


def langgraph_case(*, command, requirements="langgraph\n", config=ONE_GRAPH, module="agent") -> dict:
    return {
        "files": {
            f"{DEEP}/agent.py": DEFS + "graph = object()\n",
            f"{DEEP}/langgraph.json": graph(config),
            f"{DEEP}/requirements.txt": requirements,
        },
        "subfolder": DEEP,
        "payload": payload(entries(module), {"command": command, "workdir": DEEP, **CLI}),
    }


def cases() -> dict[str, dict]:
    pydantic = reading("pydantic_ai")
    adk = reading("google_adk")
    harbor = reading("harbor_hotels")
    sdk_saved = reading("openai_agents")
    for one in sdk_saved["tool_entrypoints"]:
        one["module"] = "agent"
    out: dict[str, dict] = {
        "pydantic_ai": {
            "files": {f"{pydantic['runtime']['source_subfolder']}/agent.py": DEFS + _callback()},
            "subfolder": pydantic["runtime"]["source_subfolder"],
            "payload": pydantic,
        },
        "google_adk": {
            "files": {
                f"{adk['runtime']['source_subfolder']}/support_agent/agent.py": DEFS + "root_agent = object()\n",
                f"{adk['runtime']['source_subfolder']}/support_agent/__init__.py": "",
                f"{adk['runtime']['source_subfolder']}/requirements.txt": "google-adk\nmcp\n",
            },
            "subfolder": adk["runtime"]["source_subfolder"],
            "payload": adk,
        },
        "langchain_harbor_hotels": {
            "files": {
                "agent.py": "def check_availability(city=None):\n    return city\n"
                "def book_room(room=None):\n    return room\n",
                "requirements.txt": "langchain\n",
            },
            "subfolder": None,
            "payload": harbor,
        },
        "livekit_voice_with_unreachable": {
            "files": {"agent.py": "print('agent')\n", "pyproject.toml": PROJECT},
            "subfolder": None,
            "voice": True,
            "payload": {
                "modality": "voice",
                "tool_entrypoints": entries("agent", "unreachable", ("search_books",)),
            },
        },
        # Probes of contracts that work today.
        "no_subfolder_root_relative_module": {
            "files": {
                "examples/harness/support/agent.py": DEFS + _callback(),
                "requirements.txt": "agent-simulate\n",
            },
            "subfolder": None,
            "payload": payload(entries("examples.harness.support.agent")),
        },
        "no_subfolder_unreachable_root_relative_module": {
            "files": {
                "examples/harness/support/agent.py": DEFS + _callback(),
                "requirements.txt": "agent-simulate\n",
            },
            "subfolder": None,
            "payload": payload(entries("examples.harness.support.agent", "unreachable")),
        },
        "agent_folder_inside_a_regular_package": {
            "files": {
                "bench/__init__.py": "",
                "bench/envs/__init__.py": "",
                "bench/envs/retail/__init__.py": "",
                "bench/envs/retail/data.py": "ORDERS = {}\n",
                "bench/envs/retail/tools.py": "from .data import ORDERS\n" + DEFS + _callback(),
                "bench/pyproject.toml": PROJECT,
            },
            "subfolder": "bench/envs/retail",
            "payload": payload(entries("bench.envs.retail.tools")),
        },
        "unreachable_inside_a_regular_package": {
            "files": {
                "bench/__init__.py": "",
                "bench/envs/retail/tools.py": DEFS + _callback(),
                "bench/pyproject.toml": PROJECT,
            },
            "subfolder": "bench/envs/retail",
            "payload": payload(entries("bench.envs.retail.tools", "unreachable")),
        },
        "two_graphs_with_the_cli_installed": langgraph_case(
            command=["langgraph", "dev"],
            requirements="langgraph\nlanggraph-cli[inmem]\n",
            config={"graphs": {"a": "./agent.py:graph", "b": "./agent.py:graph"}},
        ),
        "langgraph_json_with_http": langgraph_case(
            command=["langgraph", "dev"], config={**ONE_GRAPH, "http": {"app": "./app.py:app"}}
        ),
        "uv_run_langgraph_dev": langgraph_case(
            command=["uv", "run", "--with", "langgraph-cli[inmem]", "langgraph", "dev"]
        ),
        "outer_svc_agent_beside_inner": {
            "files": {"svc/agent.py": DEFS + _callback(), "svc/svc/agent.py": DEFS, "svc/pyproject.toml": PROJECT},
            "subfolder": "svc",
            "payload": payload(entries("svc.agent")),
        },
        "workdir_deeper_than_the_subfolder": {
            "files": {f"{SDK}/app/agent.py": DEFS + _callback(), f"{SDK}/app/requirements.txt": "x\n"},
            "subfolder": SDK,
            "payload": payload(entries("agent", "unreachable"), {"workdir": f"{SDK}/app"}),
        },
        # What the change is for.
        "agents_sdk_12_nov_reading": {
            "files": {f"{SDK}/agent.py": DEFS + _callback(), f"{SDK}/requirements.txt": "openai-agents\n"},
            "subfolder": SDK,
            "payload": reading("openai_agents"),
        },
        "deep_agents_12_nov_reading": {
            "files": {
                f"{DEEP}/agent.py": DEFS + "graph = object()\n",
                f"{DEEP}/langgraph.json": graph(ONE_GRAPH),
                f"{DEEP}/requirements.txt": "deepagents\nlanggraph\n",
            },
            "subfolder": DEEP,
            "payload": reading("deep_agents"),
        },
        # Condition 2: a contract saved earlier, an `unreachable` entry that names its module
        # and callable. It saves as it did and now records calls.
        "saved_unreachable_entry_with_module_and_callable": {
            "files": {f"{SDK}/agent.py": DEFS + _callback(), f"{SDK}/requirements.txt": "openai-agents\n"},
            "subfolder": SDK,
            "payload": sdk_saved,
        },
    }
    for path in sorted(PROVIDERS.glob("*.json")):
        body = json.loads(path.read_text())["contract"]
        body["real_use_cases"] = body.get("real_use_cases") or ["order"]
        out[f"provider_{path.stem}"] = {
            "files": {}, "subfolder": None, "save_only": True, "payload": body,
        }
    return out


CHANGED = ("agents_sdk_12_nov_reading", "deep_agents_12_nov_reading")
# An `unreachable` entry that names its module and callable saves as it did, and is now traced.
TRACED = (
    "saved_unreachable_entry_with_module_and_callable",
    "no_subfolder_unreachable_root_relative_module",
    "unreachable_inside_a_regular_package",
    "workdir_deeper_than_the_subfolder",
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def run_case(case: dict, root: Path) -> dict:
    """What saving and then bundling this contract produces."""
    record: dict = {}
    root.mkdir(parents=True, exist_ok=True)
    source = _repo(root, case["files"]) if case["files"] else root / "source"
    source.mkdir(exist_ok=True)
    subfolder = case["subfolder"]
    saved_path = root / "saved"
    if case.get("voice"):
        saved_text = json.dumps(case["payload"])
    else:
        said = accept_contract(
            json.loads(json.dumps(case["payload"])), saved_path,
            source_root=source if case["files"] else None, subfolder=subfolder,
        )
        if said.get("is_error"):
            return {"save": "refused: " + said["content"][0]["text"]}
        saved_text = (saved_path / "contract.json").read_text()
        saved = json.loads(saved_text)
        record["saved_sha"] = _sha(saved_text)
        record["saved_entries"] = [
            [one["mode"], one["module"], one["callable"]] for one in saved["tool_entrypoints"]
        ]
        if case.get("save_only"):
            return record
    authoring = _authoring(root / "bundle-authoring")
    (authoring / "contract.json").write_text(saved_text, encoding="utf-8")
    import sqlite3

    with sqlite3.connect(authoring / "world.sqlite") as database:
        database.execute("CREATE TABLE fixture (id TEXT PRIMARY KEY)")
    required = sorted(
        {
            name
            for names in ((json.loads(saved_text).get("runtime") or {}).get("required_environment") or {}).values()
            for name in names
        }
    )
    aliases = tuple(required) or ("OPENAI_API_KEY",)
    if case.get("voice"):
        job = _job(
            connector="auto", with_secrets=True,
            secret_aliases=("LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "LIVEKIT_URL"),
        )
    else:
        job = _github_job(
            subfolder, with_secrets=True, secret_aliases=aliases,
            metadata={"generic_harness_v1": True},
        )
    output = root / "bundle"
    try:
        manifest = author_bundle_v2(source=source, job=job, authoring=authoring, output=output)
    except BundleAuthorError as error:
        record["bundle"] = "refused: " + str(error).replace(str(root), "<root>")
        return record
    control = next(p for p in manifest.processes if p.name == manifest.runtime.control_service)
    record.update(
        sealed_sha=_sha((output / "contract.json").read_text()),
        control_environment=dict(sorted(control.environment.items())),
        run_command=list(getattr(control, "run_command", []) or []),
        trace_version=manifest.metadata.get("source_tool_trace_version"),
        handlers=sorted(p.name for p in (output / "handlers").glob("*")) if (output / "handlers").exists() else [],
    )
    return record


def record(path: str) -> None:
    """Write the goldens. Run only against the kit as it was before the change."""
    goldens = {}
    for name, case in cases().items():
        with tempfile.TemporaryDirectory() as folder:
            goldens[name] = run_case(case, Path(folder))
    Path(path).write_text(json.dumps(goldens, indent=1, sort_keys=True) + "\n")


def _golden() -> dict:
    return json.loads(GOLDEN.read_text())


def _callback_cases() -> list[str]:
    """The cases served by the callback adapter. None declares a runtime command, so the build now switches their
    framework spans on."""
    golden = json.loads(GOLDEN.read_text())
    return sorted(name for name, one in golden.items() if "ALK_CALLBACK_ENTRYPOINT" in (one.get("control_environment") or {}))


def _without_the_span_switch(after: dict) -> dict:
    environment = {k: v for k, v in after["control_environment"].items() if k not in _CALLBACK_SPAN_ENVIRONMENT}
    if "ALK_TOOL_TRACE_BINDINGS" in environment:
        # The tracer writes to the same file whether or not the spans are on.
        environment["HARNESS_TOOL_TRACE"] = _CALLBACK_SPAN_ENVIRONMENT["HARNESS_TOOL_TRACE"]
    return {**after, "control_environment": environment}


def test_the_goldens_cover_every_case():
    assert sorted(_golden()) == sorted(cases())


def test_a_replay_is_the_same_in_another_folder(tmp_path):
    case = cases()["pydantic_ai"]
    first = run_case(case, tmp_path / "one")
    assert first == run_case(case, tmp_path / "two")


@pytest.mark.parametrize("name", sorted(set(cases()) - set(CHANGED) - set(TRACED) - set(_callback_cases())))
def test_a_contract_that_works_today_replays_byte_for_byte(tmp_path, name):
    assert run_case(cases()[name], tmp_path) == _golden()[name]


def test_the_callback_cases_are_the_ones_the_span_switch_reaches():
    assert len(_callback_cases()) == 9
    assert set(CHANGED) & set(_callback_cases()) == {"agents_sdk_12_nov_reading"}
    assert set(TRACED) <= set(_callback_cases())


@pytest.mark.parametrize("name", sorted(set(_callback_cases()) - set(CHANGED) - set(TRACED)))
def test_a_callback_bundle_differs_only_by_the_span_switch(tmp_path, name):
    """Its environment is the one it had plus the switch, with the same tracer bindings; nothing else moves but the
    trace version, which a bundle whose spans are on carries."""
    before, after = _golden()[name], run_case(cases()[name], tmp_path)
    assert {k: after["control_environment"][k] for k in _CALLBACK_SPAN_ENVIRONMENT} == _CALLBACK_SPAN_ENVIRONMENT
    assert after["control_environment"].get("ALK_TOOL_TRACE_BINDINGS") == before["control_environment"].get(
        "ALK_TOOL_TRACE_BINDINGS"
    )
    assert after["trace_version"] == 1
    assert {**_without_the_span_switch(after), "trace_version": before["trace_version"]} == before


def test_the_agents_sdk_reading_changes_as_designed(tmp_path):
    before, after = _golden()["agents_sdk_12_nov_reading"], run_case(cases()["agents_sdk_12_nov_reading"], tmp_path)
    assert [m for _, m, _ in before["saved_entries"]] == ["examples.harness.openai_agents_support.agent"] * 3
    assert [(mode, module) for mode, module, _ in after["saved_entries"]] == [("unreachable", "agent")] * 3
    assert "ALK_TOOL_TRACE_BINDINGS" not in before["control_environment"]
    assert json.loads(after["control_environment"]["ALK_TOOL_TRACE_BINDINGS"]) == [
        {"name": n, "module": "agent", "callable": n, "qualname": n} for n in TOOLS
    ]
    assert after["trace_version"] == 1 and before["trace_version"] is None
    assert {k: after["control_environment"][k] for k in _CALLBACK_SPAN_ENVIRONMENT} == _CALLBACK_SPAN_ENVIRONMENT
    assert after["handlers"] == before["handlers"] == []
    assert after["run_command"] == before["run_command"]


def test_the_deep_agents_reading_changes_as_designed(tmp_path):
    before, after = _golden()["deep_agents_12_nov_reading"], run_case(cases()["deep_agents_12_nov_reading"], tmp_path)
    assert [m for _, m, _ in after["saved_entries"]] == ["agent"] * 3
    assert [m for _, m, _ in before["saved_entries"]] == ["examples.harness.deep_agents_support.agent"] * 3
    assert "ALK_LANGGRAPH_ENTRYPOINT" not in before["control_environment"]
    assert after["control_environment"]["ALK_LANGGRAPH_ENTRYPOINT"] == "agent.py:graph"
    assert after["run_command"] != before["run_command"]


@pytest.mark.parametrize("name", TRACED)
def test_an_unreachable_entry_that_names_its_module_saves_as_it_did_and_is_now_traced(tmp_path, name):
    """Condition 2: the platform will see these contracts grade differently, since the calls are
    now recorded. The saved contract, the run command and the handlers don't move; the module is
    bound as saved (so a root-relative name stays as the process carries it)."""
    before, after = _golden()[name], run_case(cases()[name], tmp_path)
    assert after["saved_sha"] == before["saved_sha"]
    assert after["sealed_sha"] == before["sealed_sha"]
    assert "ALK_TOOL_TRACE_BINDINGS" not in before["control_environment"]
    assert "HARNESS_TOOL_TRACE" not in before["control_environment"]
    assert before["trace_version"] is None and after["trace_version"] == 1
    bound = json.loads(after["control_environment"]["ALK_TOOL_TRACE_BINDINGS"])
    assert [(one["name"], one["module"]) for one in bound] == [
        (callable_, module) for _, module, callable_ in after["saved_entries"]
    ]
    assert after["control_environment"]["HARNESS_TOOL_TRACE"] == "{{WORLD_DIR}}/agent-tool-calls.jsonl"
    # Nothing else about the bundle moves but the span switch, which keeps the tracer's `unreachable` bindings.
    assert {k: after["control_environment"][k] for k in _CALLBACK_SPAN_ENVIRONMENT} == _CALLBACK_SPAN_ENVIRONMENT
    keep = {"ALK_TOOL_TRACE_BINDINGS", *_CALLBACK_SPAN_ENVIRONMENT}
    strip = {k: v for k, v in after["control_environment"].items() if k not in keep}
    assert strip == before["control_environment"]
    assert after["run_command"] == before["run_command"] and after["handlers"] == before["handlers"]
