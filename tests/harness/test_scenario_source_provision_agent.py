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
from fi.alk.harness.framework_detection import FrameworkFolders
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
    bundle_dir: Path,
    kind: SourceKind,
    source: ss.BundleScenarioSource | None = None,
    *,
    subfolder: str | None = None,
) -> dict[str, Any]:
    client = _RecordingClient()
    job = SimpleNamespace(
        run_id="job-1",
        metadata={},
        source=SimpleNamespace(kind=kind, repository="org/clinic", subfolder=subfolder),
    )
    asyncio.run(
        (source or ss.BundleScenarioSource()).build(
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


def test_a_url_urlsplit_refuses_is_registered_redacted_and_logged_by_class_only(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A full-width number sign NFKC-normalises to "#", so urlsplit refuses the netloc, and its
    # error repeats the whole userinfo.
    leaky = "https://admin:hunter2\uff03x@db.example/"
    _write_bundle(tmp_path, _contract(leaky))

    with caplog.at_level(logging.DEBUG):
        payload = _provision(tmp_path, SourceKind.GITHUB)

    assert payload["agent"]["agent"]["behaviour"][0]["instructions"] == "[redacted]"
    assert "hunter2" not in json.dumps(payload["agent"])
    assert payload["agent_prompt"] == "[redacted]"
    # Once for the document and once for the prompt.
    logged = [record for record in caplog.records if record.name == "fi.alk.providers.agent"]
    assert [record.getMessage() for record in logged] == [
        "secrets redacted: url=ValueError"
    ] * 2
    assert all(record.exc_info is None for record in logged)
    assert "hunter2" not in caplog.text


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
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    built_from: str | None,
    job_names: str | None,
) -> None:
    _write_bundle(
        tmp_path, _contract_from("p", built_from) if built_from else _contract("p")
    )

    with caplog.at_level(logging.WARNING, logger=ss.__name__):
        payload = _provision(tmp_path, SourceKind.GITHUB, subfolder=job_names)

    assert payload.get("agent") is None
    # Told apart from any other bad document.
    assert any("detail=subfolder_mismatch" in record.getMessage() for record in caplog.records)


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


def _source_with_crew(tmp_path: Path) -> ss.BundleScenarioSource:
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "main.py").write_text(
        "from crewai import Crew\nCrew()\n", encoding="utf-8"
    )
    return ss.BundleScenarioSource(
        framework_folders=FrameworkFolders(agent, agent, None)
    )


def _framework_log_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("framework")
    ]


def test_agent_document_carries_framework(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bundle = tmp_path / "bundle"
    contract = _contract("p")
    _write_bundle(bundle, contract)

    with caplog.at_level(logging.INFO, logger="fi.alk.harness"):
        payload = _provision(bundle, SourceKind.GITHUB, _source_with_crew(tmp_path))

    lines = _framework_log_lines(caplog)
    assert any("name=crewai" in line for line in lines)
    assert not any(str(tmp_path) in line or "main.py" in line for line in lines)
    framework = {"name": "crewai", "version": None}
    assert payload["agent"]["agent"]["extension"]["framework"] == framework
    assert payload["agent"]["agent"] == build_authored_agent(
        contract, framework=framework
    )


def test_detector_crash_keeps_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    bundle = tmp_path / "bundle"
    _write_bundle(bundle, _contract("p"))
    secret = "/customer/private/path"

    def _raise(*_: Any, **__: Any) -> dict[str, Any]:
        raise RuntimeError(secret)

    monkeypatch.setattr(ss, "detect_framework", _raise)

    with caplog.at_level(logging.INFO, logger="fi.alk.harness"):
        payload = _provision(bundle, SourceKind.GITHUB, _source_with_crew(tmp_path))

    lines = _framework_log_lines(caplog)
    assert any("class=RuntimeError" in line for line in lines)
    assert not any(secret in line for line in lines)
    assert payload["agent"]["agent"]["extension"]["framework"] == {
        "name": "unknown",
        "version": None,
    }


def test_no_folders_omits_framework(tmp_path: Path) -> None:
    _write_bundle(tmp_path, _contract("p"))

    payload = _provision(tmp_path, SourceKind.GITHUB)

    assert set(payload["agent"]["agent"]["extension"]) == {"contract"}


@pytest.mark.parametrize(
    ("kind", "expected_calls"),
    [(SourceKind.GITHUB, 1), (SourceKind.ARCHIVE, 1), (SourceKind.REMOTE, 0)],
    ids=["github", "archive", "not_for_a_remote_source"],
)
def test_detection_runs_once_off_the_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: SourceKind,
    expected_calls: int,
) -> None:
    bundle = tmp_path / "bundle"
    _write_bundle(bundle, _contract("p"))
    detections: list[Any] = []
    real_to_thread = asyncio.to_thread

    async def spy(function: Any, /, *args: Any, **kwargs: Any) -> Any:
        if function is ss.detect_framework:
            detections.append(args)
        return await real_to_thread(function, *args, **kwargs)

    monkeypatch.setattr(ss.asyncio, "to_thread", spy)

    _provision(bundle, kind, _source_with_crew(tmp_path))

    assert len(detections) == expected_calls


