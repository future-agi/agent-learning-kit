"""The one contract writer (TH-9491, plan v5.1 §2, §3, §V5-V7, §W6-W7).

Every save of the contract goes through ``write_contract``. It cleans the prose of URL
credentials and keeps the technical fields as written, and it cleans the caller's own object in
place, so the stage that holds it goes on from what was saved.
"""

from __future__ import annotations

import ast
import asyncio
import collections
import copy
import inspect
import json
import logging
import os
import re
import subprocess
import sys
import types
import typing
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, Field

from fi.alk.harness import (
    amend,
    authoring_entrypoint,
    authoring_runtime_validation,
    builder,
    contract as contract_module,
    nudge,
    runtime_repair,
    tools,
)
from fi.alk.harness.contract import (
    FREE_TEXT,
    KEPT_AS_WRITTEN,
    AgentContract,
    Dependency,
    Runtime,
    RuntimeInterface,
    ToolSpec,
    write_contract,
)
from fi.alk.providers.agent import _redact

SOURCE_TREE = Path(contract_module.__file__).resolve().parents[1]
PASSWORD = "hunter2"
KEY = "sk-1"
URL = f"https://bot:{PASSWORD}@hooks.example/v1?key={KEY}#frag"
CLEAN = "https://hooks.example/v1"
PROSE = f"Post to {URL} after each."
CLEANED = f"Post to {CLEAN} after each."


def _secret_free(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False)
    return PASSWORD not in text and KEY not in text


# -- §3, V7: which fields are cleaned ---------------------------------------------------------------

# Pinned by name: moving one of these into KEPT_AS_WRITTEN fails here (P16b).
PINNED_FREE_TEXT = (
    "AgentContract.one_liner",
    "AgentContract.system_prompt_excerpt",
    "AgentContract.hard_constraints",
    "AgentContract.implementation",
    "AgentContract.real_use_cases",
    "AgentContract.caller_identity",
    "AgentContract.notes",
    "AgentContract.open_questions",
    "AgentContract.amendments",
    "ToolSpec.description",
    "ToolSpec.refuses",
    "ToolSpec.effects",
    "ToolEntry.notes",
    "Dependency.what",
)


def _models_in(annotation: Any) -> list[type[BaseModel]]:
    """The models a field holds, through list, dict, Optional and Union (W10)."""
    found: list[type[BaseModel]] = []
    if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
        found.append(annotation)
    for argument in typing.get_args(annotation):
        found.extend(_models_in(argument))
    return found


def _reachable_models() -> dict[str, type[BaseModel]]:
    seen: dict[str, type[BaseModel]] = {}
    stack: list[type[BaseModel]] = [AgentContract]
    while stack:
        model = stack.pop()
        if model.__name__ in seen:
            continue
        seen[model.__name__] = model
        for field in model.model_fields.values():
            stack.extend(_models_in(field.annotation))
    return seen


def _containers_of_models(annotation: Any) -> list[Any]:
    """Each generic part of a field's type that holds a model, such as list[ToolSpec]."""
    found: list[Any] = []
    if typing.get_origin(annotation) is not None and _models_in(annotation):
        found.append(annotation)
    for argument in typing.get_args(annotation):
        found.extend(_containers_of_models(argument))
    return found


def _walked(annotation: Any) -> bool:
    """Whether write_contract's walk (contract._holders) follows a type of this shape."""
    origin = typing.get_origin(annotation)
    return (
        origin in (typing.Union, types.UnionType, typing.Annotated)
        or contract_module._walks_as_sequence(origin)
        or contract_module._walks_as_mapping(origin)
    )


def test_the_free_text_fields_are_the_fourteen_named() -> None:
    assert FREE_TEXT == PINNED_FREE_TEXT


def test_every_field_the_contract_reaches_is_classified() -> None:
    models = _reachable_models()
    # 19 models, three of them from fi.simulate (CompletedToolEvents, CompletedToolFailureEvent,
    # CompletedToolRows): a field added there must be classified here too.
    assert {"CompletedToolEvents", "CompletedToolFailureEvent", "CompletedToolRows"} <= set(models)
    classified = set(FREE_TEXT) | set(KEPT_AS_WRITTEN)
    unclassified = [
        f"{name}.{field}"
        for name, model in models.items()
        if name not in KEPT_AS_WRITTEN
        for field in model.model_fields
        if f"{name}.{field}" not in classified
    ]
    assert unclassified == []
    assert not set(FREE_TEXT) & set(KEPT_AS_WRITTEN)
    for entry in (*FREE_TEXT, *KEPT_AS_WRITTEN):
        model, _, field = entry.partition(".")
        assert model in models, entry
        assert not field or field in models[model].model_fields, entry
    # A model with free text in it is never kept whole.
    assert not {entry.split(".")[0] for entry in FREE_TEXT} & set(KEPT_AS_WRITTEN)
    # Every field that holds a model holds it in a shape the walk follows, so a new container
    # type can't save a model's free text raw (change review r3, B-L1).
    unwalked = [
        f"{name}.{field}: {shape}"
        for name, model in models.items()
        for field, info in model.model_fields.items()
        for shape in _containers_of_models(info.annotation)
        if not _walked(shape)
    ]
    assert unwalked == []


@pytest.mark.parametrize(
    "order",
    [
        ("fi.alk.harness.contract", "fi.alk.providers.agent"),
        ("fi.alk.providers.agent", "fi.alk.harness.contract"),
    ],
)
def test_the_contract_and_the_cleaner_import_in_either_order(order: tuple[str, str]) -> None:
    code = "import importlib\n" + "".join(f"importlib.import_module({name!r})\n" for name in order)
    subprocess.run([sys.executable, "-c", code], check=True, timeout=120)


# -- B12: what write_contract writes ----------------------------------------------------------------

# The KEPT_AS_WRITTEN fields that take a free string, each given a URL with credentials so that
# cleaning one is seen (P10). Literal and pattern-checked fields can't hold one (W10).
KEPT_WITH_A_URL = (
    "agent",
    "refusal_signature",
    "instrumentation",
    "agent_languages",
    "chosen_evals",
    "data_schema",
    "base_environment",
    "tools[].arg_values",
    "tool_entrypoints[].endpoint",
    "tool_entrypoints[].module",
    "dependencies[].name",
    "dependencies[].engine",
    "runtime_dependencies[].name",
    "data_store.host",
    "data_store.configured_by",
)


def _full_contract() -> AgentContract:
    """A contract with a URL holding a password and a key in every free-text field, and in each
    kept field that takes a free string."""
    body: dict[str, Any] = {
        "agent": URL,
        "modality": "chat",
        "refusal_signature": URL,
        "instrumentation": URL,
        "agent_languages": [URL],
        "chosen_evals": [URL],
        "data_schema": {"hook": URL},
        "base_environment": {"hook": [URL]},
        "tools": [
            {"name": "post", "args": ["kind"], "arg_values": {"kind": [URL]}},
            {"name": "book", "args": []},
        ],
        "tool_entrypoints": [
            {"tool": "post", "mode": "service", "endpoint": URL, "module": URL},
        ],
        "dependencies": [{"name": URL, "engine": URL}],
        "runtime_dependencies": [{"name": URL}],
        "data_store": {"kind": "database", "host": URL, "configured_by": URL},
    }
    for name in FREE_TEXT:
        model, field = name.split(".")
        holders = (
            [body]
            if model == "AgentContract"
            else [
                item
                for key, kind in (
                    ("tools", "ToolSpec"),
                    ("tool_entrypoints", "ToolEntry"),
                    ("dependencies", "Dependency"),
                    ("runtime_dependencies", "Dependency"),
                )
                if kind == model
                for item in body[key]
            ]
        )
        for holder in holders:
            annotation = AgentContract.model_fields.get(field)
            is_list = annotation is not None and typing.get_origin(annotation.annotation) is list
            holder[field] = [PROSE, "kept as written"] if is_list else PROSE
    return AgentContract.model_validate(body)


def _free_values(body: dict[str, Any], name: str) -> list[Any]:
    """Every value of one FREE_TEXT name in a saved body."""
    model, field = name.split(".")
    if model == "AgentContract":
        return [body[field]]
    keys = [
        key
        for key, info in AgentContract.model_fields.items()
        if any(found.__name__ == model for found in _models_in(info.annotation))
        and typing.get_origin(info.annotation) is list
    ]
    return [item[field] for key in keys for item in body[key]]


def _without_free_text(body: dict[str, Any]) -> dict[str, Any]:
    body = copy.deepcopy(body)
    for name in FREE_TEXT:
        model, field = name.split(".")
        if model == "AgentContract":
            body.pop(field)
            continue
        for key, info in AgentContract.model_fields.items():
            if any(found.__name__ == model for found in _models_in(info.annotation)):
                for item in body.get(key) or []:
                    item.pop(field)
    return body


def test_write_contract_cleans_every_free_text_field_and_keeps_every_other_field(
    tmp_path: Path,
) -> None:
    held = _full_contract()
    raw = held.model_dump()

    write_contract(held, tmp_path)

    saved = json.loads((tmp_path / "contract.json").read_text(encoding="utf-8"))
    for name in FREE_TEXT:
        before, after = _free_values(raw, name), _free_values(saved, name)
        assert before and len(before) == len(after), name
        for given, cleaned in zip(before, after):
            assert cleaned == _redact(given), name
            assert _secret_free(cleaned), name
    # Every field outside FREE_TEXT is exactly as it was given, URL and all.
    assert _without_free_text(saved) == _without_free_text(raw)
    kept = json.dumps(_without_free_text(saved))
    assert kept.count(URL) >= len(KEPT_WITH_A_URL)


def test_writing_a_written_contract_again_changes_nothing(tmp_path: Path) -> None:
    write_contract(_full_contract(), tmp_path / "first")
    first = (tmp_path / "first" / "contract.json").read_bytes()

    write_contract(AgentContract.model_validate_json(first), tmp_path / "second")

    assert (tmp_path / "second" / "contract.json").read_bytes() == first


