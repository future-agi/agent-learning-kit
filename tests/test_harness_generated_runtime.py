import json
from typing import Any
import logging
import os
import stat
import subprocess
import tempfile
import time
import types
from pathlib import Path

import pytest

from fi.alk.harness.contract import AgentContract, DataStore, Runtime, ToolSpec
from fi.alk.harness.bundle import export_session_bundle
from fi.alk.harness.generated_runtime import (
    GENERATED_DOCKERFILE,
    GeneratedRuntimeError,
    detect_generated_runtime,
    prepare_generated_runtime,
)
from fi.alk.harness import provision as provisioning
from fi.alk.harness.contract import Dependency
from fi.alk.harness.provision import reset, start_runtime, stop, stop_runtime
from fi.alk.harness.secrets import agent_value_name_reserved, client_name_reserved


def _contract(**runtime) -> AgentContract:
    return AgentContract(
        agent="unpackaged-agent",
        tools=[ToolSpec(name="answer", args=["question"])],
        real_use_cases=["answer a caller"],
        runtime=Runtime(**runtime),
    )


def test_unambiguous_python_repository_gets_a_non_root_plan(tmp_path: Path) -> None:
    source = tmp_path / "agent"
    source.mkdir()
    source.joinpath("requirements.txt").write_text("requests==2.32.4\n")
    source.joinpath("agent.py").write_text(
        'if __name__ == "__main__":\n    print("ready")\n'
    )

    plan = detect_generated_runtime(source, Runtime())

    assert plan.language == "python"
    assert plan.install_strategy == "pip-requirements"
    assert plan.command == ("python", "agent.py")
    assert "USER 10001:10001" in plan.dockerfile
    assert (
        "apt-get install -y --no-install-recommends build-essential" in plan.dockerfile
    )


def test_legacy_setup_py_repository_gets_generated_runtime(tmp_path: Path) -> None:
    source = tmp_path / "agent"
    source.mkdir()
    source.joinpath("setup.py").write_text(
        "from setuptools import setup\nsetup(name='legacy-chat-agent', version='0.1')\n"
    )
    source.joinpath("run.py").write_text("print('ready')\n")

    plan = detect_generated_runtime(
        source, Runtime(language="python", command=["python", "run.py"])
    )

    assert plan.install_strategy == "pip-setup-project-unlocked"
    assert plan.command == ("python", "run.py")
    assert "RUN pip install --no-cache-dir ." in plan.dockerfile


def test_livekit_python_entrypoint_uses_worker_start_mode(tmp_path: Path) -> None:
    source = tmp_path / "agent"
    source.mkdir()
    source.joinpath("requirements.txt").write_text("livekit-agents==1.2.0\n")
    source.joinpath("agent.py").write_text(
        "from livekit.agents import cli\ncli.run_app(object())\n"
    )

    plan = detect_generated_runtime(source, Runtime())

    assert plan.command == ("python", "agent.py", "start")


def test_lockless_uv_script_project_uses_uv_and_downloads_livekit_models(
    tmp_path: Path,
) -> None:
    source = tmp_path / "agent"
    source.mkdir()
    source.joinpath("pyproject.toml").write_text(
        """[project]
name = "voice-script"
version = "0"
dependencies = [
  "livekit-agents>=1.6",
  "livekit-plugins-silero>=1.5.7",
]
[tool.uv]
package = false
"""
    )
    source.joinpath("agent.py").write_text(
        "from livekit.agents import cli\ncli.run_app(object())\n"
    )

    plan = detect_generated_runtime(source, Runtime())

    assert plan.install_strategy == "uv-script-project-unlocked"
    assert "uv sync --no-cache" in plan.dockerfile
    assert "pip install --no-cache-dir '.'" not in plan.dockerfile
    assert "python -m livekit.agents download-files" in plan.dockerfile


def test_python_compatibility_floor_does_not_become_an_exact_old_image(
    tmp_path: Path,
) -> None:
    source = tmp_path / "agent"
    source.mkdir()
    source.joinpath("pyproject.toml").write_text(
        """[project]
name = "voice-script"
version = "0"
requires-python = ">=3.10"
dependencies = []
[tool.uv]
package = false
"""
    )
    source.joinpath("agent.py").write_text("print('ready')\n")

    plan = detect_generated_runtime(source, Runtime(version=">=3.10"))

    assert plan.version == "3.12"
    assert plan.dockerfile.startswith("FROM python:3.12-slim")


def test_selected_component_accepts_contract_absolute_monorepo_hint(
    tmp_path: Path,
) -> None:
    source = tmp_path / "examples" / "drive_thru"
    source.mkdir(parents=True)
    source.joinpath("requirements.txt").write_text("")
    source.joinpath("agent.py").write_text("print('ready')\n")

    plan = detect_generated_runtime(
        source, Runtime(language="python", workdir="/examples/drive_thru")
    )

    assert plan.component == ""


def test_selected_component_accepts_contract_relative_monorepo_hint(
    tmp_path: Path,
) -> None:
    root = tmp_path / "examples" / "hotel_receptionist"
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "hotel"\nversion = "0"\n', encoding="utf-8"
    )
    (root / "agent.py").write_text("print('ready')\n", encoding="utf-8")

    runtime = Runtime(
        language="python",
        version="3.12",
        workdir="examples/hotel_receptionist",
        command=[],
        extras=[],
    )
    plan = detect_generated_runtime(root, runtime)

    assert plan.component == ""
    assert plan.command == ("python", "agent.py")


def test_repository_root_workdir_sentinel_uses_submitted_source(tmp_path: Path) -> None:
    source = tmp_path / "agent"
    source.mkdir()
    source.joinpath("requirements.txt").write_text("")
    source.joinpath("agent.py").write_text("print('ready')\n")

    plan = detect_generated_runtime(source, Runtime(workdir="/"))

    assert plan.component == ""
    assert plan.command == ("python", "agent.py")


def test_unrelated_absolute_workdir_remains_rejected(tmp_path: Path) -> None:
    source = tmp_path / "agent"
    source.mkdir()
    source.joinpath("requirements.txt").write_text("")
    source.joinpath("agent.py").write_text("print('ready')\n")

    with pytest.raises(GeneratedRuntimeError, match="workdir_escapes_repository"):
        detect_generated_runtime(source, Runtime(workdir="/etc"))


def test_node_repository_uses_lockfile_and_start_script(tmp_path: Path) -> None:
    source = tmp_path / "agent"
    source.mkdir()
    source.joinpath("package.json").write_text(
        json.dumps({"scripts": {"start": "node src/agent.js"}})
    )
    source.joinpath("package-lock.json").write_text("{}")

    plan = detect_generated_runtime(source, Runtime(version="20"))

    assert plan.install_strategy == "npm-ci"
    assert plan.command == ("npm", "run", "start")
    assert plan.dockerfile.startswith("FROM node:20-slim")
    assert "USER node" in plan.dockerfile


def test_node_start_runtime_runs_declared_build_phase(tmp_path: Path) -> None:
    source = tmp_path / "agent"
    source.mkdir()
    source.joinpath("package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "tsc",
                    "start": "node dist/agent.js",
                }
            }
        )
    )
    source.joinpath("package-lock.json").write_text("{}")

    plan = detect_generated_runtime(source, Runtime())

    assert "RUN npm run build" in plan.dockerfile


def test_python_optional_group_is_inferred_from_grounded_entrypoint(
    tmp_path: Path,
) -> None:
    source = tmp_path / "agent"
    source.mkdir()
    source.joinpath("pyproject.toml").write_text(
        """[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"
[project]
name = "voice-agent"
version = "1.0.0"
[project.optional-dependencies]
voice = ["sounddevice>=0.4"]
dev = ["pytest"]
"""
    )
    source.joinpath("examples").mkdir()
    source.joinpath("examples/voice_demo.py").write_text("print('voice')\n")

    plan = detect_generated_runtime(
        source, Runtime(command=["python", "examples/voice_demo.py"])
    )

    assert plan.extras == ("voice",)
    assert "'.[voice]'" in plan.dockerfile


def test_ambiguous_entrypoint_stops_before_packaging(tmp_path: Path) -> None:
    source = tmp_path / "agent"
    source.mkdir()
    source.joinpath("requirements.txt").write_text("requests==2.32.4\n")
    source.joinpath("agent.py").write_text("print('agent')\n")
    source.joinpath("main.py").write_text("print('main')\n")

    with pytest.raises(GeneratedRuntimeError, match="entrypoint_ambiguous"):
        detect_generated_runtime(source, Runtime())


def test_explicit_argv_resolves_entrypoint_ambiguity(tmp_path: Path) -> None:
    source = tmp_path / "agent"
    source.mkdir()
    source.joinpath("requirements.txt").write_text("requests==2.32.4\n")
    source.joinpath("agent.py").write_text("print('agent')\n")
    source.joinpath("main.py").write_text("print('main')\n")

    plan = detect_generated_runtime(
        source, Runtime(command=["python", "main.py", "--worker"])
    )

    assert plan.command == ("python", "main.py", "--worker")


def test_generated_context_excludes_secrets_and_does_not_mutate_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "agent"
    source.mkdir()
    source.joinpath("requirements.txt").write_text("")
    source.joinpath("agent.py").write_text("print('ready')\n")
    source.joinpath(".env").write_text("OPENAI_API_KEY=secret\n")
    source.joinpath("private.pem").write_text("secret\n")
    before = sorted(path.name for path in source.iterdir())

    plan = prepare_generated_runtime(source, tmp_path / "session", Runtime())
    context = Path(plan.context_directory)

    assert sorted(path.name for path in source.iterdir()) == before
    assert context.joinpath(GENERATED_DOCKERFILE).is_file()
    assert not context.joinpath(".env").exists()
    assert not context.joinpath("private.pem").exists()
    recorded = json.loads(
        tmp_path.joinpath("session/generated-runtime.json").read_text()
    )
    assert recorded["fingerprint"] == plan.fingerprint


def test_symlink_in_generated_context_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "agent"
    source.mkdir()
    source.joinpath("requirements.txt").write_text("")
    source.joinpath("agent.py").write_text("print('ready')\n")
    source.joinpath("escape").symlink_to(tmp_path / "outside")

    with pytest.raises(GeneratedRuntimeError, match="symlink_forbidden"):
        prepare_generated_runtime(source, tmp_path / "session", Runtime())


def test_internal_symlink_is_materialized_without_preserving_link(
    tmp_path: Path,
) -> None:
    source = tmp_path / "agent"
    source.mkdir()
    source.joinpath("requirements.txt").write_text("")
    source.joinpath("agent.py").write_text("print('ready')\n")
    data = source / "data"
    data.mkdir()
    data.joinpath("task.json").write_text('{"task": 1}\n')
    source.joinpath("public-data").symlink_to(data, target_is_directory=True)

    plan = prepare_generated_runtime(source, tmp_path / "session", Runtime())
    materialized = Path(plan.context_directory) / "public-data" / "task.json"

    assert materialized.read_text() == '{"task": 1}\n'
    assert not materialized.parent.is_symlink()


def test_generated_context_omits_explicit_large_output_directory(
    tmp_path: Path,
) -> None:
    source = tmp_path / "agent"
    source.mkdir()
    source.joinpath("requirements.txt").write_text("")
    source.joinpath("agent.py").write_text("print('ready')\n")
    results = source / "data" / "results"
    results.mkdir(parents=True)
    results.joinpath("old.json").write_text("{}\n")

    plan = prepare_generated_runtime(
        source,
        tmp_path / "session",
        Runtime(context_excludes=["data/results"]),
    )

    assert plan.context_excludes == ("data/results",)
    assert not Path(plan.context_directory, "data/results").exists()


def test_generated_context_allows_excluding_not_yet_created_runtime_state(
    tmp_path: Path,
) -> None:
    source = tmp_path / "agent"
    source.mkdir()
    source.joinpath("requirements.txt").write_text("")
    source.joinpath("agent.py").write_text("print('ready')\n")

    plan = prepare_generated_runtime(
        source,
        tmp_path / "session",
        Runtime(context_excludes=["fake_data/hotel.db"]),
    )

    assert plan.context_excludes == ()
    assert Path(plan.context_directory, "agent.py").is_file()


def test_generated_context_cannot_exclude_submitted_entrypoint(tmp_path: Path) -> None:
    source = tmp_path / "agent"
    source.mkdir()
    source.joinpath("requirements.txt").write_text("")
    scripts = source / "scripts"
    scripts.mkdir()
    scripts.joinpath("agent.py").write_text("print('ready')\n")

    with pytest.raises(GeneratedRuntimeError, match="context_exclude_required"):
        detect_generated_runtime(
            source,
            Runtime(
                command=["python", "scripts/agent.py"],
                context_excludes=["scripts"],
            ),
        )