def test_detection_gets_all_three_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "bundle"
    _write_bundle(bundle, _contract("p"))
    calls: list[tuple[Any, dict[str, Any]]] = []

    def record(folder: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append((folder, kwargs))
        return {"name": "none", "version": None}

    monkeypatch.setattr(ss, "detect_framework", record)
    folders = FrameworkFolders(tmp_path / "a", tmp_path / "b", tmp_path / "c")

    _provision(
        bundle,
        SourceKind.GITHUB,
        ss.BundleScenarioSource(framework_folders=folders),
    )

    assert calls == [
        (tmp_path / "a", {"repo_root": tmp_path / "b", "package_dir": tmp_path / "c"})
    ]


def test_a_password_in_a_prompt_url_reaches_neither_the_prompt_nor_the_document(
    tmp_path: Path,
) -> None:
    prompt = "You book visits. Post to https://bot:hunter2@hooks.example/v1?key=sk-1 after each."
    _write_bundle(tmp_path, _contract(prompt))

    payload = _provision(tmp_path, SourceKind.GITHUB)

    sent = json.dumps(payload)
    assert "hunter2" not in sent
    assert "sk-1" not in sent
    assert payload["agent_prompt"] == "You book visits. Post to https://hooks.example/v1 after each."
    assert payload["agent"]["agent"]["behaviour"][0]["instructions"] == payload["agent_prompt"]


def test_a_password_in_a_prompt_url_is_redacted_for_a_source_with_no_document(
    tmp_path: Path,
) -> None:
    _write_bundle(tmp_path, _contract("Use https://bot:hunter2@hooks.example/v1 to log."))

    payload = _provision(tmp_path, SourceKind.REMOTE)

    assert "agent" not in payload
    assert payload["agent_prompt"] == "Use https://hooks.example/v1 to log."


def test_only_the_urls_in_a_prompt_change_on_their_way_to_the_platform(tmp_path: Path) -> None:
    """B1: whitespace and the tokens without a scheme start are byte-identical."""
    prompt = (
        "You book visits.\n\n\tRules:  never share https://u:pw@h.example/x?key=sk-1#f\n"
        "Then  call postgres://db:hunter2@db.example:5432/app?ssl=1,　and stop."
    )
    _write_bundle(tmp_path, _contract(prompt))

    payload = _provision(tmp_path, SourceKind.GITHUB)

    assert payload["agent_prompt"] == (
        "You book visits.\n\n\tRules:  never share https://h.example/x\n"
        "Then  call postgres://db.example:5432/app　and stop."
    )


def test_the_document_and_the_reading_hold_the_same_cleaned_prompt(tmp_path: Path) -> None:
    """B2: the provision document, the reading's agent.json and agent_prompt agree."""
    prompt = "Post to https://bot:hunter2@hooks.example/v1?key=sk-1 after each."
    _write_bundle(tmp_path, _contract(prompt))

    payload = _provision(tmp_path, SourceKind.GITHUB)
    reading = ss.authored_agent_document(
        (tmp_path / "contract.json").read_text(encoding="utf-8"), subfolder=None, framework=None
    )

    cleaned = "Post to https://hooks.example/v1 after each."
    assert payload["agent_prompt"] == cleaned
    assert payload["agent"]["agent"]["behaviour"][0]["instructions"] == cleaned
    assert reading["agent"]["behaviour"][0]["instructions"] == cleaned
    assert "hunter2" not in json.dumps(reading) and "sk-1" not in json.dumps(reading)


# -- TH-9500: the persona record is cleaned before it leaves (plan v1.1 section 5, test P1) ------

_FAKE_KEY = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


def test_a_key_in_each_cleaned_persona_field_is_masked_and_the_rest_sent_as_written() -> (
    None
):
    body = {
        "name": f"Dana {_FAKE_KEY}",
        "use_case": f"Refund {_FAKE_KEY}",
        "instruction": f"She pastes {_FAKE_KEY} into the chat",
        "tests": "The agent refuses password=Zq8wYv3Lp9 politely",
        "persona": {
            "role": f"admin {_FAKE_KEY}",
            "background": f"keeps {_FAKE_KEY} in a note",
            "secret_motive": "wants a refund",
            "traits": ["calm", f"uses {_FAKE_KEY}"],
        },
    }
    scenario = SimpleNamespace(
        presented=ss._presented(body, scenario_key="refund-ghp_key-s1"),
        presented_fields=ss._presented(
            body, scenario_key="refund-ghp_key-s1", fields=True
        ),
        scenario_key="refund-ghp_key-s1",
    )

    payload = ss._provision_payload(
        "Run ghp_x", [scenario], ["task_completion"], "", "chat", agent_name="Desk"
    )

    [persona] = payload["personas"]
    assert _FAKE_KEY not in json.dumps(payload)
    assert "Zq8wYv3Lp9" not in json.dumps(payload)
    assert persona == {
        "name": "Dana [redacted]",
        "scenario_name": "Refund [redacted]",
        "role": "admin [redacted]",
        "situation": "She pastes [redacted] into the chat",
        "outcome": "The agent refuses password=[redacted] politely",
        "persona": {
            "role": "admin [redacted]",
            # The key markers apply to the persona's own keys too.
            "background": "keeps [redacted] in a note",
            "secret_motive": "[redacted]",
            "traits": ["calm", "uses [redacted]"],
        },
        "scenario_key": "refund-ghp_key-s1",
    }
    # As written: the platform matches on scenario_key, and the rest is the kit's own.
    assert payload["name"] == "Run ghp_x"
    assert payload["agent_name"] == "Desk"
    assert payload["chosen_evals"] == ["task_completion"]
    assert payload["modality"] == "chat"
    # The judge's record is left as it was.
    assert scenario.presented["name"] == f"Dana {_FAKE_KEY}"


def _sent(body: dict[str, Any], scenario_key: str = "s1") -> tuple[dict, dict]:
    """The payload's persona for a scenario built from `body` as `_load_one` builds it, and the
    record the judge reads."""
    presented = ss._presented(body, scenario_key=scenario_key)
    scenario = SimpleNamespace(
        presented=presented,
        presented_fields=ss._presented(body, scenario_key=scenario_key, fields=True),
        scenario_key=scenario_key,
    )
    [persona] = ss._provision_payload("Run", [scenario], (), "", "chat")["personas"]
    return persona, presented


def _straddling(at: int) -> str:
    text = ("Caller says: " + "please help me with the refund " * 10)[:at]
    text += " " + _FAKE_KEY + " and then hangs up politely."
    assert text.index(_FAKE_KEY) == at + 1
    return text


def _masked(text: str) -> str:
    at = text.index(_FAKE_KEY)
    return text[:at] + "[redacted]" + text[at + len(_FAKE_KEY) :]


@pytest.mark.parametrize("at", [222, 244])
@pytest.mark.parametrize("where", ["persona", "name_falls_back_to_the_scenario"])
def test_a_key_straddling_the_255_character_cut_of_name_and_role_is_masked(
    at: int, where: str
) -> None:
    """P1 (cr1 M4, cr2 M1): `persona_of` cuts `name` and `role` at 255 characters, so the record
    sent is `persona_of` over cleaned fields, including a name that falls back to the scenario's
    own. The record the judge reads stays as written."""
    text = _straddling(at)
    if where == "persona":
        body = {"persona": {"name": text, "role": text}, "name": "s1"}
    else:
        body = {"persona": {"role": text}, "name": text}
    body.update(instruction=text, tests="ok", use_case="Refund")

    persona, presented = _sent(body)

    for field in ("name", "role"):
        assert persona[field] == _masked(text)[:255]
    assert _FAKE_KEY[:6] not in json.dumps(persona)
    assert presented["name"] == presented["role"] == text[:255]


@pytest.mark.parametrize("at", [222, 244])
@pytest.mark.parametrize("where", ["use_case", "tests"])
def test_a_key_straddling_the_cut_of_scenario_name_is_masked(
    at: int, where: str
) -> None:
    """cr2 M1: `display_scenario_name` cuts `use_case`, or `tests` when there is no `use_case`
    and no `scenario_key` to make one from, at 255 characters."""
    text = _straddling(at)
    body = {"persona": {"name": "Dana"}, "name": "s1", "instruction": "x", where: text}

    persona, presented = _sent(body, scenario_key="")

    assert persona["scenario_name"] == _masked(text)[:255]
    assert _FAKE_KEY[:6] not in json.dumps(persona)
    assert presented["scenario_name"] == text[:255]


def test_a_key_in_a_scenario_name_the_display_rewrites_is_masked() -> None:
    """cr2 M1: with no `use_case`, `tests` or `scenario_key`, the shown name is the scenario's
    `name` with `-` and `_` made spaces, which takes a key out of its shape (`ghp A1b2…`)."""
    body = {"persona": {"name": "Dana"}, "name": f"call about {_FAKE_KEY} please"}

    persona, presented = _sent(body, scenario_key="")

    assert persona["scenario_name"] == "Call about [redacted] please"
    assert _FAKE_KEY[4:20] not in json.dumps(persona)
    assert presented["scenario_name"] == f"Call about ghp {_FAKE_KEY[4:]} please"


@pytest.mark.parametrize(
    "body",
    [
        {"persona": {"name": "N" * 300, "role": "R" * 300}, "name": "S"},
        {"persona": {"occupation": "O" * 300}, "name": "S" * 300},
        {"persona": {}, "name": ""},
        {"persona": "not a dict", "name": "S"},
        {"persona": {"name": "", "role": ""}, "name": "S"},
        {"name": "refund-for_bob", "tests": "", "use_case": ""},
        {},
    ],
    ids=[
        "persona",
        "occupation_and_fallback",
        "caller",
        "persona_not_a_dict",
        "empty",
        "name_rewritten",
        "bare",
    ],
)
def test_the_record_sent_is_the_record_shown_when_nothing_is_secret(body) -> None:
    """cr2 M1, cr3 L4: cleaning first changes no honest field, so each fallback `persona_of`
    takes is the same in the record sent."""
    for scenario_key in ("s1", ""):
        persona, presented = _sent(body, scenario_key=scenario_key)
        assert persona == {**presented, "scenario_key": scenario_key}


def test_a_key_the_shown_name_rewrites_into_shape_is_masked() -> None:
    """cr3 M4: `call_AKIA…` isn't a key's shape (a key's own character comes before it), but the
    shown name makes `_` a space, `Call AKIA…`. The clean after `persona_of` masks it."""
    body = {"persona": {"name": "Dana"}, "name": "call_AKIAFAKEFAKEFAKE1234 now"}

    persona, _ = _sent(body, scenario_key="")

    assert persona["scenario_name"] == "Call [redacted] now"
    assert "AKIAFAKE" not in json.dumps(persona)


def test_loading_a_scenario_logs_nothing_and_the_send_logs_one_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """cr3 L4: the clean and its log line are the send's. Loading a scenario cleans nothing, so
    a run that loads scenarios and never sends them logs nothing."""
    body = {"persona": {"name": f"Dana {_FAKE_KEY}"}, "name": "s1"}
    with caplog.at_level(logging.WARNING):
        presented = ss._presented(body, scenario_key="s1")
        read = ss._presented(body, scenario_key="s1", fields=True)
        loaded = [r for r in caplog.records if r.name == "fi.alk.providers.agent"]
        scenario = SimpleNamespace(
            presented=presented, presented_fields=read, scenario_key="s1"
        )
        ss._provision_payload("Run", [scenario], (), "", "chat")
        sent = [r for r in caplog.records if r.name == "fi.alk.providers.agent"]

    assert loaded == []
    assert read["persona"]["name"] == f"Dana {_FAKE_KEY}"
    assert [r.getMessage() for r in sent] == ["secrets redacted: kinds=github"]


def test_a_compiled_scenario_carries_the_fields_to_send() -> None:
    """The one place a scenario is built from its document fills `presented_fields`."""
    import inspect

    assert (
        "presented_fields=_presented(body, scenario_key=scenario_key, fields=True)"
        in inspect.getsource(ss._load_one)
    )