# What a reading saves beside its contract; any JSON value is kept as it was.
RECORD = {
    "version": 1,
    "source": {"kind": "archive", "archive_artifact_id": "upload-1"},
    "subfolder": "agents/a",
    "evals_sha256": None,
    "generic_harness_v1": True,
    "odd": [0.1, "日本", {"nested": [None, False]}],
}


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def test_write_contract_keeps_the_record_saved_beside_the_contract(
    tmp_path: Path,
) -> None:
    held = _full_contract()
    body = {**held.model_dump(), contract_module.READING_RECORD: RECORD}
    (tmp_path / "contract.json").write_text(json.dumps(body), encoding="utf-8")

    write_contract(held, tmp_path)
    saved = json.loads((tmp_path / "contract.json").read_text(encoding="utf-8"))
    write_contract(AgentContract.model_validate(saved), tmp_path)
    again = json.loads((tmp_path / "contract.json").read_text(encoding="utf-8"))

    for one in (saved, again):
        assert next(iter(one)) == contract_module.READING_RECORD
        assert _canon(one[contract_module.READING_RECORD]) == _canon(RECORD)
    # Kept outside the model: neither the schema a model writes against nor the repair's hash
    # of the contract sees it.
    assert (
        contract_module.READING_RECORD
        not in AgentContract.model_json_schema()["properties"]
    )
    assert runtime_repair._contract_hash(
        AgentContract.model_validate(again)
    ) == runtime_repair._contract_hash(held)


@pytest.mark.parametrize(
    "saved",
    [
        None,
        b"{}",
        json.dumps({"agent": "a", "notes": "no reading_inputs here"}).encode(),
        json.dumps({"runtime": {"reading_inputs": RECORD}}).encode(),
        json.dumps(["reading_inputs", RECORD]).encode(),
        b'{"reading_inputs": ',
        b'\xff{"reading_inputs": {}}',
    ],
    ids=[
        "no_file",
        "empty",
        "named_in_prose",
        "nested",
        "not_an_object",
        "not_json",
        "not_utf8",
    ],
)
def test_write_contract_writes_no_record_where_none_is_saved(
    tmp_path: Path, saved: bytes | None
) -> None:
    if saved is not None:
        (tmp_path / "contract.json").write_bytes(saved)
    write_contract(_full_contract(), tmp_path / "fresh")

    write_contract(_full_contract(), tmp_path)

    written = (tmp_path / "contract.json").read_bytes()
    assert contract_module.READING_RECORD not in json.loads(written)
    assert written == (tmp_path / "fresh" / "contract.json").read_bytes()


def test_write_contract_cleans_the_callers_object_in_place_and_returns_it(tmp_path: Path) -> None:
    held = _full_contract()
    rules, tool = held.hard_constraints, held.tools[0]

    returned = write_contract(held, tmp_path)

    assert returned is held
    assert held.hard_constraints is rules and held.tools[0] is tool
    assert held.hard_constraints == [CLEANED, "kept as written"]
    assert tool.description == CLEANED
    assert held.model_dump() == AgentContract.model_validate_json(
        (tmp_path / "contract.json").read_text(encoding="utf-8")
    ).model_dump()


