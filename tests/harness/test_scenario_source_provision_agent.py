from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import fi.alk
from fi.alk.harness import scenario_source as ss
from fi.alk.harness.contract import AgentContract
from fi.alk.harness.job import SourceKind
from fi.alk.providers.agent import AgentBuildError, build_authored_agent


class _RecordingClient:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def provision(
        self, payload: dict[str, Any], *, deadline: float | None = None
    ) -> dict[str, Any]:
        self.payloads.append(payload)
        return {
            "run_test_id": "run-test-1",
            "scenarios": [{"scenario_key": "s1", "scenario_id": "platform-s1"}],
        }


def _write_bundle(bundle_dir: Path, contract: dict[str, Any] | None) -> None:
    folder = bundle_dir / ss.SCENARIOS_DIRNAME / "s1"
    (folder / "checks").mkdir(parents=True)
    (folder / "scenario.json").write_text(
        json.dumps(
            {"name": "s1", "scenario_key": "s1", "scenario_id": "", "sub_goals": []}
        ),
        encoding="utf-8",
    )
    if contract is not None:
        (bundle_dir / "contract.json").write_text(
            json.dumps(contract), encoding="utf-8"
        )


def _contract(prompt: str) -> dict[str, Any]:
    return AgentContract.model_validate(
        {
            "agent": "clinic-receptionist",
            "modality": "voice",
            "system_prompt_excerpt": prompt,
            "tools": [{"name": "find_slots", "args": ["date"]}],
        }
    ).model_dump(mode="json")


def _provision(
    bundle_dir: Path, kind: SourceKind, subfolder: str | None = None
) -> dict[str, Any]:
    client = _RecordingClient()
    job = SimpleNamespace(
        run_id="job-1",
        metadata={},
        source=SimpleNamespace(kind=kind, repository="org/clinic", subfolder=subfolder),
    )
    asyncio.run(
        ss.BundleScenarioSource().build(
            job,
            object(),
            client,
            pool=object(),
            world_factory=object(),
            bundle_dir=bundle_dir,
        )
    )
    [payload] = client.payloads
    return payload


def test_provision_payload_builds_from_the_contract_model_not_the_raw_file(
    tmp_path: Path,
) -> None:
    sparse = {"agent": "faq-bot", "tools": [{"name": "search", "args": "query"}]}
    _write_bundle(tmp_path, sparse)

    payload = _provision(tmp_path, SourceKind.GITHUB)

    expected = AgentContract.model_validate(sparse).model_dump(mode="json")
    assert payload["agent"]["agent"] == build_authored_agent(expected)
    assert payload["agent"]["agent"]["type"] == "chat"


@pytest.mark.parametrize("kind", [SourceKind.GITHUB, SourceKind.ARCHIVE])
def test_provision_payload_carries_the_agent_built_from_the_sealed_contract(
    tmp_path: Path, kind: SourceKind
) -> None:
    _write_bundle(tmp_path, _contract("first draft"))
    amended = _contract("You book clinic appointments.")
    (tmp_path / "contract.json").write_text(json.dumps(amended), encoding="utf-8")

    payload = _provision(tmp_path, kind)

    assert payload["agent"] == {
        "sdk_version": fi.alk.__version__,
        "agent": build_authored_agent(amended),
    }
    assert (
        payload["agent"]["agent"]["behaviour"][0]["instructions"]
        == "You book clinic appointments."
    )
    without_agent = {key: value for key, value in payload.items() if key != "agent"}
    assert without_agent == ss._provision_payload(
        "Clinic Receptionist",
        ss.load_scenarios(tmp_path, defer_execution=True),
        [],
        "You book clinic appointments.",
        "voice",
        agent_name="Clinic Receptionist",
    )


@pytest.mark.parametrize(
    ("kind", "contract", "build_error", "logged"),
    [
        (SourceKind.REMOTE, _contract("p"), None, None),
        (SourceKind.PROVIDER, _contract("p"), None, None),
        (SourceKind.GITHUB, None, None, "class=FileNotFoundError code="),
        (
            SourceKind.GITHUB,
            _contract("p"),
            AgentBuildError("agent_invalid", "tools/0/private_detail"),
            "class=AgentBuildError code=agent_invalid",
        ),
        (
            SourceKind.ARCHIVE,
            _contract("p"),
            RuntimeError("boom"),
            "class=RuntimeError code=",
        ),
    ],
    ids=["remote", "provider", "missing_contract", "build_error", "any_exception"],
)
def test_provision_payload_has_no_agent_for_remote_or_provider_sources_or_a_missing_contract_or_a_build_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    kind: SourceKind,
    contract: dict[str, Any] | None,
    build_error: Exception | None,
    logged: str | None,
) -> None:
    _write_bundle(tmp_path, contract)
    if build_error is not None:

        def _raise(document: Any, **_: Any) -> dict[str, Any]:
            raise build_error

        monkeypatch.setattr(ss, "build_authored_agent", _raise)

    with caplog.at_level(logging.WARNING, logger=ss.__name__):
        payload = _provision(tmp_path, kind)

    assert "agent" not in payload
    assert payload["personas"][0]["scenario_key"] == "s1"
    messages = [record.getMessage() for record in caplog.records]
    if logged is None:
        assert not any("authored agent" in message for message in messages)
    else:
        assert any(logged in message for message in messages)
    assert not any("private_detail" in message for message in messages)


def _contract_from(prompt: str, folder: str) -> dict[str, Any]:
    return {
        **_contract(prompt),
        "runtime": {"language": "python", "workdir": folder, "source_subfolder": folder},
    }


@pytest.mark.parametrize("value", ["agents/a", "my..agent"])
def test_document_echoes_subfolder_exactly(tmp_path: Path, value: str) -> None:
    _write_bundle(tmp_path, _contract_from("p", value))

    payload = _provision(tmp_path, SourceKind.GITHUB, subfolder=value)

    assert payload["agent"]["agent"]["extension"]["source"] == {"subfolder": value}


@pytest.mark.parametrize(
    ("built_from", "job_names"),
    [("agents/a", "agents/b"), ("agents/a", None), (None, "agents/a")],
)
def test_no_document_when_the_bundle_came_from_another_folder(
    tmp_path: Path, built_from: str | None, job_names: str | None
) -> None:
    _write_bundle(
        tmp_path, _contract_from("p", built_from) if built_from else _contract("p")
    )

    payload = _provision(tmp_path, SourceKind.GITHUB, subfolder=job_names)

    assert payload.get("agent") is None


@pytest.mark.parametrize("subfolder", [None, ""])
def test_document_has_no_source_without_subfolder(
    tmp_path: Path, subfolder: str | None
) -> None:
    _write_bundle(tmp_path, _contract("p"))

    payload = _provision(tmp_path, SourceKind.GITHUB, subfolder=subfolder)

    assert "source" not in payload["agent"]["agent"]["extension"]


def test_no_subfolder_setup_hash_is_pinned(tmp_path: Path) -> None:
    _write_bundle(
        tmp_path,
        {
            "agent": "faq-bot",
            "tools": [{"name": "search", "args": "query"}],
            "runtime": {"language": "python", "workdir": "app", "command": ["python", "app.py"]},
            "real_use_cases": ["ask"],
        },
    )

    payload = _provision(tmp_path, SourceKind.GITHUB)

    # The value a build without a folder produced before the field existed.
    assert (
        payload["agent"]["agent"]["setup_hash"]
        == "sh1:27408adef9319e5f760d442d8e1e4c34ca2787f736e49ede86bc7398b323dd7b"
    )