def test_provision_builds_generated_runtime_without_touching_source(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "agent"
    session = tmp_path / "session"
    source.mkdir()
    source.joinpath("requirements.txt").write_text("")
    source.joinpath("agent.py").write_text("print('ready')\n")
    calls: list[tuple[str, ...]] = []

    def run(_environment, *arguments, **_kwargs):
        calls.append(arguments)
        if "config" in arguments and "--format" in arguments:
            return json.dumps(
                {
                    "services": {
                        "agent-runtime": {
                            "profiles": ["harness-runtime"],
                            "build": {
                                "context": str(session / "generated-runtime-context"),
                                "dockerfile": GENERATED_DOCKERFILE,
                            },
                        }
                    }
                }
            )
        return ""

    monkeypatch.setattr(provisioning, "_run", run)
    environment = provisioning.provision(source, session, _contract())

    assert environment.managed and environment.runtime_fingerprint
    assert environment.generated_runtime_plan.endswith("generated-runtime.json")
    assert ("build", "agent-runtime") in calls
    assert not source.joinpath(GENERATED_DOCKERFILE).exists()
    generated = json.loads(session.joinpath("managed-compose.json").read_text())
    runtime = generated["services"]["agent-runtime"]
    assert runtime["command"] == ["python", "agent.py"]
    assert runtime["build"]["context"] == str(session / "generated-runtime-context")


def test_contract_command_changes_runtime_reuse_fingerprint(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "agent"
    session = tmp_path / "session"
    source.mkdir()
    source.joinpath("requirements.txt").write_text("")
    source.joinpath("agent.py").write_text("print('ready')\n")

    def run(_environment, *arguments, **_kwargs):
        if "config" in arguments and "--format" in arguments:
            return json.dumps(
                {"services": {"agent-runtime": {"profiles": ["harness-runtime"]}}}
            )
        return ""

    monkeypatch.setattr(provisioning, "_run", run)
    first = provisioning.provision(
        source, session, _contract(command=["python", "agent.py"])
    )
    second = provisioning.provision(
        source, session, _contract(command=["python", "agent.py", "--verbose"])
    )

    assert first.runtime_fingerprint != second.runtime_fingerprint


def test_external_model_provider_is_not_treated_as_managed_infrastructure(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "agent"
    session = tmp_path / "session"
    source.mkdir()
    source.joinpath("requirements.txt").write_text("")
    source.joinpath("agent.py").write_text("print('ready')\n")
    contract = _contract(command=["python", "agent.py"])
    contract.dependencies = [
        Dependency(
            name="Deepgram",
            kind="service",
            engine="deepgram nova-3",
            what="speech recognition provider",
            reached={"password_from": "DEEPGRAM_API_KEY"},
        )
    ]

    def run(_environment, *arguments, **_kwargs):
        if "config" in arguments and "--format" in arguments:
            return json.dumps(
                {"services": {"agent-runtime": {"profiles": ["harness-runtime"]}}}
            )
        return ""

    monkeypatch.setattr(provisioning, "_run", run)
    environment = provisioning.provision(source, session, contract)

    compose = json.loads(session.joinpath("managed-compose.json").read_text())
    assert set(compose["services"]) == {"agent-runtime"}
    assert environment.runtime_services == ["agent-runtime"]


def test_external_provider_dsn_environment_is_not_a_managed_sidecar(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "agent"
    session = tmp_path / "session"
    source.mkdir()
    source.joinpath("requirements.txt").write_text("")
    source.joinpath("agent.py").write_text("print('ready')\n")
    contract = _contract(command=["python", "agent.py"])
    contract.dependencies = [
        Dependency(
            name="OpenAI",
            kind="service",
            engine="gpt-4.1-mini",
            what="hosted model provider",
            reached={
                "dsn_env": "OPENAI_API_KEY",
                "host": "api.openai.com",
                "port": 443,
            },
        )
    ]

    def run(_environment, *arguments, **_kwargs):
        if "config" in arguments and "--format" in arguments:
            return json.dumps(
                {"services": {"agent-runtime": {"profiles": ["harness-runtime"]}}}
            )
        return ""

    monkeypatch.setattr(provisioning, "_run", run)
    environment = provisioning.provision(source, session, contract)

    compose = json.loads(session.joinpath("managed-compose.json").read_text())
    assert set(compose["services"]) == {"agent-runtime"}
    assert environment.runtime_services == ["agent-runtime"]


def test_importable_library_capability_is_not_a_managed_sidecar(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "agent"
    session = tmp_path / "session"
    source.mkdir()
    source.joinpath("requirements.txt").write_text("")
    source.joinpath("agent.py").write_text("print('ready')\n")
    contract = _contract(command=["python", "agent.py"])
    contract.dependencies = [
        Dependency(
            name="GetEmailTask",
            kind="service",
            engine="livekit-agents beta workflows",
            what="SDK-provided workflow",
            reached={
                "loader_module": "livekit.agents",
                "loader_function": "beta.workflows.GetEmailTask",
            },
        )
    ]

    def run(_environment, *arguments, **_kwargs):
        if "config" in arguments and "--format" in arguments:
            return json.dumps(
                {"services": {"agent-runtime": {"profiles": ["harness-runtime"]}}}
            )
        return ""

    monkeypatch.setattr(provisioning, "_run", run)
    environment = provisioning.provision(source, session, contract)

    compose = json.loads(session.joinpath("managed-compose.json").read_text())
    assert set(compose["services"]) == {"agent-runtime"}
    assert environment.runtime_services == ["agent-runtime"]


def test_generated_runtime_packages_are_not_treated_as_sidecars(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "agent"
    session = tmp_path / "session"
    source.mkdir()
    source.joinpath("pyproject.toml").write_text(
        """[project]
name = "hotel"
version = "0"
dependencies = ["livekit-plugins-silero>=1.6.0", "apsw>=3.46"]
"""
    )
    source.joinpath("agent.py").write_text("print('ready')\n")
    contract = _contract(command=["python", "agent.py"])
    contract.data_store = DataStore(kind="sqlite", host=":memory:")
    contract.dependencies = [
        Dependency(
            name="Silero VAD",
            kind="service",
            engine="livekit-plugins-silero>=1.6.0",
        ),
        Dependency(
            name="APSW SQLite",
            kind="datastore",
            engine="apsw>=3.46",
            reached={"host": ":memory:"},
        ),
    ]

    def run(_environment, *arguments, **_kwargs):
        if "config" in arguments and "--format" in arguments:
            return json.dumps(
                {"services": {"agent-runtime": {"profiles": ["harness-runtime"]}}}
            )
        return ""

    monkeypatch.setattr(provisioning, "_run", run)
    environment = provisioning.provision(source, session, contract)

    compose = json.loads(session.joinpath("managed-compose.json").read_text())
    assert set(compose["services"]) == {"agent-runtime"}
    assert environment.runtime_services == ["agent-runtime"]


def test_in_process_store_needs_no_managed_sidecar(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "agent"
    session = tmp_path / "session"
    source.mkdir()
    source.joinpath("requirements.txt").write_text("")
    source.joinpath("agent.py").write_text("print('ready')\n")
    contract = _contract(command=["python", "agent.py"])
    contract.data_store = {"kind": "in_process", "configured_by": "hardcoded"}

    def run(_environment, *arguments, **_kwargs):
        if "config" in arguments and "--format" in arguments:
            return json.dumps(
                {"services": {"agent-runtime": {"profiles": ["harness-runtime"]}}}
            )
        return ""

    monkeypatch.setattr(provisioning, "_run", run)
    environment = provisioning.provision(source, session, contract)

    compose = json.loads(session.joinpath("managed-compose.json").read_text())
    assert set(compose["services"]) == {"agent-runtime"}
    assert environment.services == []


def test_generated_runtime_persists_only_required_credential_names(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "agent"
    session = tmp_path / "session"
    source.mkdir()
    source.joinpath("requirements.txt").write_text("")
    source.joinpath("agent.py").write_text("print('ready')\n")
    contract = _contract(command=["python", "agent.py"])
    contract.dependencies = [
        Dependency(
            name="LiveKit",
            kind="service",
            reached={
                "dsn_env": "LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET",
            },
        )
    ]

    def run(_environment, *arguments, **_kwargs):
        if "config" in arguments and "--format" in arguments:
            return json.dumps(
                {"services": {"agent-runtime": {"profiles": ["harness-runtime"]}}}
            )
        return ""

    monkeypatch.setattr(provisioning, "_run", run)
    monkeypatch.setenv("LIVEKIT_API_SECRET", "must-never-be-persisted")
    environment = provisioning.provision(source, session, contract)

    assert environment.runtime_configuration_names == [
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "LIVEKIT_URL",
    ]
    persisted = session.joinpath("environment.json").read_text()
    assert "must-never-be-persisted" not in persisted


def test_generated_runtime_includes_runtime_dependency_configuration_names(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "agent"
    session = tmp_path / "session"
    source.mkdir()
    source.joinpath("requirements.txt").write_text("")
    source.joinpath("agent.py").write_text("print('ready')\n")
    contract = _contract(command=["python", "agent.py"])
    contract.runtime_dependencies = [
        Dependency(
            name="Vertex AI",
            kind="transport",
            reached={
                "dsn_env": "GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION",
            },
        )
    ]

    def run(_environment, *arguments, **_kwargs):
        if "config" in arguments and "--format" in arguments:
            return json.dumps(
                {"services": {"agent-runtime": {"profiles": ["harness-runtime"]}}}
            )
        return ""

    monkeypatch.setattr(provisioning, "_run", run)
    environment = provisioning.provision(source, session, contract)

    assert environment.runtime_configuration_names == [
        "GOOGLE_CLOUD_LOCATION",
        "GOOGLE_CLOUD_PROJECT",
    ]


def test_generated_runtime_includes_ephemeral_uploaded_environment_names(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "agent"
    session = tmp_path / "session"
    source.mkdir()
    source.joinpath("requirements.txt").write_text("")
    source.joinpath("agent.py").write_text("print('ready')\n")
    contract = _contract(command=["python", "agent.py"])

    def run(_environment, *arguments, **_kwargs):
        if "config" in arguments and "--format" in arguments:
            return json.dumps(
                {"services": {"agent-runtime": {"profiles": ["harness-runtime"]}}}
            )
        return ""

    monkeypatch.setattr(provisioning, "_run", run)
    monkeypatch.setenv(
        "ALK_RUNTIME_CONFIGURATION_NAMES", "CUSTOM_REGION,FEATURE_FLAG,ALK_FORBIDDEN"
    )
    monkeypatch.setenv("CUSTOM_REGION", "must-never-be-persisted")
    environment = provisioning.provision(source, session, contract)

    assert environment.runtime_configuration_names == ["CUSTOM_REGION", "FEATURE_FLAG"]
    assert (
        "must-never-be-persisted"
        not in session.joinpath("environment.json").read_text()
    )


def test_generated_worker_translates_loopback_livekit_without_persisting_it(
    tmp_path: Path, monkeypatch
) -> None:
    environment = provisioning.ProvisionedEnvironment(
        source=str(tmp_path),
        compose_file=str(tmp_path / "compose.json"),
        project="generated-runtime-test",
        runtime_services=["agent-runtime"],
        runtime_configuration_names=["LIVEKIT_URL"],
        running=True,
    )
    environment.save(tmp_path)
    monkeypatch.setenv("ALK_RUNTIME_VALUE_LIVEKIT_URL", "ws://127.0.0.1:17880")
    monkeypatch.setenv("HARNESS_RUNTIME_STABLE_SECONDS", "0")
    seen: dict[str, str] = {}

    monkeypatch.setattr(
        provisioning,
        "_config",
        lambda _environment: {"services": {"agent-runtime": {"environment": {}}}},
    )

    def run(_environment, *arguments, **kwargs):
        seen.update(kwargs.get("process_overrides") or {})
        return ""

    monkeypatch.setattr(provisioning, "_run", run)
    monkeypatch.setattr(
        provisioning,
        "_docker",
        lambda *arguments, **_kwargs: (
            "running" if arguments and arguments[0] == "inspect" else ""
        ),
    )

    provisioning.start_runtime(tmp_path)

    assert seen["LIVEKIT_URL"] == "ws://host.docker.internal:17880"
    assert "127.0.0.1:17880" not in tmp_path.joinpath("environment.json").read_text()


def test_runtime_does_not_revalidate_container_target_for_mounted_google_credentials(
    tmp_path: Path, monkeypatch
) -> None:
    credential = tmp_path / "google.json"
    credential.write_text('{"type":"service_account"}\n')
    container_target = "/etc/vertex/creds.json"
    environment = provisioning.ProvisionedEnvironment(
        source=str(tmp_path),
        compose_file=str(tmp_path / "compose.json"),
        project="mounted-google-test",
        runtime_services=["agent-runtime"],
        running=True,
    )
    environment.save(tmp_path)
    monkeypatch.setenv("HARNESS_RUNTIME_STABLE_SECONDS", "0")
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        provisioning,
        "_config",
        lambda _environment: {
            "services": {
                "agent-runtime": {
                    "environment": {
                        "GOOGLE_APPLICATION_CREDENTIALS": container_target
                    },
                    "volumes": [
                        {"source": str(credential), "target": container_target}
                    ],
                }
            }
        },
    )

    def run(_environment, *arguments, **kwargs):
        seen["arguments"] = arguments
        seen["injected"] = kwargs.get("process_overrides") or {}
        return ""

    monkeypatch.setattr(provisioning, "_run", run)
    monkeypatch.setattr(
        provisioning,
        "_docker",
        lambda *arguments, **_kwargs: (
            "running" if arguments and arguments[0] == "inspect" else ""
        ),
    )

    provisioning.start_runtime(tmp_path)

    assert f"{credential.resolve()}:{container_target}:ro" in seen["arguments"]
    assert seen["injected"]["GOOGLE_APPLICATION_CREDENTIALS"] == container_target


def test_hosted_runtime_rejects_raw_google_host_path(tmp_path, monkeypatch) -> None:
    credential = tmp_path / "customer.json"
    credential.write_text('{"type":"service_account"}\n')
    monkeypatch.setenv("ALK_HOSTED_EXECUTION", "1")
    monkeypatch.setenv(
        "ALK_RUNTIME_VALUE_GOOGLE_APPLICATION_CREDENTIALS", str(credential)
    )
    monkeypatch.delenv("ALK_RUNTIME_SECRET_FILE_NAMES", raising=False)

    with pytest.raises(provisioning.ProvisionError, match="credential-file control"):
        provisioning._runtime_credential_mounts(
            {"GOOGLE_APPLICATION_CREDENTIALS": "/etc/vertex/creds.json"},
            {
                "volumes": [
                    {"source": "/dev/null", "target": "/etc/vertex/creds.json"}
                ]
            },
        )


def test_hosted_runtime_accepts_provider_authorized_google_file(
    tmp_path, monkeypatch
) -> None:
    credential = tmp_path / "customer.json"
    credential.write_text('{"type":"service_account"}\n')
    monkeypatch.setenv("ALK_HOSTED_EXECUTION", "1")
    monkeypatch.setenv(
        "ALK_RUNTIME_VALUE_GOOGLE_APPLICATION_CREDENTIALS", str(credential)
    )
    monkeypatch.setenv(
        "ALK_RUNTIME_SECRET_FILE_NAMES", "GOOGLE_APPLICATION_CREDENTIALS"
    )

    mounts = provisioning._runtime_credential_mounts(
        {"GOOGLE_APPLICATION_CREDENTIALS": "/etc/vertex/creds.json"},
        {"volumes": [{"source": "/dev/null", "target": "/etc/vertex/creds.json"}]},
    )

    assert mounts == [
        (
            "GOOGLE_APPLICATION_CREDENTIALS",
            credential.resolve(),
            f"/run/harness-secrets/{credential.name}",
        )
    ]


def test_managed_runtime_uses_generated_private_endpoint_over_submitted_default(
    tmp_path: Path, monkeypatch
) -> None:
    environment = provisioning.ProvisionedEnvironment(
        source=str(tmp_path),
        compose_file=str(tmp_path / "compose.json"),
        project="private-endpoint",
        managed=True,
        running=True,
        runtime_services=["agent-runtime"],
        internal_overrides={"TOOLS_API_URL": "http://tools-api:8080"},
        runtime_configuration_names=["TOOLS_API_URL"],
    )
    environment.save(tmp_path)
    monkeypatch.setenv("HARNESS_RUNTIME_STABLE_SECONDS", "0")
    monkeypatch.setenv("ALK_RUNTIME_CONFIGURATION_NAMES", "TOOLS_API_URL")
    monkeypatch.setenv("ALK_RUNTIME_VALUE_TOOLS_API_URL", "http://harness:8787")
    monkeypatch.setattr(
        provisioning,
        "_config",
        lambda _environment: {
            "services": {
                "agent-runtime": {
                    "environment": {"TOOLS_API_URL": "http://harness:8787"}
                }
            }
        },
    )
    seen: dict[str, object] = {}

    def run(_environment, *arguments, **kwargs):
        seen["arguments"] = arguments
        seen["injected"] = kwargs.get("process_overrides") or {}
        return ""

    monkeypatch.setattr(provisioning, "_run", run)
    monkeypatch.setattr(
        provisioning,
        "_docker",
        lambda *arguments, **_kwargs: (
            "running" if arguments and arguments[0] == "inspect" else ""
        ),
    )

    provisioning.start_runtime(tmp_path)

    assert seen["injected"]["TOOLS_API_URL"] == "http://tools-api:8080"
    arguments = seen["arguments"]
    assert "TOOLS_API_URL=http://tools-api:8080" in arguments



def test_compose_process_overrides_uploaded_runtime_endpoint(tmp_path: Path, monkeypatch) -> None:
    """Rewritten for the env file: Compose sees the override, and it is in the file."""
    environment = provisioning.ProvisionedEnvironment(
        source=str(tmp_path),
        compose_file=str(tmp_path / "compose.json"),
        project="compose-precedence",
        runtime_configuration_names=["TOOLS_API_URL"],
    )
    monkeypatch.setenv("ALK_RUNTIME_CONFIGURATION_NAMES", "TOOLS_API_URL")
    monkeypatch.setenv("ALK_RUNTIME_VALUE_TOOLS_API_URL", "http://harness:8787")
    calls = _record_compose(monkeypatch)

    provisioning._run(
        environment,
        "run",
        process_overrides={"TOOLS_API_URL": "http://tools-api:8080"},
    )

    assert _seen(calls[0])["TOOLS_API_URL"] == "http://tools-api:8080"
    assert calls[0].job.values["TOOLS_API_URL"] == "http://tools-api:8080"
    assert "TOOLS_API_URL" not in calls[0].env


def test_generated_runtime_bundle_is_portable_and_contains_no_host_context(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "agent"
    session = tmp_path / "session"
    source.mkdir()
    source.joinpath("requirements.txt").write_text("")
    source.joinpath("agent.py").write_text("print('ready')\n")

    def run(_environment, *arguments, **_kwargs):
        if "config" in arguments and "--format" in arguments:
            return json.dumps(
                {"services": {"agent-runtime": {"profiles": ["harness-runtime"]}}}
            )
        return ""

    monkeypatch.setattr(provisioning, "_run", run)
    provisioning.provision(source, session, _contract())
    root, bundle = export_session_bundle(source, session, name="unpackaged-agent")

    compose = json.loads(root.joinpath("compose.json").read_text())
    build = compose["services"]["agent-runtime"]["build"]
    assert build["context"] == "services/generated-runtime"
    assert build["dockerfile"] == GENERATED_DOCKERFILE
    assert root.joinpath("services/generated-runtime/agent.py").is_file()
    plan = json.loads(root.joinpath("generated-runtime.json").read_text())
    assert plan["context_directory"] == "services/generated-runtime"
    assert str(tmp_path) not in root.joinpath("compose.json").read_text()
    assert bundle.runtime.document == "compose.json"


def _wait_for_log(environment, expected: str, timeout: float = 30) -> str:
    deadline = time.monotonic() + timeout
    output = ""
    while time.monotonic() < deadline:
        output = provisioning._docker(
            "logs", environment.runtime_container, check=False, timeout=10
        )
        if expected in output:
            return output
        time.sleep(0.25)
    raise AssertionError(f"runtime did not emit {expected!r}:\n{output}")


@pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="requires Docker")
def test_real_unpackaged_python_agent_gets_redis_and_clean_reset(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HARNESS_RUNTIME_STABLE_SECONDS", "1")
    source = tmp_path / "python-agent"
    session = tmp_path / "python-session"
    source.mkdir()
    source.joinpath("requirements.txt").write_text("")
    source.joinpath("agent.py").write_text(
        """import os
import socket
import time
from urllib.parse import urlsplit

target = urlsplit(os.environ["REDIS_URL"])
sock = socket.create_connection((target.hostname, target.port), timeout=5)
sock.sendall(b"*2\\r\\n$3\\r\\nGET\\r\\n$9\\r\\nalk-proof\\r\\n")
before = sock.recv(1024)
sock.sendall(b"*3\\r\\n$3\\r\\nSET\\r\\n$9\\r\\nalk-proof\\r\\n$5\\r\\nready\\r\\n")
after = sock.recv(1024)
print(f"AGENT_READY before={before!r} set={after!r}", flush=True)
while True:
    time.sleep(60)
"""
    )
    contract = _contract()
    contract.dependencies = [Dependency(name="session-cache", engine="redis")]
    environment = provisioning.provision(source, session, contract)
    try:
        assert environment.services == ["redis"]
        assert environment.generated_runtime_plan
        bundle_root, bundle = export_session_bundle(
            source, session, name="unpackaged-python-redis"
        )
        assert bundle.runtime.document == "compose.json"
        assert bundle_root.joinpath(
            "services/generated-runtime/.alk-generated.Dockerfile"
        ).is_file()
        first = start_runtime(session)
        assert "before=b'$-1\\r\\n'" in _wait_for_log(first, "AGENT_READY")
        stop_runtime(session)
        reset(session)
        second = start_runtime(session)
        assert "before=b'$-1\\r\\n'" in _wait_for_log(second, "AGENT_READY")
    finally:
        stop_runtime(session)
        stop(session)


@pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="requires Docker")
def test_real_unpackaged_node_agent_builds_and_starts(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HARNESS_RUNTIME_STABLE_SECONDS", "1")
    source = tmp_path / "node-agent"
    session = tmp_path / "node-session"
    source.mkdir()
    source.joinpath("package.json").write_text(
        json.dumps(
            {
                "name": "unpackaged-node-agent",
                "version": "1.0.0",
                "scripts": {"start": "node agent.js"},
            }
        )
    )
    source.joinpath("package-lock.json").write_text(
        json.dumps(
            {
                "name": "unpackaged-node-agent",
                "version": "1.0.0",
                "lockfileVersion": 3,
                "requires": True,
                "packages": {"": {"name": "unpackaged-node-agent", "version": "1.0.0"}},
            }
        )
    )
    source.joinpath("agent.js").write_text(
        "console.log('AGENT_READY node=1'); setInterval(() => {}, 60000);\n"
    )
    environment = provisioning.provision(
        source, session, _contract(language="node", version="22")
    )
    try:
        assert environment.services == []
        bundle_root, bundle = export_session_bundle(
            source, session, name="unpackaged-node"
        )
        assert bundle.runtime.document == "compose.json"
        assert bundle_root.joinpath("services/generated-runtime/agent.js").is_file()
        running = start_runtime(session)
        assert "AGENT_READY node=1" in _wait_for_log(running, "AGENT_READY node=1")
    finally:
        stop_runtime(session)
        stop(session)

@pytest.mark.parametrize("version", ["3.11.9", "not-a-version", "<3.12"])
def test_generated_python_rejects_incompatible_version(tmp_path: Path, version: str) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='agent'\nversion='1'\nrequires-python='>=3.12'\n"
    )
    (tmp_path / "agent.py").write_text("print('agent')\n")
    with pytest.raises(GeneratedRuntimeError, match="generated_runtime_python_version_unsupported"):
        detect_generated_runtime(tmp_path, Runtime(language="python", version=version))


def test_generated_python_range_intersects_project_requirement(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='agent'\nversion='1'\nrequires-python='>=3.13'\n"
    )
    (tmp_path / "agent.py").write_text("print('agent')\n")
    plan = detect_generated_runtime(tmp_path, Runtime(language="python", version=">=3.12"))
    assert "FROM python:3.13-slim" in plan.dockerfile


def test_generated_python_minor_image_allows_source_patch_floor(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='agent'\nversion='1'\nrequires-python='>=3.12.1,<3.13'\n"
    )
    (tmp_path / "agent.py").write_text("print('agent')\n")
    plan = detect_generated_runtime(tmp_path, Runtime(language="python", version="3.12"))
    assert "FROM python:3.12-slim" in plan.dockerfile


@pytest.mark.parametrize("excluded", [False, True])
def test_workspace_root_does_not_promote_unrelated_or_excluded_member(
    tmp_path, excluded
):
    from fi.alk.harness.generated_runtime import python_workspace_root

    member = tmp_path / "server"
    member.mkdir()
    (member / "pyproject.toml").write_text('[project]\nname="server"\nversion="1"\n')
    (tmp_path / "pyproject.toml").write_text(
        '[tool.uv.workspace]\nmembers=["server"]\nexclude=["server"]\n'
        if excluded
        else '[project]\nname="unrelated"\nversion="1"\n'
    )
    assert python_workspace_root(tmp_path, member) == member


@pytest.mark.parametrize("self_member", [".", "./", ".//./"])
def test_workspace_self_member_preserves_children_and_unique_extra_owner(
    tmp_path: Path, self_member: str
) -> None:
    from fi.alk.harness.generated_runtime import (
        python_workspace_members,
        python_workspace_root,
        select_python_extras,
    )

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="root"\nversion="1"\n'
        '[project.optional-dependencies]\nchat=["httpx"]\n'
        f'[tool.uv.workspace]\nmembers=["{self_member}", "packages/*"]\n'
        'exclude=["packages/excluded"]\n'
    )
    member = tmp_path / "packages" / "server"
    member.mkdir(parents=True)
    (tmp_path / "packages" / "excluded").mkdir()

    assert python_workspace_members(tmp_path) == {member.resolve()}
    assert python_workspace_root(tmp_path, member) == tmp_path.resolve()
    selection = select_python_extras(tmp_path, ["chat"], required_owner=tmp_path)
    assert selection.names == ("chat",)
    assert not selection.all_packages


# ---------------------------------------------------------------------------------------------
# TH-9550 A-M3: Compose sees only what the job may use.
#
# "Present" below means present in what Compose sees: ``{**job_file, **process_env}``, with the
# job env file parsed from argv inside the patched ``subprocess.run`` (the file is deleted
# after the call).
# ---------------------------------------------------------------------------------------------

_CLIENT_SETTINGS = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/home/worker",
    "TMPDIR": "/tmp/worker",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "DOCKER_HOST": "tcp://base-docker:2375",
    "SSL_CERT_FILE": "/etc/ssl/base.pem",
    "SSL_CERT_DIR": "/etc/ssl/base-certs",
}
_CONTROLLER_CANARIES = {
    "HARNESS_PLATFORM_API_KEY": "canary-platform",
    "AGENTCC_API_KEY": "canary-agentcc",
    "FI_API_KEY": "canary-fi",
    "CARTESIA_API_KEY": "canary-cartesia",
    "DOCKER_CONFIG": "/canary/docker-config",
    "SOME_SHELL_NAME": "canary-shell",
}
_COMPOSE_LOGGER = "fi.alk.harness.provision"
_COMPOSE_PIN = {
    "COMPOSE_EXPERIMENTAL_GIT_REMOTE": "0",
    "COMPOSE_EXPERIMENTAL_OCI_REMOTE": "0",
}


def _unescape_env_value(text: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\":
            following = text[index + 1]
            out.append("\n" if following == "n" else following)
            index += 2
        else:
            out.append(char)
            index += 1
    return "".join(out)


def _parse_env_file(text: str) -> dict[str, str]:
    """A small reader of the escapes the job file uses: ``\\\\``, ``\\"``, ``\\$`` and ``\\n``."""
    values: dict[str, str] = {}
    for line in text.split("\n"):
        if not line:
            continue
        name, separator, rest = line.partition("=")
        assert separator and rest.startswith('"') and rest.endswith('"'), line
        values[name] = _unescape_env_value(rest[1:-1])
    return values


def _record_compose(monkeypatch, *, raises=None, returncode=0, output=""):
    """Patch ``subprocess.run``; for each call keep argv, env and the parsed job env file."""
    calls: list[types.SimpleNamespace] = []

    def run(command, **kwargs):
        env_files = [
            command[index + 1]
            for index, argument in enumerate(command)
            if argument == "--env-file"
        ]
        job = None
        if env_files:
            path = Path(env_files[-1])
            job = types.SimpleNamespace(
                path=path,
                mode=stat.S_IMODE(path.stat().st_mode),
                directory_mode=stat.S_IMODE(path.parent.stat().st_mode),
                values=_parse_env_file(path.read_text(encoding="utf-8")),
            )
        # Override files and the service env files they name, read while Compose would read them.
        overrides: dict[str, Any] = {}
        service_env_files: dict[str, types.SimpleNamespace] = {}
        for index, argument in enumerate(command[:-1]):
            if argument != "--file" or not Path(command[index + 1]).is_file():
                continue
            text = Path(command[index + 1]).read_text(encoding="utf-8")
            try:
                document = json.loads(text)
            except ValueError:
                continue
            overrides[command[index + 1]] = document
            for service in (document.get("services") or {}).values():
                for named in (service or {}).get("env_file") or []:
                    path = Path(named)
                    service_env_files[named] = types.SimpleNamespace(
                        mode=stat.S_IMODE(path.stat().st_mode),
                        values=_parse_env_file(path.read_text(encoding="utf-8")),
                    )
        call = types.SimpleNamespace(
            argv=list(command),
            env=dict(kwargs["env"]),
            env_files=env_files,
            overrides=overrides,
            service_env_files=service_env_files,
            job=job,
            cwd=kwargs.get("cwd"),
            cwd_listing=(
                sorted(entry.name for entry in Path(kwargs["cwd"]).iterdir())
                if kwargs.get("cwd") and Path(kwargs["cwd"]).is_dir()
                else None
            ),
        )
        calls.append(call)
        if raises is not None:
            raise raises
        return types.SimpleNamespace(returncode=returncode, stdout=output, stderr="")

    monkeypatch.setattr(provisioning.subprocess, "run", run)
    return calls


def _seen(call) -> dict[str, str]:
    """What Compose sees: the job env file under the process environment."""
    return {**(call.job.values if call.job else {}), **call.env}


def _shell(monkeypatch, shell: dict[str, str], manifest: str | None) -> None:
    """Make ``os.environ`` exactly ``shell`` (plus the manifest when given)."""
    for name in list(os.environ):
        if name != "PYTEST_CURRENT_TEST":
            monkeypatch.delenv(name, raising=False)
    for name, value in shell.items():
        monkeypatch.setenv(name, value)
    if manifest is not None:
        monkeypatch.setenv("ALK_RUNTIME_CONFIGURATION_NAMES", manifest)


def _compose_environment(tmp_path: Path, **fields) -> provisioning.ProvisionedEnvironment:
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    values = {
        "source": str(source),
        "compose_file": str(source / "compose.yaml"),
        "project": "compose-env",
    }
    values.update(fields)
    return provisioning.ProvisionedEnvironment(**values)


@pytest.mark.parametrize("manifest", ["", "AGENT_SETTING"])
def test_manifest_compose_environment_is_the_client_settings_only(
    tmp_path: Path, monkeypatch, manifest: str
) -> None:
    shell = {**_CLIENT_SETTINGS, **_CONTROLLER_CANARIES, "LANG": "C.UTF-8"}
    _shell(monkeypatch, shell, manifest)
    monkeypatch.delenv("LC_ALL")
    calls = _record_compose(monkeypatch)

    provisioning._run(_compose_environment(tmp_path), "config")

    seen = _seen(calls[0])
    for name in _CONTROLLER_CANARIES:
        assert name not in seen, name
    assert "ALK_RUNTIME_CONFIGURATION_NAMES" not in seen
    for name, value in _CLIENT_SETTINGS.items():
        if name == "LC_ALL":
            assert name not in seen
        else:
            assert seen[name] == value
    assert set(calls[0].env) == set(_CLIENT_SETTINGS) - {"LC_ALL"} | set(_COMPOSE_PIN)


@pytest.mark.parametrize("manifest", [True, False])
@pytest.mark.parametrize("command", ["config", "up", "build", "run"])
def test_ports_runtime_values_and_overrides_reach_compose_on_both_branches(
    tmp_path: Path, monkeypatch, manifest: bool, command: str
) -> None:
    _shell(monkeypatch, {"PATH": "/usr/bin"}, "SETTING_A,SETTING_B" if manifest else None)
    if manifest:
        monkeypatch.setenv("ALK_RUNTIME_VALUE_SETTING_A", "job-a")
        monkeypatch.setenv("ALK_RUNTIME_VALUE_SETTING_B", "job-b")
    else:
        monkeypatch.setenv("SETTING_A", "job-a")
        monkeypatch.setenv("SETTING_B", "job-b")
    calls = _record_compose(monkeypatch)
    environment = _compose_environment(
        tmp_path,
        port_variables={"TOOLS_PORT": "18090"},
        runtime_configuration_names=["SETTING_A", "SETTING_B"],
    )

    provisioning._run(
        environment, command, process_overrides={"SETTING_B": "override-b", "EXTRA": "x"}
    )

    seen = _seen(calls[0])
    assert seen["TOOLS_PORT"] == "18090"
    assert seen["SETTING_A"] == "job-a"
    assert seen["SETTING_B"] == "override-b"
    assert seen["EXTRA"] == "x"
    assert command in calls[0].argv


def test_manifest_runtime_name_that_is_a_controller_name_has_no_bare_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    _shell(
        monkeypatch,
        {"PATH": "/usr/bin", "GOOGLE_APPLICATION_CREDENTIALS": "/controller/adc.json"},
        "GOOGLE_APPLICATION_CREDENTIALS",
    )
    environment = _compose_environment(
        tmp_path, runtime_configuration_names=["GOOGLE_APPLICATION_CREDENTIALS"]
    )
    calls = _record_compose(monkeypatch)

    provisioning._run(environment, "config")
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in _seen(calls[0])

    monkeypatch.setenv("ALK_RUNTIME_VALUE_GOOGLE_APPLICATION_CREDENTIALS", "/job/adc.json")
    provisioning._run(environment, "config")
    assert _seen(calls[1])["GOOGLE_APPLICATION_CREDENTIALS"] == "/job/adc.json"
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in calls[1].env


def test_bare_cli_passes_the_shell_minus_the_denied_names(
    tmp_path: Path, monkeypatch
) -> None:
    denied = {
        "FI_API_KEY": "d1",
        "FI_SECRET_KEY": "d2",
        "AGENTCC_BASE_URL": "d3",
        "ALK_SANDBOX_TOKEN": "d4",
        "HARNESS_WEBHOOK_URL": "d5",
    }
    kept = {"OPENAI_API_KEY": "own-openai", "DOCKER_CONTEXT": "colima", "PATH": "/usr/bin"}
    _shell(monkeypatch, {**denied, **kept}, None)
    calls = _record_compose(monkeypatch)

    provisioning._run(_compose_environment(tmp_path), "config")

    seen = _seen(calls[0])
    for name in denied:
        assert name not in seen, name
    for name, value in kept.items():
        assert seen[name] == value


def test_bare_cli_denied_runtime_name_never_reaches_compose_or_the_run_arguments(
    tmp_path: Path, monkeypatch
) -> None:
    _shell(
        monkeypatch,
        {"PATH": "/usr/bin", "AGENTCC_API_KEY": "canary-agentcc", "OPENAI_API_KEY": "own"},
        None,
    )
    environment = _compose_environment(
        tmp_path,
        runtime_services=["agent-runtime"],
        running=True,
        runtime_configuration_names=["AGENTCC_API_KEY", "OPENAI_API_KEY"],
    )
    environment.save(tmp_path)
    calls = _drive_start_runtime(tmp_path, monkeypatch)

    run_call = next(call for call in calls if "run" in call.argv)
    assert "AGENTCC_API_KEY" not in _seen(run_call)
    assert "AGENTCC_API_KEY" not in run_call.argv
    assert _seen(run_call)["OPENAI_API_KEY"] == "own"
    assert "OPENAI_API_KEY" in run_call.argv


def _drive_start_runtime(destination: Path, monkeypatch, **kwargs) -> list:
    """Run ``start_runtime`` with only ``subprocess.run``, ``_config`` and ``_docker`` patched."""
    monkeypatch.setenv("HARNESS_RUNTIME_STABLE_SECONDS", "0")
    calls = _record_compose(monkeypatch)
    monkeypatch.setattr(
        provisioning,
        "_config",
        lambda _environment: {"services": {"agent-runtime": {"environment": {}}}},
    )
    monkeypatch.setattr(
        provisioning,
        "_docker",
        lambda *arguments, **_kwargs: (
            "running" if arguments and arguments[0] == "inspect" else ""
        ),
    )
    provisioning.start_runtime(destination, **kwargs)
    return calls


_AGENT_SETTINGS = {
    "HTTPS_PROXY": "http://proxy.agent.invalid:3128",
    "LANG": "de_DE.UTF-8",
    "SSL_CERT_FILE": "/agent/ca.pem",
}


def _agent_settings_environment(tmp_path: Path, monkeypatch, *, managed: bool):
    names = [*_AGENT_SETTINGS, "MY_KEY"]
    _shell(monkeypatch, {"PATH": "/usr/bin"}, ",".join(names))
    for name, value in {**_AGENT_SETTINGS, "MY_KEY": "own"}.items():
        monkeypatch.setenv(f"ALK_RUNTIME_VALUE_{name}", value)
    environment = _compose_environment(
        tmp_path,
        runtime_services=["agent-runtime"],
        running=True,
        managed=managed,
        runtime_configuration_names=names,
    )
    environment.save(tmp_path)
    return environment


def test_a_kit_written_runtime_gives_the_agent_its_proxy_ca_and_locale_settings(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    # Compose reads these names for itself, so they never go through its environment or the
    # job's env file. Where the kit wrote the service file, they go to the agent's own service
    # as its env_file, which reaches that container and nothing else.
    _agent_settings_environment(tmp_path, monkeypatch, managed=True)

    with caplog.at_level("WARNING"):
        calls = _drive_start_runtime(tmp_path, monkeypatch)

    # Delivered, so not reported as skipped.
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert not any(name in logged for name in _AGENT_SETTINGS), logged
    run_call = next(call for call in calls if "run" in call.argv)
    [override] = run_call.overrides.values()
    [(path, delivered)] = run_call.service_env_files.items()
    # Only the agent's service gets the file, never a helper service in the same project.
    assert override == {"services": {"agent-runtime": {"env_file": [path]}}}
    assert delivered.values == _AGENT_SETTINGS
    assert delivered.mode == 0o600
    for name in _AGENT_SETTINGS:
        assert name not in _seen(run_call), name
        assert name not in run_call.argv, name
    assert _seen(run_call)["MY_KEY"] == "own"
    # Only the run that starts the agent carries it.
    assert all(not call.overrides for call in calls if call is not run_call)
    # The values are gone once the agent is started.
    assert not Path(path).exists()
    assert not Path(path).parent.exists()


def test_compose_config_never_gets_the_agents_settings(tmp_path: Path, monkeypatch) -> None:
    # `config` prints the rendered file, values included, so the override that names the
    # settings file must reach the run that starts the agent and never a `config` call. The
    # kit's own `config` runs here for real, through the recorded Compose.
    _agent_settings_environment(tmp_path, monkeypatch, managed=True)
    monkeypatch.setenv("HARNESS_RUNTIME_STABLE_SECONDS", "0")
    calls = _record_compose(
        monkeypatch,
        output=json.dumps({"services": {"agent-runtime": {"environment": {}}}}),
    )
    monkeypatch.setattr(
        provisioning,
        "_docker",
        lambda *arguments, **_kwargs: (
            "running" if arguments and arguments[0] == "inspect" else ""
        ),
    )

    provisioning.start_runtime(tmp_path)

    config_calls = [call for call in calls if "config" in call.argv]
    assert config_calls, "the kit's own config call was not made"
    for call in config_calls:
        assert not call.overrides, call.argv
        assert not call.service_env_files, call.argv
        for value in _AGENT_SETTINGS.values():
            assert value not in " ".join(call.argv), call.argv
    # The run that starts the agent does carry it, so the check above can see an override.
    run_call = next(call for call in calls if "run" in call.argv)
    assert run_call.service_env_files


def test_a_customers_own_compose_file_still_skips_the_settings_compose_reads(
    tmp_path: Path, monkeypatch
) -> None:
    # Only the customer knows which of their services a proxy or locale is for.
    environment = _agent_settings_environment(tmp_path, monkeypatch, managed=False)

    calls = _drive_start_runtime(tmp_path, monkeypatch)

    run_call = next(call for call in calls if "run" in call.argv)
    assert run_call.overrides == {}
    for name in _AGENT_SETTINGS:
        assert name not in _seen(run_call), name
    assert provisioning.skipped_setting_names(environment) == sorted(_AGENT_SETTINGS)


def test_a_kit_written_runtime_reports_no_skipped_settings(
    tmp_path: Path, monkeypatch
) -> None:
    environment = _agent_settings_environment(tmp_path, monkeypatch, managed=True)

    assert provisioning.skipped_setting_names(environment) == []


def test_a_settings_file_a_killed_worker_left_is_removed_by_stop_s_sweep(
    tmp_path: Path, monkeypatch
) -> None:
    # A worker killed after writing the file never reaches its own cleanup; ``stop`` sweeps the
    # project's private job directories, and this one is among them.
    environment = _agent_settings_environment(tmp_path, monkeypatch, managed=True)
    override = provisioning._agent_settings_override(environment, "agent-runtime")
    assert override is not None and override.parent.exists()

    provisioning._remove_leftover_job_directories(environment.project)

    assert not override.parent.exists()


def test_a_setting_with_dollars_and_quotes_is_written_to_arrive_as_given(
    tmp_path: Path, monkeypatch
) -> None:
    # Compose must not substitute inside the value: it reaches the agent exactly as given.
    _agent_settings_environment(tmp_path, monkeypatch, managed=True)
    given = 'http://u:pa$$w"rd@proxy.invalid:3128/$HOME/${PATH}\\x'
    monkeypatch.setenv("ALK_RUNTIME_VALUE_HTTPS_PROXY", given)
    environment = provisioning.ProvisionedEnvironment.load(tmp_path)
    override = provisioning._agent_settings_override(environment, "agent-runtime")
    try:
        settings = json.loads(override.read_text())["services"]["agent-runtime"]["env_file"][0]
        assert _parse_env_file(Path(settings).read_text())["HTTPS_PROXY"] == given
    finally:
        provisioning._remove_job_directory(override.parent)


def test_the_agents_settings_file_is_removed_when_the_start_fails(
    tmp_path: Path, monkeypatch
) -> None:
    _agent_settings_environment(tmp_path, monkeypatch, managed=True)
    monkeypatch.setenv("HARNESS_RUNTIME_STABLE_SECONDS", "0")
    calls = _record_compose(monkeypatch, returncode=1, output="boom")
    monkeypatch.setattr(
        provisioning,
        "_config",
        lambda _environment: {"services": {"agent-runtime": {"environment": {}}}},
    )
    monkeypatch.setattr(provisioning, "_docker", lambda *arguments, **_kwargs: "")

    with pytest.raises(provisioning.ProvisionError):
        provisioning.start_runtime(tmp_path)

    run_call = next(call for call in calls if "run" in call.argv)
    [path] = run_call.service_env_files
    assert not Path(path).exists()
    assert not Path(path).parent.exists()


_RESERVED_NAMES = [
    "DOCKER_CONFIG",
    "DOCKER_HOST",
    "COMPOSE_FILE",
    "LD_PRELOAD",
    "https_proxy",
    "SSL_CERT_FILE",
    "GIT_SSH_COMMAND",
    "GCONV_PATH",
    "BASH_ENV",
    "SSH_ASKPASS",
    "XDG_CONFIG_HOME",
]


@pytest.mark.parametrize("manifest", [True, False])
def test_reserved_client_names_never_set_compose_or_the_run_arguments(
    tmp_path: Path, monkeypatch, caplog, manifest: bool
) -> None:
    runtime_names = (
        [
            # The skip is the backstop for names intake refuses; a name intake admits (a proxy,
            # a CA bundle) fails the job by name instead (test_an_admitted_value_...).
            *[name for name in _RESERVED_NAMES if agent_value_name_reserved(name)],
            "PROXY_PORT",
            "HARNESS_POSTGRES_PORT",
            "PYTHONPATH",
            "NODE_OPTIONS",
            "GOOGLE_CLOUD_PROJECT",
            "NODE_ENV",
            "PYTHONUNBUFFERED",
        ]
        if manifest
        else [
            "PROXY_PORT",
            "GOOGLE_CLOUD_PROJECT",
            "NODE_ENV",
            "PYTHONUNBUFFERED",
        ]
    )
    _shell(
        monkeypatch,
        {"PATH": "/usr/bin", "HOME": "/home/worker", "DOCKER_HOST": "tcp://base:2375"},
        ",".join(runtime_names) if manifest else None,
    )
    for name in runtime_names:
        monkeypatch.setenv(
            f"ALK_RUNTIME_VALUE_{name}" if manifest else name, f"/evil/{name}"
        )
    environment = _compose_environment(
        tmp_path,
        runtime_services=["agent-runtime"],
        running=True,
        runtime_configuration_names=runtime_names,
        port_variables={
            **{name: f"/evil-port/{name}" for name in _RESERVED_NAMES},
        },
        internal_overrides={"DOCKER_HOST": "tcp://evil:1", "HOME": "/evil/home"},
    )
    environment.save(tmp_path)
    caplog.set_level(logging.DEBUG, logger=_COMPOSE_LOGGER)

    calls = _drive_start_runtime(
        tmp_path, monkeypatch, overrides={name: f"/evil-override/{name}" for name in _RESERVED_NAMES}
    )

    run_call = next(call for call in calls if "run" in call.argv)
    seen = _seen(run_call)
    base = {"PATH": "/usr/bin", "HOME": "/home/worker", "DOCKER_HOST": "tcp://base:2375"}
    for name in [*_RESERVED_NAMES, "PYTHONPATH", "NODE_OPTIONS"]:
        assert name not in run_call.job.values, name
        assert seen.get(name) == base.get(name), name
    # No port_variables for these here, so the job's own values are kept.
    # (``HARNESS_*`` runtime names are denied on the bare CLI, so that one is checked on the
    # manifest branch only.)
    kept = ["PROXY_PORT", "GOOGLE_CLOUD_PROJECT", "NODE_ENV", "PYTHONUNBUFFERED"]
    if manifest:
        kept.append("HARNESS_POSTGRES_PORT")
    for name in kept:
        assert run_call.job.values[name] == f"/evil/{name}"
    env_names = {
        argument.split("=", 1)[0]
        for index, argument in enumerate(run_call.argv)
        if index and run_call.argv[index - 1] == "--env"
    }
    assert env_names
    assert not [name for name in env_names if client_name_reserved(name)]
    assert not [name for name in run_call.argv if name.startswith("/evil")]
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for name in ("DOCKER_HOST", "SSH_ASKPASS", "XDG_CONFIG_HOME", "GIT_SSH_COMMAND"):
        assert name in logged
    assert "/evil" not in logged


def _stop_environment(tmp_path: Path, **fields):
    environment = _compose_environment(
        tmp_path, runtime_configuration_names=["AGENT_SETTING"], running=True, **fields
    )
    environment.save(tmp_path)
    return environment


def test_stop_tears_down_by_project_with_the_manifest_client_settings_only(
    tmp_path: Path, monkeypatch
) -> None:
    _shell(
        monkeypatch,
        {**_CLIENT_SETTINGS, "AGENTCC_API_KEY": "canary", "SOME_SHELL_NAME": "canary"},
        "AGENT_SETTING",
    )
    monkeypatch.setenv("ALK_RUNTIME_VALUE_AGENT_SETTING", "job-value")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "from-shell")
    _stop_environment(tmp_path)
    monkeypatch.setattr(provisioning, "_docker", lambda *_a, **_k: "")
    calls = _record_compose(monkeypatch)

    assert stop(tmp_path)

    call = next(call for call in calls if "down" in call.argv)
    assert "--file" not in call.argv
    assert "--env-file" not in call.argv
    assert call.job is None
    assert call.argv[:4] == ["docker", "compose", "--project-name", "compose-env"]
    assert call.env == {**_CLIENT_SETTINGS, **_COMPOSE_PIN}


def test_stop_without_the_manifest_keeps_the_shell_minus_denied_and_compose_names(
    tmp_path: Path, monkeypatch
) -> None:
    _shell(
        monkeypatch,
        {
            "PATH": "/usr/bin",
            "DOCKER_CONTEXT": "colima",
            "DOCKER_CERT_PATH": "/certs",
            "AGENTCC_API_KEY": "canary",
            "COMPOSE_FILE": "/elsewhere.yaml",
            "AGENT_SETTING": "job-value",
        },
        None,
    )
    _stop_environment(tmp_path)
    monkeypatch.setattr(provisioning, "_docker", lambda *_a, **_k: "")
    calls = _record_compose(monkeypatch)

    assert stop(tmp_path)

    call = next(call for call in calls if "down" in call.argv)
    assert "--file" not in call.argv and "--env-file" not in call.argv
    assert call.env["DOCKER_CONTEXT"] == "colima"
    assert call.env["DOCKER_CERT_PATH"] == "/certs"
    assert "AGENTCC_API_KEY" not in call.env
    assert "COMPOSE_FILE" not in call.env
    # The plan wins over the design page's wording: with no manifest the base is the shell minus
    # the denied names and ``COMPOSE_*``, so a plain shell name is kept (nothing is interpolated).
    assert call.env.get("AGENT_SETTING") == "job-value"


def test_sandbox_server_imports_exactly_three_names_from_provision() -> None:
    import ast

    path = Path(provisioning.__file__).with_name("sandbox_server.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "provision" and node.level == 1:
            imported.update(alias.name for alias in node.names)
    assert imported == {"ProvisionError", "source_fingerprint", "stop"}


def test_start_runtime_hands_a_declared_credential_to_compose_through_the_job_file(
    tmp_path: Path, monkeypatch
) -> None:
    _shell(monkeypatch, {"PATH": "/usr/bin"}, "CARTESIA_TOKEN")
    monkeypatch.setenv("ALK_RUNTIME_VALUE_CARTESIA_TOKEN", "job-token")
    environment = _compose_environment(
        tmp_path,
        runtime_services=["agent-runtime"],
        running=True,
        runtime_configuration_names=["CARTESIA_TOKEN"],
    )
    environment.save(tmp_path)

    calls = _drive_start_runtime(tmp_path, monkeypatch)

    run_call = next(call for call in calls if "run" in call.argv)
    assert run_call.job.values["CARTESIA_TOKEN"] == "job-token"
    assert "CARTESIA_TOKEN" not in run_call.env
    assert "CARTESIA_TOKEN" in run_call.argv
    assert "CARTESIA_TOKEN=job-token" not in run_call.argv
    assert "job-token" not in " ".join(run_call.argv)


def test_sandbox_job_plain_name_values_do_not_reach_compose(
    tmp_path: Path, monkeypatch
) -> None:
    from fi.alk.harness.secrets import worker_environment

    child = worker_environment(
        {"AGENT_SETTING": "plain-canary"},
        runtime_configuration={},
        host_environment={**_CLIENT_SETTINGS, "AGENTCC_API_KEY": "controller"},
    )
    assert child["AGENT_SETTING"] == "plain-canary"  # the plain name is in the worker
    _shell(monkeypatch, child, "")
    calls = _record_compose(monkeypatch)

    provisioning._run(_compose_environment(tmp_path), "config")

    seen = _seen(calls[0])
    assert "AGENT_SETTING" not in seen
    assert "AGENTCC_API_KEY" not in seen


_OWNED_AND_NEUTRAL = {
    "SSH_ASKPASS": "/job/askpass",
    "XDG_CONFIG_HOME": "/job/xdg",
    "GIT_ASKPASS": "/job/git-askpass",
    "JOB_SETTING": "neutral",
}


@pytest.mark.parametrize("manifest", [True, False])
def test_no_job_value_is_in_the_compose_process_environment(
    tmp_path: Path, monkeypatch, manifest: bool
) -> None:
    runtime = ["SSH_ASKPASS", "JOB_SETTING"]
    _shell(monkeypatch, {"PATH": "/usr/bin"}, ",".join(runtime) if manifest else None)
    # On the bare CLI a shell name that steers a client is the developer's own and stays in the
    # base; only the neutral name is set there, the reserved ones come as ports and overrides.
    for name in runtime if manifest else ["JOB_SETTING"]:
        monkeypatch.setenv(
            f"ALK_RUNTIME_VALUE_{name}" if manifest else name, _OWNED_AND_NEUTRAL[name]
        )
    calls = _record_compose(monkeypatch)
    environment = _compose_environment(
        tmp_path,
        runtime_configuration_names=runtime,
        port_variables={"XDG_CONFIG_HOME": "/job/xdg", "PORT_SETTING": "9"},
    )

    provisioning._run(
        environment,
        "up",
        process_overrides={
            "GIT_ASKPASS": "/job/git-askpass",
            "JOB_SETTING": "override-neutral",
            "OVERRIDE_SETTING": "o",
        },
    )

    call = calls[0]
    for name in (*_OWNED_AND_NEUTRAL, "PORT_SETTING", "OVERRIDE_SETTING"):
        assert name not in call.env, name
    assert call.job.values["JOB_SETTING"] == "override-neutral"
    assert call.job.values["PORT_SETTING"] == "9"
    assert call.job.values["OVERRIDE_SETTING"] == "o"
    for name in ("SSH_ASKPASS", "XDG_CONFIG_HOME", "GIT_ASKPASS"):
        assert name not in call.job.values, name


_PROXY_AND_EXPORT = {
    "HTTP_PROXY": "http://canary.invalid:3128",
    "HTTPS_PROXY": "http://canary.invalid:3128",
    "NO_PROXY": "",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://canary.invalid",
}


def test_a_jobs_proxy_and_export_settings_reach_only_the_env_file(
    tmp_path: Path, monkeypatch
) -> None:
    # Agent settings, so the job may set them, but in Compose's own environment they would send
    # its Docker API traffic through the job's proxy, or its telemetry to the job's endpoint.
    names = ["OTEL_EXPORTER_OTLP_ENDPOINT"]
    _shell(monkeypatch, {"PATH": "/usr/bin"}, ",".join(names))
    monkeypatch.setenv(
        "ALK_RUNTIME_VALUE_OTEL_EXPORTER_OTLP_ENDPOINT", _PROXY_AND_EXPORT["OTEL_EXPORTER_OTLP_ENDPOINT"]
    )
    calls = _record_compose(monkeypatch)
    environment = _compose_environment(tmp_path, runtime_configuration_names=names)

    provisioning._run(environment, "up")

    call = calls[0]
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in call.env
    assert call.job.values["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://canary.invalid"

    # A proxy reaches neither: it is skipped, and Compose still runs.
    proxies = ["HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"]
    _shell(monkeypatch, {"PATH": "/usr/bin"}, ",".join(proxies))
    for name in proxies:
        monkeypatch.setenv(f"ALK_RUNTIME_VALUE_{name}", _PROXY_AND_EXPORT[name] or "x")
    environment = _compose_environment(tmp_path, runtime_configuration_names=proxies)
    provisioning._run(environment, "up")
    assert len(calls) == 2
    for name in proxies:
        assert name not in calls[1].env, name
        assert name not in calls[1].job.values, name


def test_a_customers_tool_credential_reaches_the_env_file(tmp_path: Path, monkeypatch) -> None:
    # GIT_TOKEN, DOCKER_PASSWORD and SSH_PRIVATE_KEY are the agent's: git, ssh and docker don't
    # read them, so they go to the job's env file like any other agent setting.
    names = ["GIT_TOKEN", "DOCKER_PASSWORD", "SSH_PRIVATE_KEY", "DOCKER_CONFIG"]
    _shell(monkeypatch, {"PATH": "/usr/bin"}, ",".join(names))
    for name in names:
        monkeypatch.setenv(f"ALK_RUNTIME_VALUE_{name}", f"{name.lower()}-canary")
    calls = _record_compose(monkeypatch)
    environment = _compose_environment(tmp_path, runtime_configuration_names=names)

    provisioning._run(environment, "up")

    job = calls[0].job
    for name in ("GIT_TOKEN", "DOCKER_PASSWORD", "SSH_PRIVATE_KEY"):
        assert job.values[name] == f"{name.lower()}-canary", name
    # A name that steers docker stays out.
    assert "DOCKER_CONFIG" not in job.values


def test_an_admitted_value_compose_also_reads_is_skipped_and_named(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    # Intake admits a proxy, CA bundle or locale name as the agent's own setting, but Compose
    # reads the same names for itself. Until the value can go to the agent's own service
    # (a later change), it is skipped as before #337, and the skip is named: the job runs, it
    # does not fail (Dileep, #348).
    names = ["HTTPS_PROXY", "SSL_CERT_FILE", "LANG", "LC_ALL", "NO_PROXY", "REQUESTS_CA_BUNDLE", "MY_KEY"]
    _shell(monkeypatch, {"PATH": "/usr/bin"}, ",".join(names))
    for name in names:
        monkeypatch.setenv(f"ALK_RUNTIME_VALUE_{name}", f"{name.lower()}-canary")
    calls = _record_compose(monkeypatch)
    environment = _compose_environment(tmp_path, runtime_configuration_names=names)

    with caplog.at_level("WARNING"):
        provisioning._run(environment, "up")

    assert len(calls) == 1
    assert calls[0].job.values["MY_KEY"] == "my_key-canary"
    skipped = ["HTTPS_PROXY", "LANG", "LC_ALL", "NO_PROXY", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"]
    for name in skipped:
        assert name not in calls[0].job.values, name
        assert name not in calls[0].env, name
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert all(name in logged for name in skipped)
    assert "MY_KEY" not in logged
    assert "canary" not in logged


def test_an_environment_record_with_a_field_this_kit_does_not_know_still_loads(
    tmp_path: Path,
) -> None:
    # A newer kit may add fields to environment.json (a template rolled back reads one that a
    # newer kit wrote); this kit keeps the fields it knows and ignores the rest.
    environment = _compose_environment(tmp_path, runtime_configuration_names=["MY_KEY"])
    destination = tmp_path / "session"
    environment.save(destination)
    record = json.loads(destination.joinpath(provisioning.MANIFEST).read_text())
    record["a_field_from_a_newer_kit"] = ["anything"]
    destination.joinpath(provisioning.MANIFEST).write_text(json.dumps(record))

    loaded = provisioning.ProvisionedEnvironment.load(destination)

    assert loaded == environment


def test_provision_records_no_skipped_settings_for_a_kit_written_runtime(
    tmp_path: Path, monkeypatch
) -> None:
    # The kit wrote this service file, so the agent gets its proxy and locale through its own
    # service and nothing is reported as skipped (a customer's own file is the other test).
    source = tmp_path / "agent"
    session = tmp_path / "session"
    source.mkdir()
    source.joinpath("requirements.txt").write_text("")
    source.joinpath("agent.py").write_text("print('ready')\n")
    names = ["LANG", "MY_KEY", "NO_PROXY"]
    _shell(monkeypatch, {"PATH": "/usr/bin"}, ",".join(names))
    for name in names:
        monkeypatch.setenv(f"ALK_RUNTIME_VALUE_{name}", f"{name.lower()}-canary")
    monkeypatch.setattr(
        provisioning, "_contract_runtime_configuration_names", lambda _contract: names
    )

    def run(_environment, *arguments, **_kwargs):
        if "config" in arguments and "--format" in arguments:
            return json.dumps(
                {
                    "services": {
                        "agent-runtime": {
                            "profiles": ["harness-runtime"],
                            "build": {
                                "context": str(session / "generated-runtime-context"),
                                "dockerfile": GENERATED_DOCKERFILE,
                            },
                        }
                    }
                }
            )
        return ""

    monkeypatch.setattr(provisioning, "_run", run)
    environment = provisioning.provision(source, session, _contract())

    assert environment.managed
    assert environment.skipped_setting_names == []
    saved = json.loads(session.joinpath("environment.json").read_text())
    assert saved["skipped_setting_names"] == []
    assert "canary" not in session.joinpath("environment.json").read_text()

def _failing_unlink(monkeypatch, inside: str):
    """Make removing any file under a directory whose name holds ``inside`` fail."""
    real_unlink = os.unlink

    def unlink(path, *args, **kwargs):
        # rmtree removes by bare name relative to an open directory (dir_fd) where it can.
        if inside in str(path) or kwargs.get("dir_fd") is not None:
            raise PermissionError(13, "Permission denied")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", unlink)


def test_a_job_file_that_cannot_be_removed_is_logged_by_path_never_by_value(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    _shell(monkeypatch, {"PATH": "/usr/bin"}, "MY_KEY")
    monkeypatch.setenv("ALK_RUNTIME_VALUE_MY_KEY", "secret-canary-value")
    calls = _record_compose(monkeypatch)
    environment = _compose_environment(tmp_path, runtime_configuration_names=["MY_KEY"])
    _failing_unlink(monkeypatch, provisioning._job_directory_prefix(environment.project))
    caplog.set_level(logging.WARNING, logger=_COMPOSE_LOGGER)

    provisioning._run(environment, "ps")

    job_file = calls[0].job.path
    assert job_file.exists()
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert [w for w in warnings if str(job_file) in w and "PermissionError" in w], warnings
    assert "secret-canary-value" not in caplog.text
    monkeypatch.undo()
    job_file.unlink()


def test_a_leftover_job_directory_that_cannot_be_removed_is_logged(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    project = "alk-leftover-probe"
    leftover = Path(tempfile.mkdtemp(prefix=provisioning._job_directory_prefix(project)))
    (leftover / "job.env").write_text("MY_KEY=secret-canary-value\n", encoding="utf-8")
    _failing_unlink(monkeypatch, leftover.name)
    caplog.set_level(logging.WARNING, logger=_COMPOSE_LOGGER)

    try:
        provisioning._remove_leftover_job_directories(project)
        warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert [w for w in warnings if leftover.name in w and "PermissionError" in w], warnings
        assert "secret-canary-value" not in caplog.text
    finally:
        monkeypatch.undo()
        import shutil

        shutil.rmtree(leftover, ignore_errors=True)


_ROUND_TRIP_VALUES = {
    "PLAIN": "value",
    "DOLLAR": "cost $5 and $HOME",
    "BRACED": "${NAME:-fallback} and ${OTHER}",
    "QUOTES": "say \"hi\" and 'bye'",
    "BACKSLASH": "C:\\path\\to\\ \\n not a newline",
    "HASH": "a # not a comment",
    "SPACES": "inner  spaces",
    "EQUALS": "a=b=c",
    "NEWLINE": "line one\nline two",
    "UNICODE": "caf\u00e9 \u2603 \u65e5\u672c",
    "EMPTY_ISH": "x",
}


def test_job_file_is_private_outside_the_source_and_removed_after_the_call(
    tmp_path: Path, monkeypatch
) -> None:
    _shell(monkeypatch, {"PATH": "/usr/bin"}, ",".join(_ROUND_TRIP_VALUES))
    for name, value in _ROUND_TRIP_VALUES.items():
        monkeypatch.setenv(f"ALK_RUNTIME_VALUE_{name}", value)
    environment = _compose_environment(
        tmp_path,
        runtime_configuration_names=list(_ROUND_TRIP_VALUES),
    )
    world = tmp_path / "world"
    world.mkdir()
    calls = _record_compose(monkeypatch)

    # Runtime values are stripped (as before); an override keeps its edge whitespace exactly.
    edge = {"EDGE_SPACES": "  leading and trailing  ", "EDGE_NEWLINE": "line\n"}
    provisioning._run(environment, "up", process_overrides=edge)

    job = calls[0].job
    assert job.mode == 0o600
    assert job.directory_mode == 0o700
    assert not job.path.exists() and not job.path.parent.exists()
    for forbidden in (Path(environment.source), world, tmp_path):
        assert forbidden not in job.path.parents
    assert job.values == {**_ROUND_TRIP_VALUES, **edge}


def test_job_file_is_removed_after_a_timeout_and_after_a_failed_check(
    tmp_path: Path, monkeypatch
) -> None:
    _shell(monkeypatch, {"PATH": "/usr/bin"}, "SETTING")
    monkeypatch.setenv("ALK_RUNTIME_VALUE_SETTING", "v")
    environment = _compose_environment(tmp_path, runtime_configuration_names=["SETTING"])
    calls = _record_compose(
        monkeypatch, raises=subprocess.TimeoutExpired(cmd="docker", timeout=1)
    )

    with pytest.raises(provisioning.ProvisionError, match="timed out"):
        provisioning._run(environment, "up", timeout=1)

    assert calls[0].job.values == {"SETTING": "v"}
    assert not calls[0].job.path.parent.exists()

    failing = _record_compose(monkeypatch, returncode=1, output="boom")
    with pytest.raises(provisioning.ProvisionError, match="boom"):
        provisioning._run(environment, "up")
    assert not failing[0].job.path.parent.exists()


def test_job_file_is_removed_after_the_network_reclaim_retry(
    tmp_path: Path, monkeypatch
) -> None:
    _shell(monkeypatch, {"PATH": "/usr/bin"}, "SETTING")
    monkeypatch.setenv("ALK_RUNTIME_VALUE_SETTING", "v")
    environment = _compose_environment(tmp_path, runtime_configuration_names=["SETTING"])
    calls = _record_compose(
        monkeypatch,
        returncode=1,
        output="all predefined address pools have been fully subnetted",
    )
    monkeypatch.setattr(provisioning, "_remove_unused_harness_networks", lambda **_k: True)

    with pytest.raises(provisioning.ProvisionError):
        provisioning._run(environment, "up")

    assert len(calls) == 2
    assert calls[0].job.path == calls[1].job.path
    assert not calls[1].job.path.parent.exists()


def test_job_file_is_removed_when_writing_it_fails(tmp_path: Path, monkeypatch) -> None:
    _shell(monkeypatch, {"PATH": "/usr/bin"}, "")
    seen: list[Path] = []
    real_mkdtemp = provisioning.tempfile.mkdtemp

    def mkdtemp(*args, **kwargs):
        directory = real_mkdtemp(*args, **kwargs)
        seen.append(Path(directory))
        return directory

    def broken_write(_fd, _values):
        os.close(_fd)
        raise OSError("disk full")

    monkeypatch.setattr(provisioning.tempfile, "mkdtemp", mkdtemp)
    monkeypatch.setattr(provisioning, "_write_env_file", broken_write)
    _record_compose(monkeypatch)

    with pytest.raises(OSError):
        provisioning._run(_compose_environment(tmp_path), "up")

    assert seen and not seen[0].exists()


def test_env_file_arguments_follow_project_name_and_put_the_repository_env_first(
    tmp_path: Path, monkeypatch
) -> None:
    _shell(monkeypatch, {"PATH": "/usr/bin"}, "")
    environment = _compose_environment(tmp_path)
    calls = _record_compose(monkeypatch)

    provisioning._run(environment, "up", "--detach")
    assert len(calls[0].env_files) == 1
    assert calls[0].env_files[0] == str(calls[0].job.path)

    env_file = Path(environment.source) / ".env"
    env_file.write_text("FROM_DOTENV=1\n")
    provisioning._run(environment, "up", "--detach")

    argv = calls[1].argv
    assert calls[1].env_files == [str(env_file), str(calls[1].job.path)]
    project = argv.index("--project-name")
    assert argv[project + 2 : project + 6] == [
        "--env-file",
        str(env_file),
        "--env-file",
        str(calls[1].job.path),
    ]
    assert argv[project + 6 :] == ["up", "--detach"]

    env_file.unlink()
    env_file.mkdir()
    provisioning._run(environment, "up")
    assert len(calls[2].env_files) == 1  # a ``.env`` directory is not passed

    # ``stop`` passes no --env-file, even with a repository .env present.
    env_file.rmdir()
    env_file.write_text("FROM_DOTENV=1\n")
    environment.save(tmp_path)
    monkeypatch.setattr(provisioning, "_docker", lambda *_a, **_k: "")
    stop(tmp_path)
    down = next(call for call in calls if "down" in call.argv and "--volumes" in call.argv)
    assert "--env-file" not in down.argv


def test_alk_values_win_over_a_job_value_of_the_same_name_through_run(
    tmp_path: Path, monkeypatch
) -> None:
    _shell(monkeypatch, {"PATH": "/usr/bin"}, "HARNESS_POSTGRES_PORT,TOOLS_API_URL")
    monkeypatch.setenv("ALK_RUNTIME_VALUE_HARNESS_POSTGRES_PORT", "1111")
    monkeypatch.setenv("ALK_RUNTIME_VALUE_TOOLS_API_URL", "http://job:1")
    environment = _compose_environment(
        tmp_path,
        port_variables={"HARNESS_POSTGRES_PORT": "0"},
        runtime_configuration_names=["HARNESS_POSTGRES_PORT", "TOOLS_API_URL"],
    )
    calls = _record_compose(monkeypatch)

    provisioning._run(
        environment, "run", process_overrides={"TOOLS_API_URL": "http://tools-api:8080"}
    )

    assert calls[0].job.values["HARNESS_POSTGRES_PORT"] == "0"
    assert calls[0].job.values["TOOLS_API_URL"] == "http://tools-api:8080"


def test_alk_values_win_over_a_job_value_of_the_same_name_through_start_runtime(
    tmp_path: Path, monkeypatch
) -> None:
    _shell(monkeypatch, {"PATH": "/usr/bin"}, "HARNESS_POSTGRES_PORT,TOOLS_API_URL")
    monkeypatch.setenv("ALK_RUNTIME_VALUE_HARNESS_POSTGRES_PORT", "1111")
    monkeypatch.setenv("ALK_RUNTIME_VALUE_TOOLS_API_URL", "http://job:1")
    environment = _compose_environment(
        tmp_path,
        runtime_services=["agent-runtime"],
        running=True,
        port_variables={"HARNESS_POSTGRES_PORT": "0"},
        internal_overrides={"TOOLS_API_URL": "http://tools-api:8080"},
        runtime_configuration_names=["HARNESS_POSTGRES_PORT", "TOOLS_API_URL"],
    )
    environment.save(tmp_path)

    calls = _drive_start_runtime(tmp_path, monkeypatch)

    run_call = next(call for call in calls if "run" in call.argv)
    assert run_call.job.values["HARNESS_POSTGRES_PORT"] == "0"
    assert run_call.job.values["TOOLS_API_URL"] == "http://tools-api:8080"
    assert "TOOLS_API_URL=http://tools-api:8080" in run_call.argv


def test_bare_cli_shell_name_that_is_also_an_override_is_removed_from_the_process_environment(
    tmp_path: Path, monkeypatch
) -> None:
    _shell(
        monkeypatch,
        {
            "PATH": "/usr/bin",
            "TOOLS_API_URL": "http://shell:1",
            "SETTING": "shell-setting",
            "PORT_NAME": "shell-port",
            "KEPT": "kept",
        },
        None,
    )
    environment = _compose_environment(
        tmp_path,
        port_variables={"PORT_NAME": "7"},
        runtime_configuration_names=["SETTING"],
    )
    calls = _record_compose(monkeypatch)

    provisioning._run(
        environment, "run", process_overrides={"TOOLS_API_URL": "http://tools-api:8080"}
    )

    call = calls[0]
    for name in ("TOOLS_API_URL", "SETTING", "PORT_NAME"):
        assert name not in call.env, name
    assert call.env["KEPT"] == "kept"
    assert _seen(call)["TOOLS_API_URL"] == "http://tools-api:8080"
    assert _seen(call)["PORT_NAME"] == "7"
    assert _seen(call)["SETTING"] == "shell-setting"


def test_env_file_text_escapes_dollar_quote_backslash_and_newline_exactly() -> None:
    fd, path = tempfile.mkstemp()
    try:
        provisioning._write_env_file(fd, {"A": 'x$y"z\\w\nv', "B": "${HOME}"})
        assert Path(path).read_text(encoding="utf-8") == (
            'A="x\\$y\\"z\\\\w\\nv"\nB="\\${HOME}"\n'
        )
    finally:
        os.unlink(path)


def test_env_file_skips_and_logs_an_invalid_name_by_name_only(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="fi.alk.harness.provision")
    fd, path = tempfile.mkstemp()
    try:
        provisioning._write_env_file(
            fd, {"BAD-NAME": "v1", "1LEAD": "v2", "A B": "v3", "GOOD": "g"}
        )
        assert Path(path).read_text(encoding="utf-8") == 'GOOD="g"\n'
    finally:
        os.unlink(path)
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "BAD-NAME" in logged and "1LEAD" in logged
    assert "v1" not in logged and "v2" not in logged


def test_hosted_uploaded_google_credential_reaches_compose_as_the_container_path(
    tmp_path: Path, monkeypatch
) -> None:
    credential = tmp_path / "customer.json"
    credential.write_text('{"type":"service_account"}\n', encoding="utf-8")
    for name in list(os.environ):
        if name not in {"PYTEST_CURRENT_TEST", "PATH", "HOME"}:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(
        "ALK_RUNTIME_CONFIGURATION_NAMES", "GOOGLE_APPLICATION_CREDENTIALS"
    )
    monkeypatch.setenv(
        "ALK_RUNTIME_VALUE_GOOGLE_APPLICATION_CREDENTIALS", str(credential)
    )
    monkeypatch.setenv(
        "ALK_RUNTIME_SECRET_FILE_NAMES", "GOOGLE_APPLICATION_CREDENTIALS"
    )
    monkeypatch.setenv("ALK_HOSTED_EXECUTION", "1")
    monkeypatch.setenv("HARNESS_RUNTIME_STABLE_SECONDS", "0")
    source = tmp_path / "source"
    source.mkdir()
    provisioning.ProvisionedEnvironment(
        source=str(source),
        compose_file=str(source / "compose.yaml"),
        project="gac-upload",
        runtime_services=["agent-runtime"],
        runtime_configuration_names=["GOOGLE_APPLICATION_CREDENTIALS"],
        running=True,
    ).save(tmp_path)
    calls = []

    def run(command, **kwargs):
        files = [command[i + 1] for i, a in enumerate(command) if a == "--env-file"]
        text = Path(files[-1]).read_text(encoding="utf-8") if files else ""
        calls.append(
            types.SimpleNamespace(
                argv=list(command), env=dict(kwargs["env"]), text=text
            )
        )
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(provisioning.subprocess, "run", run)
    monkeypatch.setattr(
        provisioning,
        "_config",
        lambda _e: {"services": {"agent-runtime": {"environment": {}}}},
    )
    monkeypatch.setattr(
        provisioning,
        "_docker",
        lambda *a, **_k: "running" if a and a[0] == "inspect" else "",
    )

    provisioning.start_runtime(tmp_path)

    run_call = next(call for call in calls if "run" in call.argv)
    target = "/run/harness-secrets/google_application_credentials"
    assert f'GOOGLE_APPLICATION_CREDENTIALS="{target}"\n' in run_call.text
    assert str(credential) not in run_call.text
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in run_call.env
    assert any(
        run_call.argv[i : i + 2] == ["--env", "GOOGLE_APPLICATION_CREDENTIALS"]
        for i in range(len(run_call.argv) - 1)
    )


def _every_reserved_name() -> list[str]:
    from fi.alk.harness import secrets

    names = sorted(secrets._CLIENT_EXACT)
    names += [f"{prefix}SOME_NAME" for prefix in secrets._CLIENT_PREFIXES]
    names += ["x_proxy", "HTTPS_PROXY", "lower_case_proxy"]
    assert all(client_name_reserved(name) for name in names)
    return names


@pytest.mark.parametrize("manifest", [True, False])
def test_every_reserved_name_stays_out_of_the_job_file_and_compose_environment(
    tmp_path: Path, monkeypatch, caplog, manifest: bool
) -> None:
    reserved = _every_reserved_name()
    base = {
        "PATH": "/usr/bin",
        "HOME": "/home/worker",
        "DOCKER_HOST": "tcp://base:2375",
    }
    # On the bare CLI a shell value is not a runtime value, so the runtime layer is manifest-only.
    # It holds the names intake refuses: the skip is their backstop. A name intake admits (a
    # proxy, CA bundle or locale) fails the job by name (test_an_admitted_value_...).
    runtime_names = (
        [name for name in reserved if agent_value_name_reserved(name)] if manifest else []
    )
    _shell(monkeypatch, base, ",".join(runtime_names) if manifest else None)
    for name in runtime_names:
        monkeypatch.setenv(f"ALK_RUNTIME_VALUE_{name}", f"/evil-runtime/{name}")
    environment = _compose_environment(
        tmp_path,
        runtime_services=["agent-runtime"],
        running=True,
        runtime_configuration_names=runtime_names,
        port_variables={name: f"/evil-port/{name}" for name in reserved},
        internal_overrides={name: f"/evil-internal/{name}" for name in reserved},
    )
    environment.save(tmp_path)
    caplog.set_level(logging.DEBUG, logger=_COMPOSE_LOGGER)

    calls = _drive_start_runtime(
        tmp_path,
        monkeypatch,
        overrides={name: f"/evil-override/{name}" for name in reserved},
    )

    assert calls
    for call in calls:
        if call.job is None:
            continue
        assert not [name for name in call.job.values if client_name_reserved(name)]
        for name in reserved:
            assert call.env.get(name) == base.get(name), name
        assert not [value for value in call.env.values() if value.startswith("/evil")]
        assert not [
            value for value in call.job.values.values() if value.startswith("/evil")
        ]
        assert not [argument for argument in call.argv if argument.startswith("/evil")]
        env_names = {
            call.argv[index]
            for index in range(1, len(call.argv))
            if call.argv[index - 1] == "--env"
        }
        assert not [name for name in env_names if client_name_reserved(name)]
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for name in reserved:
        assert name in logged, name
    assert "/evil" not in logged


def test_stop_removes_the_job_directories_a_killed_worker_left_for_its_project_only(
    tmp_path: Path, monkeypatch
) -> None:
    temp = tmp_path / "shared-temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    _shell(monkeypatch, {"PATH": "/usr/bin"}, "AGENT_SETTING")
    monkeypatch.setenv("ALK_RUNTIME_VALUE_AGENT_SETTING", "v")
    environment = _stop_environment(tmp_path)

    # The directory a live call makes is named after the project.
    calls = _record_compose(monkeypatch)
    provisioning._run(environment, "up")
    assert calls[0].job.path.parent.name.startswith("alk-compose-env-compose-env.")
    assert calls[0].job.path.parent.parent == temp

    leftover = Path(tempfile.mkdtemp(prefix="alk-compose-env-compose-env.", dir=temp))
    (leftover / "job").write_text('OPENAI_API_KEY="leftover"\n')
    other = Path(tempfile.mkdtemp(prefix="alk-compose-env-other.", dir=temp))
    (other / "job").write_text('OPENAI_API_KEY="other"\n')
    longer = Path(
        tempfile.mkdtemp(prefix="alk-compose-env-compose-env-extra.", dir=temp)
    )
    monkeypatch.setattr(provisioning, "_docker", lambda *_a, **_k: "")

    assert stop(tmp_path)

    assert not leftover.exists()
    assert (other / "job").exists()
    assert longer.exists()


def test_repository_env_that_is_a_symlink_or_resolves_outside_the_source_is_not_passed(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    _shell(monkeypatch, {"PATH": "/usr/bin"}, "")
    caplog.set_level(logging.DEBUG, logger=_COMPOSE_LOGGER)
    environment = _compose_environment(tmp_path)
    source = Path(environment.source)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "target.env").write_text("OUTSIDE_NAME=outside-canary\n")
    (outside / ".env").write_text("OUTSIDE_NAME=outside-canary\n")
    calls = _record_compose(monkeypatch)

    # A ``.env`` that is a link to a file outside the source.
    (source / ".env").symlink_to(outside / "target.env")
    provisioning._run(environment, "config")
    # A ``.env`` that is a link to a file inside the source.
    (source / ".env").unlink()
    (source / "real.env").write_text("INSIDE_NAME=inside-canary\n")
    (source / ".env").symlink_to(source / "real.env")
    provisioning._run(environment, "config")
    # A regular ``.env`` in a compose directory that is itself a link out of the source.
    (source / ".env").unlink()
    (source / "linked").symlink_to(outside, target_is_directory=True)
    linked = _compose_environment(
        tmp_path, compose_file=str(source / "linked" / "compose.yaml")
    )
    provisioning._run(linked, "config")

    for call in calls:
        assert call.env_files == [str(call.job.path)]
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert str(source / ".env") in logged
    assert str(source / "linked" / ".env") in logged
    for canary in ("outside-canary", "inside-canary", "OUTSIDE_NAME", "INSIDE_NAME"):
        assert canary not in logged

    # A regular ``.env`` inside the source is still passed first.
    (source / "linked").unlink()
    (source / ".env").write_text("FROM_DOTENV=1\n")
    provisioning._run(environment, "config")
    assert calls[-1].env_files == [str(source / ".env"), str(calls[-1].job.path)]


def test_teardown_runs_from_an_empty_directory_that_is_not_the_source(
    tmp_path: Path, monkeypatch
) -> None:
    _shell(monkeypatch, _CLIENT_SETTINGS, "AGENT_SETTING")
    environment = _stop_environment(tmp_path)
    (Path(environment.source) / ".env").write_text("COMPOSE_FILE=/elsewhere.yaml\n")
    monkeypatch.setattr(provisioning, "_docker", lambda *_a, **_k: "")
    calls = _record_compose(monkeypatch)

    assert stop(tmp_path)

    call = next(call for call in calls if "down" in call.argv)
    assert call.cwd != environment.source
    assert not Path(call.cwd).resolve().is_relative_to(Path(environment.source).resolve())
    assert Path(call.cwd).parent == Path(tempfile.gettempdir())
    assert call.cwd_listing == []
    assert "--project-name" in call.argv
    assert "--file" not in call.argv and "--env-file" not in call.argv
    assert not Path(call.cwd).exists()


@pytest.mark.parametrize("manifest", ["", "AGENT_SETTING"])
def test_manifest_turns_compose_remote_git_off_whatever_the_repository_env_sets(
    tmp_path: Path, monkeypatch, manifest: str
) -> None:
    _shell(monkeypatch, _CLIENT_SETTINGS, manifest)
    environment = _compose_environment(tmp_path)
    source = Path(environment.source)
    (source / ".env").write_text("COMPOSE_EXPERIMENTAL_GIT_REMOTE=1\n")
    calls = _record_compose(monkeypatch)

    provisioning._run(environment, "config")

    call = calls[0]
    # Both files are handed over, and the process environment still beats them.
    assert call.env_files == [str(source / ".env"), str(call.job.path)]
    assert call.env["COMPOSE_EXPERIMENTAL_GIT_REMOTE"] == "0"
    assert "COMPOSE_EXPERIMENTAL_GIT_REMOTE" not in call.job.values

    # Teardown too.
    environment.runtime_configuration_names = ["AGENT_SETTING"]
    environment.running = True
    environment.save(tmp_path)
    monkeypatch.setattr(provisioning, "_docker", lambda *_a, **_k: "")
    assert stop(tmp_path)
    down = next(call for call in calls if "down" in call.argv)
    assert down.env["COMPOSE_EXPERIMENTAL_GIT_REMOTE"] == "0"


@pytest.mark.parametrize("manifest", ["", "AGENT_SETTING"])
def test_manifest_turns_compose_remote_oci_off_whatever_the_repository_env_sets(
    tmp_path: Path, monkeypatch, manifest: str
) -> None:
    _shell(monkeypatch, _CLIENT_SETTINGS, manifest)
    environment = _compose_environment(tmp_path)
    source = Path(environment.source)
    (source / ".env").write_text("COMPOSE_EXPERIMENTAL_OCI_REMOTE=1\n")
    calls = _record_compose(monkeypatch)

    provisioning._run(environment, "config")

    call = calls[0]
    assert call.env_files == [str(source / ".env"), str(call.job.path)]
    assert call.env["COMPOSE_EXPERIMENTAL_OCI_REMOTE"] == "0"
    assert "COMPOSE_EXPERIMENTAL_OCI_REMOTE" not in call.job.values

    environment.runtime_configuration_names = ["AGENT_SETTING"]
    environment.running = True
    environment.save(tmp_path)
    monkeypatch.setattr(provisioning, "_docker", lambda *_a, **_k: "")
    assert stop(tmp_path)
    down = next(call for call in calls if "down" in call.argv)
    assert down.env["COMPOSE_EXPERIMENTAL_OCI_REMOTE"] == "0"


def test_bare_cli_leaves_compose_remote_git_to_the_shell(
    tmp_path: Path, monkeypatch
) -> None:
    _shell(monkeypatch, {"PATH": "/usr/bin"}, None)
    calls = _record_compose(monkeypatch)

    provisioning._run(_compose_environment(tmp_path), "config")

    assert "COMPOSE_EXPERIMENTAL_GIT_REMOTE" not in calls[0].env


@pytest.mark.parametrize(
    "message",
    [
        'git remote resource is disabled by "COMPOSE_EXPERIMENTAL_GIT_REMOTE"',
        'experimental git remote resource is disabled. "COMPOSE_EXPERIMENTAL_GIT_REMOTE" must be '
        "set",
    ],
)
def test_a_git_include_is_refused_with_a_clear_error_that_holds_no_value(
    tmp_path: Path, monkeypatch, message: str
) -> None:
    _shell(monkeypatch, {"PATH": "/usr/bin"}, "SETTING")
    monkeypatch.setenv("ALK_RUNTIME_VALUE_SETTING", "job-secret-canary")
    environment = _compose_environment(
        tmp_path, runtime_configuration_names=["SETTING"]
    )
    calls = _record_compose(
        monkeypatch,
        returncode=1,
        output=f"{message}\ninclude git@github.com:example/remote.git job-secret-canary",
    )

    with pytest.raises(provisioning.ProvisionError) as raised:
        provisioning._run(environment, "config")

    text = str(raised.value)
    assert "git include" in text and "not allowed" in text
    assert "job-secret-canary" not in text
    assert len(calls) == 1


class _Killed(BaseException):
    """Stands in for a worker killed mid-boot: ``except Exception`` does not catch it."""


def test_a_worker_killed_during_its_first_provision_leaves_a_stoppable_project(
    tmp_path: Path, monkeypatch
) -> None:
    temp = tmp_path / "shared-temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    source = tmp_path / "agent"
    session = tmp_path / "session"
    source.mkdir()
    source.joinpath("requirements.txt").write_text("")
    source.joinpath("agent.py").write_text("print('ready')\n")
    calls: list[tuple[str, ...]] = []

    def run(_environment, *arguments, **_kwargs):
        calls.append(arguments)
        if "up" in arguments or "build" in arguments:
            raise _Killed
        if "config" in arguments and "--format" in arguments:
            return json.dumps(
                {
                    "services": {
                        "agent-runtime": {
                            "profiles": ["harness-runtime"],
                            "build": {
                                "context": str(session / "generated-runtime-context"),
                                "dockerfile": GENERATED_DOCKERFILE,
                            },
                        }
                    }
                }
            )
        return ""

    monkeypatch.setattr(provisioning, "_run", run)
    with pytest.raises(_Killed):
        provisioning.provision(source, session, _contract())

    recorded = provisioning.ProvisionedEnvironment.load(session)
    assert recorded is not None and not recorded.running
    leftover = Path(
        tempfile.mkdtemp(prefix=f"alk-compose-env-{recorded.project}.", dir=temp)
    )
    (leftover / "job").write_text('OPENAI_API_KEY="leftover"\n')
    calls.clear()
    monkeypatch.setattr(provisioning, "_docker", lambda *_a, **_k: "")

    assert stop(session)

    assert not leftover.exists()
    assert any("down" in arguments for arguments in calls)


_ENV_FILE_PARSE_ERRORS = [
    # Compose v2.40.3
    'failed to read /s/inc/.env: line 1: unexpected character "{" in variable name '
    '"{\\"k\\": \\"parse-canary\\"}"',
    # Compose v2.33.0
    'failed to read /w/sym/srcj/inc/.env: line 1: unexpected character "{" in variable '
    'name "{\\"k\\": \\"parse-canary\\"}"',
]


@pytest.mark.parametrize("message", _ENV_FILE_PARSE_ERRORS)
def test_an_env_file_compose_cannot_parse_is_named_by_category_under_the_sandbox(
    tmp_path: Path, monkeypatch, message: str
) -> None:
    _shell(monkeypatch, {"PATH": "/usr/bin"}, "")
    environment = _compose_environment(tmp_path)
    _record_compose(monkeypatch, returncode=1, output=message)

    with pytest.raises(provisioning.ProvisionError) as raised:
        provisioning._run(environment, "config")

    text = str(raised.value)
    assert "env file" in text and "could not be read" in text
    assert "parse-canary" not in text


def test_an_env_file_parse_error_is_still_shown_off_the_sandbox(
    tmp_path: Path, monkeypatch
) -> None:
    _shell(monkeypatch, {"PATH": "/usr/bin"}, None)
    environment = _compose_environment(tmp_path)
    _record_compose(monkeypatch, returncode=1, output=_ENV_FILE_PARSE_ERRORS[0])

    with pytest.raises(provisioning.ProvisionError) as raised:
        provisioning._run(environment, "config")

    assert "parse-canary" in str(raised.value)