def test_a_failed_move_leaves_the_saved_contract_and_the_held_one_as_they_were(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_contract(AgentContract(agent="desk", one_liner="books desks"), tmp_path)
    saved = (tmp_path / "contract.json").read_bytes()
    held = AgentContract(agent="desk", one_liner=PROSE)

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("no space left on the device")

    monkeypatch.setattr(contract_module.os, "replace", refuse)
    with pytest.raises(OSError):
        write_contract(held, tmp_path)

    assert (tmp_path / "contract.json").read_bytes() == saved
    assert sorted(path.name for path in tmp_path.iterdir()) == ["contract.json"]
    # The held object is cleaned only after the move succeeds.
    assert held.one_liner == PROSE


def test_write_contract_syncs_the_written_file_before_moving_it_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Change review r1, A-L2 (plan §2.2): the file moved in is the one synced, synced first."""
    calls: list[tuple[str, int, str]] = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(descriptor: int) -> None:
        calls.append(("fsync", os.fstat(descriptor).st_ino, ""))
        real_fsync(descriptor)

    def replace(source: Any, target: Any) -> None:
        calls.append(("replace", os.stat(source).st_ino, Path(target).name))
        real_replace(source, target)

    monkeypatch.setattr(contract_module.os, "fsync", fsync)
    monkeypatch.setattr(contract_module.os, "replace", replace)

    write_contract(AgentContract(agent="desk", one_liner=PROSE), tmp_path)

    assert [(kind, name) for kind, _, name in calls] == [("fsync", ""), ("replace", "contract.json")]
    assert calls[0][1] == calls[1][1]
    assert json.loads((tmp_path / "contract.json").read_text(encoding="utf-8"))["one_liner"] == (
        CLEANED
    )


class _Wider(AgentContract):
    """A contract with free-text models in field shapes AgentContract doesn't use yet (A-L3)."""

    fallback_tool: ToolSpec | None = None
    tools_by_name: dict[str, ToolSpec] = Field(default_factory=dict)
    tool_groups: list[list[ToolSpec]] = Field(default_factory=list)
    dependency_by_use: dict[str, list[Dependency | None]] = Field(default_factory=dict)


def test_write_contract_finds_free_text_models_in_any_field_shape(tmp_path: Path) -> None:
    """Change review r1, A-L3: an Optional, a dict's values, nested lists, all cleaned in place."""
    fallback = ToolSpec(name="fallback", description=PROSE, refuses=PROSE)
    named = ToolSpec(name="post", description=PROSE, effects=PROSE)
    grouped = ToolSpec(name="book", effects=PROSE)
    dependency = Dependency(name="hooks", what=PROSE)
    held = _Wider(
        agent="desk",
        fallback_tool=fallback,
        tools_by_name={"post": named},
        tool_groups=[[grouped]],
        dependency_by_use={"post": [None, dependency]},
    )

    write_contract(held, tmp_path)

    saved = json.loads((tmp_path / "contract.json").read_text(encoding="utf-8"))
    assert _secret_free(saved)
    assert saved["fallback_tool"]["description"] == CLEANED
    assert saved["fallback_tool"]["refuses"] == CLEANED
    assert saved["tools_by_name"]["post"]["description"] == CLEANED
    assert saved["tools_by_name"]["post"]["effects"] == CLEANED
    assert saved["tool_groups"][0][0]["effects"] == CLEANED
    empty, given = saved["dependency_by_use"]["post"]
    assert empty is None and given["what"] == CLEANED and given["name"] == "hooks"
    # The caller's own objects, in place.
    assert held.fallback_tool is fallback and fallback.description == CLEANED
    assert held.tools_by_name["post"] is named and named.effects == CLEANED
    assert grouped.effects == CLEANED and dependency.what == CLEANED


class _Shaped(AgentContract):
    """Change review r2, A-L1: tuples, a Sequence and a Mapping, which a first walk missed."""

    tool_tuple: tuple[ToolSpec, ...] = ()
    tool_pair: tuple[ToolSpec, Dependency] | None = None
    tool_seq: typing.Sequence[ToolSpec] = Field(default_factory=list)
    tool_map: typing.Mapping[str, ToolSpec] = Field(default_factory=dict)
    tool_list: typing.List[ToolSpec] = Field(default_factory=list)  # noqa: UP006


def test_write_contract_finds_free_text_models_in_tuples_sequences_and_mappings(
    tmp_path: Path,
) -> None:
    """Change review r2, A-L1: each shape is cleaned on disk and on the caller's own objects."""
    tools = {name: ToolSpec(name=name, description=PROSE) for name in ("t", "p", "s", "m", "l")}
    dependency = Dependency(name="hooks", what=PROSE)
    held = _Shaped(
        agent="desk",
        tool_tuple=(tools["t"],),
        tool_pair=(tools["p"], dependency),
        tool_seq=[tools["s"]],
        tool_map={"m": tools["m"]},
        tool_list=[tools["l"]],
    )

    write_contract(held, tmp_path)

    saved = json.loads((tmp_path / "contract.json").read_text(encoding="utf-8"))
    assert _secret_free(saved)
    assert saved["tool_tuple"][0]["description"] == CLEANED
    assert saved["tool_pair"][0]["description"] == CLEANED
    assert saved["tool_pair"][1]["what"] == CLEANED and saved["tool_pair"][1]["name"] == "hooks"
    assert saved["tool_seq"][0]["description"] == CLEANED
    assert saved["tool_map"]["m"]["description"] == CLEANED
    assert saved["tool_list"][0]["description"] == CLEANED
    assert all(tool.description == CLEANED for tool in tools.values())
    assert dependency.what == CLEANED and _secret_free(held.model_dump())


class _Ordered(AgentContract):
    """Change review r3, B-L1: a subclass of a mapping, which a membership test missed."""

    tools_in_order: collections.OrderedDict[str, ToolSpec] = Field(
        default_factory=collections.OrderedDict
    )


def test_write_contract_finds_free_text_models_in_a_mapping_subclass(tmp_path: Path) -> None:
    tool = ToolSpec(name="o", description=PROSE)
    held = _Ordered(agent="desk", tools_in_order=collections.OrderedDict(o=tool))

    write_contract(held, tmp_path)

    saved = json.loads((tmp_path / "contract.json").read_text(encoding="utf-8"))
    assert _secret_free(saved)
    assert saved["tools_in_order"]["o"]["description"] == CLEANED
    assert tool.description == CLEANED
    assert _walked(_Ordered.model_fields["tools_in_order"].annotation)


def test_write_contract_passes_over_an_empty_optional_model(tmp_path: Path) -> None:
    """Change review r1, A-L3: a free-text model field left None saves as None.

    A regression guard only: no plant on the current walk makes it fail, since every branch
    already passes a None over. It pins the behaviour for a rewrite of the walk."""
    held = _Wider(agent="desk", one_liner=PROSE)

    write_contract(held, tmp_path)

    saved = json.loads((tmp_path / "contract.json").read_text(encoding="utf-8"))
    assert saved["fallback_tool"] is None and saved["one_liner"] == CLEANED
    assert held.fallback_tool is None and held.one_liner == CLEANED


def test_one_save_logs_one_line_however_many_urls_are_refused(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    refused = "https://admin:hunter2＃x@db.example/"
    held = AgentContract(
        agent="desk", one_liner=refused, notes=refused, hard_constraints=[refused] * 5
    )

    with caplog.at_level(logging.DEBUG, logger="fi.alk.providers.agent"):
        write_contract(held, tmp_path)

    logged = [record for record in caplog.records if record.name == "fi.alk.providers.agent"]
    assert [record.getMessage() for record in logged] == [
        "a URL was redacted whole: class=ValueError"
    ]
    assert PASSWORD not in caplog.text
    assert PASSWORD not in (tmp_path / "contract.json").read_text(encoding="utf-8")


# -- B13: every writer goes through write_contract --------------------------------------------------


class _Refused(Exception):
    pass


@pytest.fixture
def refusing_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise _Refused

    # Every module that holds the name, so no caller can reach the real one.
    for module in (contract_module, tools, amend, runtime_repair):
        monkeypatch.setattr(module, "write_contract", refuse)


def _amendable() -> AgentContract:
    return AgentContract(
        agent="desk",
        one_liner="books desks",
        real_use_cases=["book a desk"],
        hard_constraints=["Confirm before booking"],
        tools=[{"name": "post", "args": ["kind"], "arg_values": {"kind": ["a"]}}],
        tool_entrypoints=[{"tool": "gone", "mode": "unreachable"}],
    )


def _declare_tool(tmp_path: Path, contract: AgentContract, description: str = "cancel") -> Any:
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    (source / "agent.py").write_text("def cancel():\n    return 'ok'\n", encoding="utf-8")
    server = builder.suite_server(tmp_path / "out", [], contract, source)
    declare = next(one for one in server.tools if one.name == "declare_tool")
    return asyncio.run(
        declare.handler(
            {
                "name": "cancel",
                "description": description,
                "evidence_file": "agent.py",
                "evidence_quote": "def cancel():",
                "why": "the agent has it",
            }
        )
    )


def _runtime_repair(tmp_path: Path) -> None:
    source = tmp_path / "source"
    service = source / "service"
    service.mkdir(parents=True)
    (service / "pyproject.toml").write_text("[project]\nname='agent'\nversion='1'\n")
    (service / "app.py").write_text("# entrypoint\n")
    contract = AgentContract(agent="support-agent", runtime=Runtime(language="python"))
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(contract.model_dump_json(indent=2) + "\n")
    runtime_repair.apply_runtime_plan_patch(
        source,
        contract_path,
        runtime_repair.RuntimePlanPatch(
            base_contract_hash=runtime_repair._contract_hash(contract),
            runtime=Runtime(
                language="python",
                workdir="service",
                command=["python", "app.py"],
                interface=RuntimeInterface(kind="http", port=8080, path="/chat"),
            ),
            evidence_paths=("service/pyproject.toml", "service/app.py"),
            diagnostic_codes=("generated_runtime_plan_invalid",),
            summary="Run the HTTP service declared by the submitted component.",
        ),
        allowed_diagnostic_codes={"generated_runtime_plan_invalid"},
    )


def _data_store_repair(tmp_path: Path) -> int:
    """V8 row 5: the data-store repair's helper, called directly with a fake candidate folder."""
    authoring = tmp_path / "authoring"
    authoring.mkdir()
    contract = AgentContract(agent="desk", real_use_cases=["book"])
    (authoring / "contract.json").write_text(contract.model_dump_json(), encoding="utf-8")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "contract.json").write_text(
        AgentContract(agent="desk", data_store={"kind": "in_memory"}).model_dump_json(),
        encoding="utf-8",
    )
    return authoring_runtime_validation._promote_data_store(contract, authoring, candidate)


def _write_stale_contract(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "contract.json").write_text(_amendable().model_dump_json(), encoding="utf-8")
    return workspace


_WRITERS = {
    "_save_contract": lambda tmp: tools._save_contract(_amendable(), tmp),
    "widen": lambda tmp: amend.widen(
        _amendable(), tmp, tool_name="post", argument="kind", values=["b"], why="w"
    ),
    "add_rule": lambda tmp: amend.add_rule(_amendable(), tmp, rule="Be brief", why="w"),
    "drop_rule": lambda tmp: amend.drop_rule(
        _amendable(), tmp, rule="Confirm before booking", why="w"
    ),
    "fix_tool": lambda tmp: amend.fix_tool(
        _amendable(), tmp, tool_name="post", why="w", description="posts"
    ),
    "set_modality": lambda tmp: amend.set_modality(_amendable(), tmp, modality="voice", why="w"),
    "builder.declare_tool": lambda tmp: _declare_tool(tmp, _amendable()),
    "nudge": lambda tmp: nudge.drop_stale_tool_references(_write_stale_contract(tmp)),
    "authoring_entrypoint": lambda tmp: authoring_entrypoint._refresh_source_facts(
        None, tmp, _write_stale_contract(tmp)
    ),
    "runtime_plan_repair": _runtime_repair,
    "data_store_repair": _data_store_repair,
}


@pytest.mark.parametrize("writer", list(_WRITERS))
def test_every_contract_writer_goes_through_write_contract(
    writer: str, tmp_path: Path, refusing_writer: None
) -> None:
    with pytest.raises(_Refused):
        _WRITERS[writer](tmp_path)


# The static half of B13 (W7). A function is flagged when it names the contract file and calls a
# write verb; the allow-list holds every such function by (module, function, verb, count), so a
# new write in any of them, or a new function, fails the test. The scan reads the source, so it
# is a heuristic: the raising patch above and the canaries are what prove each writer goes
# through write_contract. Change review r1 (A-M1) widened it to the nine shapes it missed:
# a path held on self, helpers to any depth, methods and nested defs as helpers, lambdas, an
# aliased CONTRACT_FILE, functions imported from shutil and os, the file named at the end of a
# path, an open mode held in a variable, and os.open, os.fdopen and os.link. Change review r2
# (A-M1) added three more: a module-level name bound to the file, imported into another module
# under any name or read as module.NAME; a helper method called on an instance of a class
# defined in the same module (Keeper().save(...), or keeper = Keeper() then keeper.save(...));
# and zipfile's writestr and tarfile's addfile. Change review r3 (B-M1) added three more: a static
# or class method called on a class defined in the same module (Store.keep(...)); a class
# constant bound to the file in a class body, read as Names.CONTRACT_JSON; and a name bound to a
# marker by an import (from .contract import CONTRACT_FILE as CONTRACT_JSON), which is a marker
# in every module that imports it from there.
#
# What it still can't see, each a way to write the file on purpose rather than by accident:
# - a getter that returns the path (contract_path(out).write_text(...));
# - a path handed to a constructor and written by a method of the object
#   (JsonFile(out / "contract.json").save(body), B's plant R3a);
# - a copy run as a process (subprocess and cp), or through functools.partial, getattr or
#   io.FileIO;
# - a name built at run time ("contract" + ".json"), the file named as bytes (b"contract.json"),
#   or a module global set in one function and written in another;
# - a helper kept in a module-level lambda (_keep = lambda path, body: path.write_text(body)),
#   or a bound method kept in a name (write = (out / "contract.json").write_text; write(body));
# - a def that hands its own parameter to an instance method called on its class with the
#   instance passed by hand (Keeper.save(keeper, path)) is not made a helper, since the slots
#   are read one place off; the call itself is flagged where the file is named;
# - a class constant read on a class from another module, or on an instance other than self;
# - ZipFile.write and TarFile.add with the file as the archive name, since write and add are
#   too common to be verbs;
# - a helper method inherited from a class in another module, a method called on an instance
#   of an imported class, and a helper function re-exported through a package __init__;
# - a changed target: the allow-list keys each function by (module, function, verb, count), so
#   a write already counted in an allow-listed function can be pointed at contract.json without
#   changing its count, and the scan still passes.
MARKER_NAMES = frozenset({"CONTRACT", "CONTRACT_FILE"})
MARKER_TEXT = "contract.json"
# The file by itself, or as the last part of a path: "authoring/contract.json".
_MARKER_PATH = re.compile(r"(?:^|[/\\])" + re.escape(MARKER_TEXT) + r"$")
_DOTTED_VERBS = frozenset(
    {
        "os.replace",
        "os.rename",
        "os.renames",
        "os.link",
        "os.symlink",
        "shutil.copy",
        "shutil.copy2",
        "shutil.copyfile",
        "shutil.copytree",
        "shutil.move",
        "json.dump",
    }
)
# A call into one of these modules is a verb only when it is listed above or is an open that
# writes, never by the attribute rules below (os.open is not Path.open).
_KNOWN_MODULES = frozenset({"os", "shutil", "json", "io", "codecs"})
_WRITE_FLAGS = frozenset({"O_WRONLY", "O_RDWR", "O_CREAT", "O_APPEND", "O_TRUNC", "O_EXCL"})
_READ_FLAGS = frozenset(
    {"os", "O_RDONLY", "O_CLOEXEC", "O_NOFOLLOW", "O_BINARY", "O_NONBLOCK", "O_DIRECTORY"}
)
_SELF = frozenset({"self", "cls"})
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)
# A lambda is not a scope of its own: its body counts in the def around it.
_SCOPES = (*_DEFS, ast.ClassDef)


@dataclass(frozen=True)
class _Module:
    """What a module's names resolve to, for telling which def a call reaches."""

    name: str
    # Local names bound to a module: the stdlib's, or one of this package's.
    modules: dict[str, str]
    # Local names bound to a function of _KNOWN_MODULES, as "module.function".
    functions: dict[str, str]
    # Local names imported from one of this package's modules, as (module, name).
    names: dict[str, tuple[str, str]]
    # The functions defined here, at any depth (methods aside).
    defined: frozenset[str]
    # The classes defined here, at any depth, and the names bound anywhere here to an instance
    # of one: keeper = Keeper(...).
    classes: frozenset[str] = frozenset()
    instances: frozenset[str] = frozenset()


def _module_name(path: Path, root: Path) -> str:
    parts = path.relative_to(root.parent.parent).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _context(tree: ast.Module, path: Path, root: Path, package: frozenset[str]) -> _Module:
    name = _module_name(path, root)
    here = name.split(".") if path.name == "__init__.py" else name.split(".")[:-1]
    modules = {one: one for one in _KNOWN_MODULES}
    functions: dict[str, str] = {}
    names: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    modules[alias.asname] = alias.name
                else:
                    modules[alias.name.split(".")[0]] = alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            base = here[: len(here) - node.level + 1] if node.level else []
            source = ".".join([*base, *([node.module] if node.module else [])])
            for alias in node.names:
                local = alias.asname or alias.name
                if f"{source}.{alias.name}" in package:
                    modules[local] = f"{source}.{alias.name}"
                elif source in _KNOWN_MODULES:
                    functions[local] = f"{source}.{alias.name}"
                else:
                    names[local] = (source, alias.name)
    defined = frozenset(node.name for node, method in _defs(tree) if not method)
    classes = frozenset(node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef))
    instances: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            value, targets = node.value, node.targets
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
            value, targets = node.value, [node.target]
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            value, targets = node.context_expr, [node.optional_vars]
        else:
            continue
        if _instance_of(value, classes):
            instances |= {one.id for one in targets if isinstance(one, ast.Name)}
    return _Module(name, modules, functions, names, defined, classes, frozenset(instances))


def _instance_of(node: ast.AST, classes: frozenset[str]) -> bool:
    """Whether a node makes an instance of one of these classes: Keeper(...)."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in classes
    )


def _marker_aliases(tree: ast.Module) -> frozenset[str]:
    """Names CONTRACT or CONTRACT_FILE is imported under: ``import CONTRACT_FILE as _NAME``."""
    return frozenset(
        alias.asname
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name in MARKER_NAMES and alias.asname
    )


def _docstrings(tree: ast.AST) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                found.add(id(body[0].value))
    return found


def _own_nodes(scope: ast.AST) -> list[ast.AST]:
    """A scope's own nodes: a nested def or class is scanned as its own scope, a lambda is not."""
    nodes: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        nodes.append(node)
        if not isinstance(node, _SCOPES):
            stack.extend(ast.iter_child_nodes(node))
    return nodes


def _argument(call: ast.Call, position: int, keyword: str) -> ast.AST | None:
    for one in call.keywords:
        if one.arg == keyword:
            return one.value
    return call.args[position] if len(call.args) > position else None


def _mode_writes(mode: ast.AST | None) -> bool:
    """Whether an open mode may write. One held in a variable may."""
    if mode is None:
        return False
    if isinstance(mode, ast.Constant):
        return isinstance(mode.value, str) and any(flag in mode.value for flag in "wax+")
    return True


def _flags_write(call: ast.Call) -> bool:
    """Whether os.open's flags may write: a write flag, or flags this can't read."""
    flags = _argument(call, 1, "flags")
    if flags is None:
        return False
    if isinstance(flags, ast.Constant):
        return flags.value != 0
    names = {sub.attr for sub in ast.walk(flags) if isinstance(sub, ast.Attribute)}
    names |= _names_in(flags)
    return bool(names & _WRITE_FLAGS or names - _READ_FLAGS - _WRITE_FLAGS)


def _dotted(func: ast.AST, imports: _Module) -> str | None:
    """``module.function`` for a call into one of _KNOWN_MODULES, through any import alias."""
    if isinstance(func, ast.Name):
        return imports.functions.get(func.id)
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        module = imports.modules.get(func.value.id)
        if module in _KNOWN_MODULES:
            return f"{module}.{func.attr}"
    return None


def _written(call: ast.Call, slots: frozenset[int | str]) -> list[ast.AST]:
    """The arguments a call to a helper writes to: those in its slots, and any * or ** ones."""
    targets: list[ast.AST] = [
        argument
        for index, argument in enumerate(call.args)
        if index in slots or isinstance(argument, ast.Starred)
    ]
    targets += [one.value for one in call.keywords if one.arg is None or one.arg in slots]
    return targets


# A helper is keyed by ("function" or "method", module, name). A call reaches one through the
# module's own names: a bare name defined here or imported from where it is defined, a function
# on an imported module, or a method of this module on self or cls or on an instance of a class
# defined here. A call on any other object is never a helper call, so a common name such as get
# or append can't spread through the tree.
_Helpers = dict[tuple[str, str, str], frozenset[int | str]]


def _helper(func: ast.AST, helpers: _Helpers, imports: _Module) -> frozenset[int | str] | None:
    if isinstance(func, ast.Name):
        if func.id in imports.defined:
            return helpers.get(("function", imports.name, func.id))
        if func.id in imports.names:
            return helpers.get(("function", *imports.names[func.id]))
        return None
    if isinstance(func, ast.Attribute) and _instance_of(func.value, imports.classes):
        return helpers.get(("method", imports.name, func.attr))
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if func.value.id in _SELF or func.value.id in imports.instances:
            return helpers.get(("method", imports.name, func.attr))
        if func.value.id in imports.classes:
            # A static or class method called on its class: Store.keep(...) (change review r3).
            return helpers.get(("method", imports.name, func.attr))
        module = imports.modules.get(func.value.id)
        if module is not None:
            return helpers.get(("function", module, func.attr))
    return None


def _verb(call: ast.Call, helpers: _Helpers, imports: _Module) -> tuple[str, list[ast.AST]] | None:
    """The write verb a call is, with the expressions it writes to."""
    func = call.func
    dotted = _dotted(func, imports)
    if dotted in _DOTTED_VERBS:
        return dotted, list(call.args[:2])
    if dotted == "os.open":
        return (dotted, list(call.args[:1])) if _flags_write(call) else None
    if dotted in ("os.fdopen", "io.open", "codecs.open"):
        return (dotted, list(call.args[:1])) if _mode_writes(_argument(call, 1, "mode")) else None
    if dotted is not None:
        return None
    if isinstance(func, ast.Attribute):
        if func.attr in ("write_text", "write_bytes"):
            return func.attr, [func.value]
        # Path.replace(target) and Path.rename(target) take one argument; str.replace two.
        if func.attr in ("replace", "rename") and len(call.args) == 1 and not call.keywords:
            return f"Path.{func.attr}", [func.value, call.args[0]]
        if func.attr in ("hardlink_to", "symlink_to") and len(call.args) == 1:
            return f"Path.{func.attr}", [func.value, call.args[0]]
        if func.attr == "open":
            # tarfile.open(name, mode) takes the mode second; Path.open(mode) first.
            on_a_module = isinstance(func.value, ast.Name) and func.value.id in imports.modules
            mode = _argument(call, 1 if on_a_module else 0, "mode")
            return ("Path.open", [func.value]) if _mode_writes(mode) else None
        if func.attr == "_write_whole":
            return func.attr, list(call.args[:1])
        if func.attr in ("writestr", "addfile"):
            # ZipFile.writestr(arcname, data) and TarFile.addfile(tarinfo, fileobj).
            return func.attr, list(call.args[:1])
        slots = _helper(func, helpers, imports)
        return (func.attr, _written(call, slots)) if slots is not None else None
    if isinstance(func, ast.Name):
        if func.id == "open":
            return ("open", list(call.args[:1])) if _mode_writes(_argument(call, 1, "mode")) else None
        if func.id == "_write_whole":
            return func.id, list(call.args[:1])
        slots = _helper(func, helpers, imports)
        return (func.id, _written(call, slots)) if slots is not None else None
    return None


def _names_in(node: ast.AST) -> set[str]:
    return {sub.id for sub in ast.walk(node) if isinstance(sub, ast.Name)}


def _bound(nodes: list[ast.AST], seeds: set[str], is_seed: Any = None) -> set[str]:
    """The seeds, and every name bound here from an expression holding one, to a fixed point."""
    marked = set(seeds)
    pairs: list[tuple[set[str], bool, set[str]]] = []
    for node in nodes:
        if isinstance(node, ast.Assign):
            value, targets = node.value, node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)) and node.value:
            value, targets = node.value, [node.target]
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            value, targets = node.context_expr, [node.optional_vars]
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            value, targets = node.iter, [node.target]
        else:
            continue
        seeded = is_seed is not None and any(is_seed(sub) for sub in ast.walk(value))
        pairs.append((_names_in(value), seeded, set().union(*map(_names_in, targets))))
    changed = True
    while changed:
        changed = False
        for names, seeded, bound in pairs:
            if (seeded or names & marked) and bound - marked:
                marked |= bound
                changed = True
    return marked


def _defs(tree: ast.Module) -> list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, bool]]:
    """Every def in a module, at any depth, and whether it is a method (a def nested in a method
    is a function: it is called by its name)."""
    found: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, bool]] = []

    def collect(node: ast.AST, in_class: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _DEFS):
                found.append((child, in_class))
                collect(child, False)
            elif isinstance(child, ast.ClassDef):
                collect(child, True)
            else:
                collect(child, in_class)

    collect(tree, False)
    return found


def _parameters(node: ast.FunctionDef | ast.AsyncFunctionDef, method: bool) -> list[tuple[int | None, str]]:
    """A def's parameters by (position, name); a method's self or cls is not one of them."""
    arguments = node.args
    positional = [one.arg for one in (*arguments.posonlyargs, *arguments.args)]
    static = any(
        isinstance(decorator, ast.Name) and decorator.id == "staticmethod"
        for decorator in node.decorator_list
    )
    if method and not static and positional and positional[0] in _SELF:
        positional = positional[1:]
    named: list[tuple[int | None, str]] = list(enumerate(positional))
    named += [(None, one.arg) for one in arguments.kwonlyargs]
    return named


def _writer_helpers(trees: dict[Path, ast.Module], imports: dict[Path, _Module]) -> _Helpers:
    """Functions, methods and nested defs that write to one of their parameters, each with the
    parameters it writes to (by position and by name). Found to a fixed point: a def that hands
    a parameter to a known helper is a helper too, however deep.

    write_contract is the sanctioned writer, so a call to it is never a write verb here."""
    prepared = []
    for path, tree in trees.items():
        for node, method in _defs(tree):
            if node.name == "write_contract":
                continue
            own = _own_nodes(node)
            calls = [sub for sub in own if isinstance(sub, ast.Call)]
            if not calls:
                continue
            derived = [
                (slot, name, _bound(own, {name})) for slot, name in _parameters(node, method)
            ]
            key = ("method" if method else "function", imports[path].name, node.name)
            prepared.append((key, calls, derived, imports[path]))
    helpers: dict[tuple[str, str, str], set[int | str]] = {}
    changed = True
    while changed:
        changed = False
        known = {key: frozenset(slots) for key, slots in helpers.items()}
        for key, calls, derived, module_imports in prepared:
            for call in calls:
                found = _verb(call, known, module_imports)
                if not found:
                    continue
                written = set().union(*map(_names_in, found[1]))
                for slot, parameter, names in derived:
                    if written & names:
                        fresh = {parameter} if slot is None else {parameter, slot}
                        slots = helpers.setdefault(key, set())
                        if not fresh <= slots:
                            slots |= fresh
                            changed = True
    return {key: frozenset(slots) for key, slots in helpers.items()}


def _self_attrs(node: ast.ClassDef, marked: set[str], is_marker: Any) -> frozenset[str]:
    """The attributes of self or cls that hold the contract file in a class, set in any of its
    methods or in its body, to a fixed point."""
    scopes = [_own_nodes(node)] + [_own_nodes(sub) for sub in ast.walk(node) if isinstance(sub, _DEFS)]
    attrs: set[str] = set()
    while True:
        known = frozenset(attrs)

        def seed(sub: ast.AST, known: frozenset[str] = known) -> bool:
            return is_marker(sub, known)

        for index, own in enumerate(scopes):
            names = _bound(own, marked, seed) - _SELF
            for sub in own:
                if isinstance(sub, ast.Assign):
                    value, targets = sub.value, sub.targets
                elif isinstance(sub, (ast.AnnAssign, ast.AugAssign)) and sub.value is not None:
                    value, targets = sub.value, [sub.target]
                else:
                    continue
                if not (_names_in(value) & names or any(seed(part) for part in ast.walk(value))):
                    continue
                for target in targets:
                    for part in ast.walk(target):
                        if isinstance(part, ast.Attribute) and isinstance(part.value, ast.Name):
                            if part.value.id in _SELF:
                                attrs.add(part.attr)
                        elif isinstance(part, ast.Name) and index == 0:
                            # A class constant, read as self.NAME or cls.NAME.
                            attrs.add(part.id)
        if attrs == known:
            return known


def _names_the_file(
    sub: ast.AST,
    docs: set[int],
    marker_names: frozenset[str],
    imports: _Module,
    exported: dict[str, frozenset[str]],
) -> bool:
    """Whether a node names the contract file: the text, a marker name, or module.NAME for a
    name another module binds to the file."""
    if isinstance(sub, ast.Constant):
        return isinstance(sub.value, str) and bool(_MARKER_PATH.search(sub.value)) and id(sub) not in docs
    if isinstance(sub, ast.Name):
        return sub.id in marker_names
    if isinstance(sub, ast.Attribute):
        if sub.attr in MARKER_NAMES:
            return True
        if isinstance(sub.value, ast.Name) and sub.value.id in imports.modules:
            return sub.attr in exported.get(imports.modules[sub.value.id], frozenset())
    return False


def _marker_names(
    tree: ast.Module, imports: _Module, exported: dict[str, frozenset[str]]
) -> frozenset[str]:
    """The names a module reads the contract file by: CONTRACT and CONTRACT_FILE under any name,
    and any name imported from a module that binds it to the file."""
    imported = {
        local
        for local, (source, name) in imports.names.items()
        if name in exported.get(source, frozenset())
    }
    return MARKER_NAMES | _marker_aliases(tree) | imported


def _exported_markers(
    trees: dict[Path, ast.Module], imports: dict[Path, _Module]
) -> dict[str, frozenset[str]]:
    """Each module's module-level names bound to the contract file, or to an expression holding
    it, found to a fixed point across modules: a name bound to one imported from elsewhere is
    one too (change review r2, plant N1)."""
    exported: dict[str, frozenset[str]] = {}
    docs = {path: _docstrings(tree) for path, tree in trees.items()}
    owns = {path: _own_nodes(tree) for path, tree in trees.items()}
    while True:
        found: dict[str, frozenset[str]] = {}
        for path, tree in trees.items():
            module_imports = imports[path]
            names = _marker_names(tree, module_imports, exported)

            def seed(
                sub: ast.AST,
                path: Path = path,
                names: frozenset[str] = names,
                module_imports: _Module = module_imports,
            ) -> bool:
                return _names_the_file(sub, docs[path], names, module_imports, exported)

            # A name bound to a marker by an import (from .contract import CONTRACT_FILE as
            # CONTRACT_JSON) is one too, as W7 says (change review r3, B-M1).
            found[module_imports.name] = frozenset(
                _bound(owns[path], set(), seed) | (names - MARKER_NAMES)
            )
        if found == exported:
            return exported
        exported = found


def scan_contract_writers(root: Path) -> dict[tuple[str, str, str], int]:
    trees = {path: ast.parse(path.read_text(encoding="utf-8")) for path in sorted(root.rglob("*.py"))}
    package = frozenset(_module_name(path, root) for path in trees)
    imports = {path: _context(tree, path, root, package) for path, tree in trees.items()}
    helpers = _writer_helpers(trees, imports)
    exported = _exported_markers(trees, imports)
    hits: dict[tuple[str, str, str], int] = {}
    for path, tree in trees.items():
        docs = _docstrings(tree)
        module = ".".join(path.relative_to(root.parent.parent).with_suffix("").parts)
        module_imports = imports[path]
        marker_names = _marker_names(tree, module_imports, exported)

        def names_it(
            sub: ast.AST,
            docs: set[int] = docs,
            marker_names: frozenset[str] = marker_names,
            module_imports: _Module = module_imports,
        ) -> bool:
            return _names_the_file(sub, docs, marker_names, module_imports, exported)

        # The constants each class of this module binds to the file in its body, read as
        # Names.CONTRACT_JSON (change review r3).
        constants = {
            node.name: frozenset(_bound(_own_nodes(node), set(), names_it))
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
        }

        def is_marker(
            sub: ast.AST,
            attrs: frozenset[str] = frozenset(),
            names_it: Any = names_it,
            constants: dict[str, frozenset[str]] = constants,
        ) -> bool:
            if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name):
                if sub.value.id in _SELF and sub.attr in attrs:
                    return True
                if sub.attr in constants.get(sub.value.id, frozenset()):
                    return True
            return names_it(sub)

        def record(
            qualified: str,
            own: list[ast.AST],
            names: set[str],
            attrs: frozenset[str],
            module: str = module,
            module_imports: _Module = module_imports,
            is_marker: Any = is_marker,
        ) -> None:
            mentions = any(
                is_marker(sub, attrs) or (isinstance(sub, ast.Name) and sub.id in names)
                for sub in own
            )
            if not mentions:
                return
            verbs: Counter[str] = Counter()
            for sub in own:
                found = _verb(sub, helpers, module_imports) if isinstance(sub, ast.Call) else None
                if found:
                    verbs[found[0]] += 1
            for verb, count in verbs.items():
                hits[(module, qualified, verb)] = count

        def visit(
            node: ast.AST,
            marked: set[str],
            prefix: str,
            attrs: frozenset[str],
            is_marker: Any = is_marker,
            record: Any = record,
        ) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, _DEFS):
                    own = _own_nodes(child)
                    names = _bound(own, marked, lambda sub, attrs=attrs: is_marker(sub, attrs))
                    qualified = prefix + child.name
                    record(qualified, own, names, attrs)
                    visit(child, names, qualified + ".", attrs)
                elif isinstance(child, ast.ClassDef):
                    held = _self_attrs(child, marked, is_marker)
                    own = _own_nodes(child)
                    names = _bound(own, marked, lambda sub, held=held: is_marker(sub, held))
                    record(prefix + child.name + ".<body>", own, names, held)
                    visit(child, marked, prefix + child.name + ".", held)
                else:
                    visit(child, marked, prefix, attrs)

        own = _own_nodes(tree)
        marked = _bound(own, set(), is_marker)
        record("<module>", own, marked, frozenset())
        visit(tree, marked, "", frozenset())
    return hits


_COPIES = "copies the contract.json write_contract wrote into a bundle, as written"
_READS = "reads contract.json; the write is another file"
_FAILURE = "writes the reading's failure file (failure_file.write_failure_file)"
_TEMP_COMPOSE = (
    "reaches runtime_selection.select_runtime, whose compose_merge.merge_compose writes compose "
    "configs into a temporary directory"
)
_TRACE = "run.conversation.converse writes http-failure.json beside the run's trace"
# (module, function, verb): (count, reason). Taken from reviewer A's probe
# (plan-v5-r1/copy-A/probes-v5A/scan_v6_strict.py), run again on the fixed tree with W7's rule.
ALLOWED: dict[tuple[str, str, str], tuple[int, str]] = {
    ("fi.alk.harness.contract", "write_contract", "write_text"): (1, "the writer itself"),
    ("fi.alk.harness.contract", "write_contract", "os.replace"): (1, "the writer itself"),
    # Readers: each names contract.json to read it, and writes something else.
    ("fi.alk.harness.agent_reading", "_read", "document"): (
        1, "compares the saved file's name; reading_result.document writes agent.json"),
    ("fi.alk.harness.agent_reading", "_reading", "document"): (
        2, "compares the saved file's name; reading_result.document writes agent.json"),
    ("fi.alk.harness.reading_result", "classify", "document"): (
        1, "compares the saved file's name; document writes agent.json"),
    ("fi.alk.harness.reading_result", "document", "_write_whole"): (
        1, "reads contract.json; writes agent.json through build_authored_agent's _redact"),
    # Copies: each writes contract.json from bytes an earlier save already wrote.
    ("fi.alk.harness.reading_result", "document", "replace_if_unchanged"): (
        1, "rewrites the saved contract's own bytes with the reading's record (a technical key) "
        "spliced in; the free text is the saved, cleaned text"),
    ("fi.alk.harness.seed_contract", "use_seed", "_write_whole"): (
        2, "installs the platform's seed, a reading's saved contract, as written; a reading from "
        "before the cleaner stays as written (a named limit), and every send cleans again"),
    ("fi.alk.harness.authoring_entrypoint", "_prepare_source_data_invariants",
     "prune_invariant_scopes"): (1, _READS + " (the invariants file)"),
    ("fi.alk.harness.authoring_runtime_validation", "validate_and_repair",
     "_write_generic_certificate"): (1, _READS + " (the certificate)"),
    ("fi.alk.harness.authoring_runtime_validation", "validate_and_repair", "write_text"): (
        3, _READS + " (diagnostics and runtime-validation records)"),
    ("fi.alk.harness.authoring_runtime_validation", "validate_once",
     "_freeze_certified_bundle"): (2, _READS + " (the frozen bundle)"),
    ("fi.alk.harness.authoring_runtime_validation", "validate_once",
     "_probe_runtime_readiness"): (1, _READS + " (the readiness record)"),
    ("fi.alk.harness.authoring_runtime_validation", "validate_once", "author_invariants"): (
        1, _READS + " (the invariants file)"),
    ("fi.alk.harness.authoring_runtime_validation", "validate_once", "probe_chat_ingress"): (
        1, _READS + " (the ingress probe record)"),
    ("fi.alk.harness.authoring_runtime_validation", "validate_once", "probe_chat_workflow"): (
        1, _READS + " (the workflow probe record)"),
    ("fi.alk.harness.authoring_runtime_validation", "validate_once", "validate_native"): (
        1, _READS + " (the native validation record)"),
    ("fi.alk.harness.authoring_runtime_validation", "validate_once", "write_bytes"): (
        1, _READS + " (a validation record)"),
    ("fi.alk.harness.authoring_runtime_validation", "validate_once", "write_text"): (
        2, _READS + " (validation records)"),
    ("fi.alk.harness.bundle_author_v2", "_reuse_certified_bundle", "Path.rename"): (
        3, _READS + " (its digest); moves bundle folders"),
    ("fi.alk.harness.bundle_author_v2", "_reuse_certified_bundle", "shutil.copy2"): (
        1, _READS + " (its digest); copies another artifact"),
    ("fi.alk.harness.bundle_author_v2", "_reuse_certified_bundle", "shutil.copytree"): (
        1, _READS + " (its digest); copies a bundle folder"),
    ("fi.alk.harness.cli", "_auto", "_write_adjustment_status"): (2, _READS + " (the status)"),
    ("fi.alk.harness.cli", "_auto", "seal_artifacts"): (1, _READS + " (the seal)"),
    ("fi.alk.harness.cli", "_auto", "write_text"): (1, _READS + " (job.json)"),
    ("fi.alk.harness.compose_integration", "validate_native", "_freeze_certified_bundle"): (
        1, _READS + " (the frozen bundle)"),
    ("fi.alk.harness.compose_integration", "validate_native", "_probe_runtime_readiness"): (
        1, _READS + " (the readiness record)"),
    ("fi.alk.harness.compose_integration", "validate_native", "probe_chat_ingress"): (
        1, _READS + " (the ingress probe record)"),
    ("fi.alk.harness.compose_integration", "validate_native", "write_text"): (
        1, _READS + " (the native validation record)"),
    ("fi.alk.harness.sandbox_server", "LocalSandbox.rerun", "_write_json"): (
        1, "checks contract.json is there; writes the job state"),
    ("fi.alk.harness.source_data_invariants", "author_invariants", "write_text"): (
        2, _READS + " (the invariants file)"),
    # Copies: each carries the authoring contract.json, already cleaned, into a bundle.
    ("fi.alk.harness.bundle", "export_session_bundle", "Path.replace"): (1, _COPIES),
    ("fi.alk.harness.bundle", "export_session_bundle", "_copy_generated_runtime_context"): (
        1, _COPIES),
    ("fi.alk.harness.bundle", "export_session_bundle", "seal_bundle"): (1, _COPIES),
    ("fi.alk.harness.bundle", "export_session_bundle", "shutil.copy2"): (2, _COPIES),
    ("fi.alk.harness.bundle", "export_session_bundle", "write_environment_plan"): (1, _COPIES),
    ("fi.alk.harness.bundle", "export_session_bundle", "write_text"): (1, _COPIES),
    ("fi.alk.harness.bundle_author_v2", "_carry_contract", "_compile_source_tool_handlers"): (
        1, _COPIES),
    ("fi.alk.harness.bundle_author_v2", "_carry_contract", "shutil.copy2"): (1, _COPIES),
    ("fi.alk.harness.bundle_author_v2", "_carry_contract", "shutil.copytree"): (1, _COPIES),
    ("fi.alk.harness.bundle_author_v2", "_carry_contract", "write_text"): (
        1, _COPIES + ", with only runtime.interface (kept) set from the build"),
    ("fi.alk.harness.bundle_author_v2", "_copy_chat_authoring", "shutil.copy2"): (2, _COPIES),
    ("fi.alk.harness.bundle_author_v2", "_copy_chat_authoring", "shutil.copytree"): (1, _COPIES),
    ("fi.alk.harness.bundle_author_v2", "author_bundle_v2", "Path.rename"): (3, _COPIES),
    ("fi.alk.harness.bundle_author_v2", "author_bundle_v2", "_compile_source_tool_handlers"): (
        1, _COPIES),
    ("fi.alk.harness.bundle_author_v2", "author_bundle_v2", "_copy_chat_authoring"): (1, _COPIES),
    ("fi.alk.harness.bundle_author_v2", "author_bundle_v2", "_copy_scenarios"): (1, _COPIES),
    ("fi.alk.harness.bundle_author_v2", "author_bundle_v2", "_copy_sub_goal_catalogue"): (
        1, _COPIES),
    ("fi.alk.harness.bundle_author_v2", "author_bundle_v2", "_generic_postgres_seed_artifacts"): (
        1, _COPIES),
    ("fi.alk.harness.bundle_author_v2", "author_bundle_v2", "_reuse_certified_bundle"): (
        1, _COPIES),
    ("fi.alk.harness.bundle_author_v2", "author_bundle_v2", "compile_bundle"): (1, _COPIES),
    ("fi.alk.harness.bundle_author_v2", "author_bundle_v2", "shutil.copy2"): (3, _COPIES),
    ("fi.alk.harness.bundle_author_v2", "author_bundle_v2", "write_text"): (
        5, _COPIES + " (:3752 writes the parsed authoring body back as written)"),
    ("fi.alk.harness.bundle_author_v2", "reseal_scenarios", "Path.rename"): (2, _COPIES),
    ("fi.alk.harness.bundle_author_v2", "reseal_scenarios", "_carry_contract"): (1, _COPIES),
    ("fi.alk.harness.bundle_author_v2", "reseal_scenarios", "_copy_scenarios"): (1, _COPIES),
    ("fi.alk.harness.bundle_author_v2", "reseal_scenarios", "_recertify_scenarios"): (1, _COPIES),
    ("fi.alk.harness.bundle_author_v2", "reseal_scenarios", "shutil.copy2"): (1, _COPIES),
    ("fi.alk.harness.bundle_author_v2", "reseal_scenarios", "shutil.copytree"): (1, _COPIES),
    ("fi.alk.harness.bundle_author_v2", "reseal_scenarios", "write_text"): (1, _COPIES),
    ("fi.alk.harness.compose_integration", "compile_bundle", "Path.rename"): (1, _COPIES),
    ("fi.alk.harness.compose_integration", "compile_bundle", "_compile_source_tool_handlers"): (
        1, _COPIES),
    ("fi.alk.harness.compose_integration", "compile_bundle", "_copy_chat_authoring"): (1, _COPIES),
    ("fi.alk.harness.compose_integration", "compile_bundle", "_copy_scenarios"): (1, _COPIES),
    ("fi.alk.harness.compose_integration", "compile_bundle", "_copy_sub_goal_catalogue"): (
        1, _COPIES),
    ("fi.alk.harness.compose_integration", "compile_bundle", "write_text"): (4, _COPIES),
    # Found by the widened scan (change review r1, A-M1): calls to a helper that writes to what
    # it is given, found to a fixed point. None of these helpers names contract.json (one that
    # did would be listed under its own name), so each reason says what the helper writes.
    ("fi.alk.harness.agent_reading", "_read", "_reading"): (1, "calls _reading, listed below"),
    ("fi.alk.harness.agent_reading", "_reading", "_timed_out"): (1, _FAILURE),
    ("fi.alk.harness.agent_reading", "_reading", "classify"): (
        1, _FAILURE + "; classify's document writes agent.json"),
    ("fi.alk.harness.agent_reading", "_reading", "model_error"): (1, _FAILURE),
    ("fi.alk.harness.agent_reading", "_reading", "refuse_unusable_folder"): (1, _FAILURE),
    ("fi.alk.harness.agent_reading", "_reading", "write_reading_failure"): (1, _FAILURE),
    ("fi.alk.harness.authoring_runtime_validation", "validate_and_repair",
     "apply_runtime_plan_patch"): (
        1, "the runtime-plan repair, which saves through write_contract (B13); " + _TEMP_COMPOSE),
    ("fi.alk.harness.authoring_runtime_validation", "validate_and_repair", "repair"): (
        3, "calls its nested repair, scanned as validate_and_repair.repair"),
    ("fi.alk.harness.authoring_runtime_validation", "validate_and_repair", "validate"): (
        2, "calls its nested validate, which runs validate_once (listed above)"),
    ("fi.alk.harness.authoring_runtime_validation", "validate_and_repair", "write_scenarios"): (
        1, "writes the scenario folders (folder.write_folder and write_index)"),
    ("fi.alk.harness.authoring_runtime_validation", "validate_and_repair.repair", "_build"): (
        1, "the build stage (cli._build), which saves the contract through tools._save_contract "
        "and amend, both write_contract (B13); it writes the world besides"),
    ("fi.alk.harness.authoring_runtime_validation", "validate_and_repair.repair", "_scenarios"): (
        1, "the scenario stage (cli._scenarios), which writes the scenario folders"),
    ("fi.alk.harness.authoring_runtime_validation", "validate_and_repair.repair",
     "request_runtime_plan_patch"): (1, "builds a runtime patch; " + _TEMP_COMPOSE),
    ("fi.alk.harness.bundle_author_v2", "<module>", "main"): (
        1, "the __main__ guard; main runs author_bundle_v2 (listed) and refuse_unusable_folder "
        "(the failure file)"),
    ("fi.alk.harness.bundle_author_v2", "author_bundle_v2", "_adopted_seed_sql"): (
        1, "reads the agent's schema files; " + _TEMP_COMPOSE),
    ("fi.alk.harness.bundle_author_v2", "author_bundle_v2", "_package_process_sources"): (
        1, "copies the agent's own source components into the bundle "
        "(generated_runtime._copy_sanitized_context)"),
    ("fi.alk.harness.bundle_author_v2", "author_bundle_v2", "resolve_environment_plan"): (
        1, _TEMP_COMPOSE),
    ("fi.alk.harness.bundle_author_v2", "reseal_scenarios", "_reseeded"): (
        1, "_carried_world writes the world seed (world.sqlite, world-ir.json)"),
    ("fi.alk.harness.chat_call_runner", "HostedChatCallRunner.run", "_drive_conversation"): (
        1, _TRACE),
    ("fi.alk.harness.cli", "_auto", "save_session"): (
        1, "sessions.save writes the session's own record (META)"),
    ("fi.alk.harness.compose_integration", "compile_bundle", "select_runtime"): (
        1, _TEMP_COMPOSE),
    ("fi.alk.harness.reading_result", "classify", "model_error"): (2, _FAILURE),
    ("fi.alk.harness.reading_result", "classify", "write_reading_failure"): (1, _FAILURE),
    ("fi.alk.harness.reading_result", "document", "write_reading_failure"): (2, _FAILURE),
    ("fi.alk.harness.retell_chat_call_runner", "RetellChatCallRunner.run",
     "_drive_conversation"): (1, _TRACE),
    ("fi.alk.harness.sandbox_server", "LocalSandbox.rerun", "_claim_secret_files"): (
        1, "moves the job's pending secret files into the job's folder"),
    ("fi.alk.harness.sandbox_server", "LocalSandbox.rerun", "_execute_rerun"): (
        1, "writes the job state and worker.log, and copies the environment bundle as written"),
    ("fi.alk.harness.tools", "contract_tools.submit_contract", "write_failure_file"): (
        1, "writes the stage's failure file, not the contract"),
}


def test_no_contract_write_outside_write_contract_and_the_listed_readers_and_copies() -> None:
    found = scan_contract_writers(SOURCE_TREE)
    assert found == {key: count for key, (count, _) in ALLOWED.items()}
    assert all(reason for _, reason in ALLOWED.values())


def test_the_scan_flags_each_writer_shape_the_plan_names(tmp_path: Path) -> None:
    """P12b and P12c as fixtures, plus the W7 verbs, so the scan is seen to catch each."""
    package = tmp_path / "fi" / "alk"
    package.mkdir(parents=True)
    (package / "shapes.py").write_text(
        '''
import json, os, shutil
CONTRACT_FILE = "contract.json"


def _write_json(path, payload):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload))
    temporary.replace(path)


def like_save_contract(contract, destination):
    path = destination / "contract.json"
    partial = destination / ".contract.json.partial"
    partial.write_text(contract.model_dump_json())
    os.replace(partial, path)


def through_a_helper(run_root, body):
    _write_json(run_root / "contract.json", body)


def opened(destination, body):
    with (destination / CONTRACT_FILE).open("w") as stream:
        json.dump(body, stream)


def moved(staging, destination):
    shutil.move(staging / "contract.json", destination)


def renamed(staging, destination):
    contract = staging / "contract.json"
    contract.rename(destination)


def copied(staging, destination):
    shutil.copytree(staging, destination / "bundle")
    return staging / CONTRACT_FILE


def a_reader(destination):
    """Writes nothing, though its docstring says contract.json and it calls replace."""
    return (destination / "contract.json").read_text().replace("a", "b")
''',
        encoding="utf-8",
    )

    found = scan_contract_writers(package)

    module = "fi.alk.shapes"
    assert found == {
        (module, "like_save_contract", "write_text"): 1,
        (module, "like_save_contract", "os.replace"): 1,
        (module, "through_a_helper", "_write_json"): 1,
        (module, "opened", "Path.open"): 1,
        (module, "opened", "json.dump"): 1,
        (module, "moved", "shutil.move"): 1,
        (module, "renamed", "Path.rename"): 1,
        (module, "copied", "shutil.copytree"): 1,
    }


def test_the_scan_flags_each_shape_the_change_review_found_it_missed(tmp_path: Path) -> None:
    """Change review r1, A-M1: the nine writer shapes the one-level scan missed, one each."""
    package = tmp_path / "fi" / "alk"
    package.mkdir(parents=True)
    (package / "missed.py").write_text(
        '''
import os
from shutil import copyfile
from .contract import CONTRACT_FILE as _NAME


class Held:
    """1. The path is held on self, and written in another method."""

    def __init__(self, root):
        self.path = root / "contract.json"

    def save(self, body):
        self.path.write_text(body)


def _write_json(path, body):
    path.write_text(body)


def _save_state_file(path, body):
    _write_json(path, body)


def two_levels(run_root, body):
    """2. A helper two levels deep."""
    _save_state_file(run_root / "contract.json", body)


def in_a_lambda(destination, body):
    """3. The write is inside a lambda."""
    target = destination / "contract.json"
    put = lambda: target.write_text(body)
    put()


def aliased(destination, body):
    """4. CONTRACT_FILE imported under another name."""
    (destination / _NAME).write_text(body)


def imported_copy(staging, destination):
    """5. copyfile imported from shutil."""
    copyfile(staging / "contract.json", destination)


def one_constant(root, body):
    """6. The file named at the end of a path."""
    (root / "authoring/contract.json").write_text(body)


def mode_in_a_variable(destination, body, mode):
    """7. The open mode is held in a variable."""
    with open(destination / "contract.json", mode) as stream:
        stream.write(body)


class Store:
    def _write(self, path, body):
        path.write_text(body)

    def keep(self, root, body):
        """8. A method helper on self."""
        self._write(root / "contract.json", body)


def linked(destination, body):
    """9. os.open and os.fdopen, then os.link into place."""
    partial = destination / ".partial"
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(body)
    os.link(partial, destination / "contract.json")


def read_only(destination):
    """Not a writer: it reads the file, through os.open and an open with a read mode."""
    descriptor = os.open(destination / "contract.json", os.O_RDONLY)
    os.close(descriptor)
    with open(destination / "contract.json", "r") as stream:
        return stream.read()
''',
        encoding="utf-8",
    )

    found = scan_contract_writers(package)

    module = "fi.alk.missed"
    assert found == {
        (module, "Held.save", "write_text"): 1,
        (module, "two_levels", "_save_state_file"): 1,
        (module, "in_a_lambda", "write_text"): 1,
        (module, "aliased", "write_text"): 1,
        (module, "imported_copy", "shutil.copyfile"): 1,
        (module, "one_constant", "write_text"): 1,
        (module, "mode_in_a_variable", "open"): 1,
        (module, "Store.keep", "_write"): 1,
        (module, "linked", "os.open"): 1,
        (module, "linked", "os.fdopen"): 1,
        (module, "linked", "os.link"): 1,
    }


def test_the_scan_flags_each_shape_the_second_change_review_found_it_missed(tmp_path: Path) -> None:
    """Change review r2, A-M1: plants N1 and N2 as fixtures, and the archive writes."""
    package = tmp_path / "fi" / "alk"
    package.mkdir(parents=True)
    (package / "names.py").write_text(
        'CONTRACT_NAME = "contract.json"\nKEPT_AS = CONTRACT_NAME\n', encoding="utf-8"
    )
    (package / "user.py").write_text(
        '''
import tarfile
import zipfile
from . import names
from .names import CONTRACT_NAME as _OTHER, KEPT_AS


def imported_under_another_name(run_root, body):
    """N1. A module constant bound to the file, imported under another name."""
    (run_root / _OTHER).write_text(body)


def through_a_second_binding(run_root, body):
    (run_root / KEPT_AS).write_text(body)


def read_as_module_name(run_root, body):
    (run_root / names.CONTRACT_NAME).write_text(body)


class _RawKeeper:
    def save(self, path, body):
        path.write_text(body)


def on_an_instance(run_root, body):
    """N2. A helper method called on an instance of a class defined here."""
    _RawKeeper().save(run_root / "contract.json", body)


def on_a_bound_instance(run_root, body):
    keeper = _RawKeeper()
    keeper.save(run_root / "contract.json", body)


def zipped(bundle, body):
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("contract.json", body)


def tarred(bundle, stream):
    with tarfile.open(bundle, "w") as archive:
        archive.addfile(tarfile.TarInfo("contract.json"), stream)
''',
        encoding="utf-8",
    )

    found = scan_contract_writers(package)

    module = "fi.alk.user"
    assert found == {
        (module, "imported_under_another_name", "write_text"): 1,
        (module, "through_a_second_binding", "write_text"): 1,
        (module, "read_as_module_name", "write_text"): 1,
        (module, "on_an_instance", "save"): 1,
        (module, "on_a_bound_instance", "save"): 1,
        (module, "zipped", "writestr"): 1,
        (module, "tarred", "Path.open"): 1,
        (module, "tarred", "addfile"): 1,
    }



# Reviewer B's three real-tree plants of change review r3, as B wrote them, each a new module.
_R3_PLANTS = {
    "r3a_constructor": (
        '"""planted"""\nimport json\n\n\nclass JsonFile:\n    def __init__(self, path):\n'
        "        self.path = path\n\n    def save(self, body):\n"
        "        self.path.write_text(json.dumps(body))\n\n\n"
        "def keep_raw_contract(run_root, body):\n"
        '    JsonFile(run_root / "contract.json").save(body)\n'
    ),
    "r3b_constant": (
        '"""planted"""\nimport json\n\n\nclass Names:\n    CONTRACT_JSON = "contract.json"\n\n\n'
        "def keep_raw_contract(run_root, body):\n"
        "    (run_root / Names.CONTRACT_JSON).write_text(json.dumps(body))\n"
    ),
    "r3c_static": (
        '"""planted"""\nimport json\n\n\nclass Store:\n    @staticmethod\n'
        "    def keep(path, body):\n        path.write_text(json.dumps(body))\n\n\n"
        "def keep_raw_contract(run_root, body):\n"
        '    Store.keep(run_root / "contract.json", body)\n'
    ),
}


def test_the_scan_flags_each_shape_the_third_change_review_found_it_missed(tmp_path: Path) -> None:
    """Change review r3, B-M1: B's plants R3b and R3c, a class method on its class, and a marker
    re-exported through a module by ``import ... as`` (W7's "a name bound to one of them").

    R3a, a path handed to a constructor and written by a method, is a named limit (the comment
    above MARKER_NAMES): the exact result pins that it is still missed."""
    package = tmp_path / "fi" / "alk"
    package.mkdir(parents=True)
    for name, code in _R3_PLANTS.items():
        (package / f"{name}.py").write_text(code, encoding="utf-8")
    (package / "contract.py").write_text('CONTRACT_FILE = "contract.json"\n', encoding="utf-8")
    (package / "relay.py").write_text(
        "from .contract import CONTRACT_FILE as CONTRACT_JSON\n", encoding="utf-8"
    )
    (package / "user.py").write_text(
        '''
from .relay import CONTRACT_JSON


def through_a_re_export(out, body):
    (out / CONTRACT_JSON).write_text(body)


class Store:
    @classmethod
    def keep(cls, path, body):
        path.write_text(body)


def through_a_class_method(out, body):
    Store.keep(out / "contract.json", body)
''',
        encoding="utf-8",
    )

    found = scan_contract_writers(package)

    assert found == {
        ("fi.alk.r3b_constant", "keep_raw_contract", "write_text"): 1,
        ("fi.alk.r3c_static", "keep_raw_contract", "keep"): 1,
        ("fi.alk.user", "through_a_re_export", "write_text"): 1,
        ("fi.alk.user", "through_a_class_method", "keep"): 1,
    }

# -- B14, W6: the rule checks compare cleaned text ---------------------------------------------------

RULE = f"Always post to {URL}"


def test_add_rule_refuses_a_rule_whose_cleaned_form_is_saved(tmp_path: Path) -> None:
    held = _amendable()
    assert amend.add_rule(held, tmp_path, rule=RULE, why="asked")[0]

    added, said = amend.add_rule(held, tmp_path, rule=RULE, why="asked again")

    assert not added and "already has that rule" in said
    assert held.hard_constraints.count(f"Always post to {CLEAN}") == 1


def test_drop_rule_finds_a_rule_given_as_the_operator_wrote_it(tmp_path: Path) -> None:
    held = _amendable()
    amend.add_rule(held, tmp_path, rule=RULE, why="asked")

    dropped, said = amend.drop_rule(held, tmp_path, rule=RULE, why="not the agent's")

    assert dropped, said
    assert f"Always post to {CLEAN}" not in held.hard_constraints


def _held_raw() -> AgentContract:
    """A contract held with its rule raw, as one saved before this fix is loaded."""
    held = _amendable()
    held.hard_constraints.append(RULE)
    return held


@pytest.mark.parametrize("given", [RULE, f"Always post to {CLEAN}"], ids=["raw", "cleaned"])
def test_add_rule_refuses_a_rule_a_raw_held_contract_already_has(
    tmp_path: Path, given: str
) -> None:
    """Change review r1, A-L1."""
    held = _held_raw()

    added, said = amend.add_rule(held, tmp_path, rule=given, why="asked again")

    assert not added and "already has that rule" in said
    assert held.hard_constraints == ["Confirm before booking", RULE]
    assert not (tmp_path / "contract.json").exists()


@pytest.mark.parametrize(
    "given", [RULE, f"Always post to {CLEAN}", "post to https://hooks.example"],
    ids=["raw", "cleaned", "part"],
)
def test_drop_rule_finds_a_rule_a_raw_held_contract_holds(tmp_path: Path, given: str) -> None:
    """Change review r1, A-L1: the raw rule is the one removed, and the save holds no password."""
    held = _held_raw()

    dropped, said = amend.drop_rule(held, tmp_path, rule=given, why="not the agent's")

    assert dropped, said
    assert held.hard_constraints == ["Confirm before booking"]
    saved = (tmp_path / "contract.json").read_text(encoding="utf-8")
    assert PASSWORD not in saved and KEY not in saved
    assert json.loads(saved)["hard_constraints"] == ["Confirm before booking"]


def test_add_rule_names_the_rule_it_refuses_as_saved(tmp_path: Path) -> None:
    """Change review r2, A-H1: the hosted chat sends this reply on to the platform."""
    held = _held_raw()

    added, said = amend.add_rule(held, tmp_path, rule=RULE, why="asked again")

    assert not added
    assert said == f"the agent already has that rule: Always post to {CLEAN}"


def test_drop_rule_lists_the_rules_it_has_as_saved(tmp_path: Path) -> None:
    """Change review r2, A-H1: the reply lists the held rules cleaned, in their own case."""
    held = _held_raw()

    dropped, said = amend.drop_rule(held, tmp_path, rule="never rings", why="not the agent's")

    assert not dropped
    assert said == (
        "no rule like that. It has:\n  - Confirm before booking\n"
        f"  - Always post to {CLEAN}"
    )
    assert held.hard_constraints == ["Confirm before booking", RULE]


# -- B15, W6: the caller's own object is cleaned -----------------------------------------------------


def test_add_rule_cleans_the_contract_the_stage_holds(tmp_path: Path) -> None:
    held = _amendable()
    rules = held.hard_constraints

    assert amend.add_rule(held, tmp_path, rule=RULE, why=f"see {URL}")[0]

    assert held.hard_constraints is rules
    assert _secret_free(held.model_dump()) and _secret_free(held.brief())
    assert f"Always post to {CLEAN}" in held.brief()


def test_fix_tool_cleans_the_contract_the_stage_holds(tmp_path: Path) -> None:
    held = _amendable()
    tool = held.tools[0]

    assert amend.fix_tool(held, tmp_path, tool_name="post", why="w", description=PROSE)[0]

    assert held.tools[0] is tool and tool.description == CLEANED
    assert _secret_free(held.model_dump()) and _secret_free(held.brief())


def test_declare_tool_cleans_the_contract_the_builder_holds(tmp_path: Path) -> None:
    held = _amendable()

    result = _declare_tool(tmp_path, held, description=PROSE)

    assert not result.get("is_error"), result
    assert held.tools[-1].description == CLEANED
    assert _secret_free(held.model_dump()) and _secret_free(held.brief())
    saved = (tmp_path / "out" / "contract.json").read_text(encoding="utf-8")
    assert PASSWORD not in saved and KEY not in saved


# -- W7's canary: the amend chain leaves no password on disk -----------------------------------------


def test_no_file_the_amend_chain_writes_holds_the_password(tmp_path: Path) -> None:
    output = tmp_path / "authoring"
    accepted = tools.accept_contract(
        {
            "agent": "desk",
            "modality": "chat",
            "one_liner": f"Books desks via {URL}",
            "system_prompt_excerpt": PROSE,
            "hard_constraints": [RULE],
            "real_use_cases": ["book a desk"],
            "notes": PROSE,
            "tools": [
                {"name": "post", "args": ["kind"], "arg_values": {"kind": ["a"]},
                 "description": PROSE},
            ],
        },
        output,
    )
    assert not accepted.get("is_error"), accepted
    held = AgentContract.model_validate_json((output / "contract.json").read_text("utf-8"))
    amend.add_rule(held, output, rule=f"Never call {URL} twice", why=f"from {URL}")
    amend.fix_tool(held, output, tool_name="post", why=URL, refuses=PROSE)
    amend.widen(held, output, tool_name="post", argument="kind", values=["b"], why=URL)
    amend.set_modality(held, output, modality="voice", why=URL)
    _declare_tool(tmp_path, held, description=PROSE)

    written = [path for path in tmp_path.rglob("*") if path.is_file() and path.name != "agent.py"]
    assert {path.name for path in written} == {"contract.json"}
    for path in written:
        text = path.read_text(encoding="utf-8")
        assert PASSWORD not in text and KEY not in text, path
