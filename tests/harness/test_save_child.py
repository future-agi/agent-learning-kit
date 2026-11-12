"""The save checks' child, driven in this process over a real pipe.

``serve`` runs on a thread here rather than in a forked child, so each row can patch what the
child runs and read every message it sends, in the order it sent them.
"""

from __future__ import annotations

import ast
import contextlib
import logging
import multiprocessing
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

import pytest

from fi.alk.harness import (
    agent_type_check,
    forkserver_preload,
    instrumentation_evidence,
    save_child,
    scan_folders,
    tools,
)
from fi.alk.harness.contract import AgentContract

_WAIT = 30.0
_TERMINAL = {"trial", "stand_in", "done"}


@contextlib.contextmanager
def _child(*, install_logging: bool = False):
    parent, child = multiprocessing.Pipe()
    thread = threading.Thread(
        target=save_child.serve,
        args=(child,),
        kwargs={"install_logging": install_logging},
        daemon=True,
    )
    thread.start()
    try:
        yield parent
    finally:
        # The child reads end of file and returns.
        parent.close()
        thread.join(10)
        child.close()
        assert not thread.is_alive()


def _read(parent, *, count: int | None = None, until=None) -> list[dict[str, Any]]:
    """Messages, in order, until ``until(message)`` is true or ``count`` arrived."""
    got: list[dict[str, Any]] = []
    while True:
        assert parent.poll(_WAIT), f"the child stopped answering after {got}"
        message = parent.recv()
        got.append(message)
        if until is not None and until(message):
            return got
        if count is not None and len(got) == count:
            return got


def _up(parent) -> dict[str, Any]:
    return _read(parent, count=1)[0]


def _answered(rid: int):
    return lambda message: message["id"] == rid and message["kind"] in _TERMINAL | {"failed"}


_SAME = object()


def _session(parent, rid: int, root: Path | None, subfolder: str | None, *, scan_root=_SAME):
    """Sends a session request and reads until both parts have answered."""
    source = str(root) if root is not None else None
    parent.send(
        {
            "id": rid,
            "kind": "session",
            "scan_root": source if scan_root is _SAME else scan_root,
            "source_root": source,
            "subfolder": subfolder,
            "log_level": logging.WARNING,
        }
    )
    got: list[dict[str, Any]] = []
    parts = 0
    while parts < 2:
        message = _read(parent, count=1)[0]
        got.append(message)
        if message["kind"] in {"scan", "instrumentation"} or (
            message["kind"] == "failed" and message["part"] in {"scan", "instrumentation"}
        ):
            parts += 1
    return got


def _save_request(rid: int, root: Path, payload: dict[str, Any], **changes: Any):
    request = {
        "id": rid,
        "kind": "save",
        "payload": payload,
        "source_root": str(root),
        "subfolder": save_child.TRIAL_SUBFOLDER,
        "require_setup_contract": False,
        "instrumentation": "traceai",
        "asked": [],
        "held": {},
    }
    request.update(changes)
    return request


def _write(root: Path, tree: dict[str, str]) -> Path:
    for name, text in tree.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _kinds(messages: list[dict[str, Any]]) -> list[str]:
    return [m["kind"] for m in messages if m["kind"] != "log"]


@pytest.fixture
def trial_dir(tmp_path, monkeypatch):
    """The trial's temporary folder lands here, so a row can see that it is gone."""
    made = tmp_path / "temp"
    made.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(made))
    return made


# P0, P1


def test_up_comes_first_with_the_childs_pid():
    with _child() as parent:
        assert _up(parent) == {"id": None, "kind": "up", "pid": os.getpid()}


def test_two_requests_are_answered_in_the_order_they_came():
    with _child() as parent:
        _up(parent)
        parent.send({"id": 1, "kind": "stand_in", "module": "no_such_stand_in_module"})
        parent.send(
            {
                "id": 2,
                "kind": "session",
                "scan_root": None,
                "source_root": None,
                "subfolder": None,
                "log_level": logging.WARNING,
            }
        )
        got = _read(parent, count=3)
    assert [(m["id"], m["kind"]) for m in got] == [
        (1, "stand_in"),
        (2, "instrumentation"),
        (2, "scan"),
    ]
    assert got[1]["value"] == "none"
    assert got[2] == {"id": 2, "kind": "scan", "found": [], "cut": []}


def test_the_loop_returns_when_the_parents_end_closes():
    parent, child = multiprocessing.Pipe()
    thread = threading.Thread(target=save_child.serve, args=(child,), kwargs={"install_logging": False})
    thread.start()
    _up(parent)
    parent.close()
    thread.join(10)
    child.close()
    assert not thread.is_alive()


# P2


def test_the_trial_passes_on_the_real_checks_and_sends_only_its_answer(trial_dir):
    with _child() as parent:
        _up(parent)
        parent.send({"id": 7, "kind": "trial"})
        got = _read(parent, until=_answered(7))
    assert [m for m in got if m["kind"] != "log"] == [
        {"id": 7, "kind": "trial", "ok": True, "detail": ""}
    ]
    assert not list(trial_dir.glob("alk-trial-*"))


def test_the_trial_fails_when_the_scan_finds_nothing(trial_dir, monkeypatch):
    monkeypatch.setattr(tools, "browser_scan", lambda *args, **kwargs: ([], frozenset()))
    with _child() as parent:
        _up(parent)
        parent.send({"id": 7, "kind": "trial"})
        got = _read(parent, until=_answered(7))
    answer = got[-1]
    assert answer["kind"] == "trial" and answer["ok"] is False
    assert "scan found []" in answer["detail"]
    assert _kinds(got) == ["trial"]
    assert not list(trial_dir.glob("alk-trial-*"))


def test_the_trial_fails_when_instrumentation_ignores_what_the_scan_reached(
    trial_dir, monkeypatch
):
    real = instrumentation_evidence.detect_instrumentation
    monkeypatch.setattr(
        instrumentation_evidence,
        "detect_instrumentation",
        lambda source_root, reached=(): real(source_root),
    )
    with _child() as parent:
        _up(parent)
        parent.send({"id": 7, "kind": "trial"})
        answer = _read(parent, until=_answered(7))[-1]
    assert answer["ok"] is False
    assert "'none', not 'traceai'" in answer["detail"]


def test_the_trial_fails_when_a_stage_goes_unreported(trial_dir, monkeypatch):
    real = tools._checked_contract

    def skips_the_model(*args, progress, **kwargs):
        def report(stage, problems):
            if stage != "model":
                progress(stage, problems)

        return real(*args, progress=report, **kwargs)

    monkeypatch.setattr(tools, "_checked_contract", skips_the_model)
    with _child() as parent:
        _up(parent)
        parent.send({"id": 7, "kind": "trial"})
        answer = _read(parent, until=_answered(7))[-1]
    assert answer["ok"] is False
    assert "stages" in answer["detail"]


def test_the_trial_fails_when_the_runtime_stage_is_missing(trial_dir, monkeypatch):
    def answers_early(payload, *, progress, **kwargs):
        for stage in ["contract", "tool_evidence", "prompt_copy", "model"]:
            progress(stage, [])
        return _trial_contract()

    monkeypatch.setattr(tools, "_checked_contract", answers_early)
    with _child() as parent:
        _up(parent)
        parent.send({"id": 7, "kind": "trial"})
        answer = _read(parent, until=_answered(7))[-1]
    assert answer["ok"] is False
    assert "stages" in answer["detail"]


def test_the_trial_fails_when_the_save_refuses_its_contract(trial_dir, monkeypatch):
    def refuses(payload, *, progress, **kwargs):
        for stage in save_child._STAGE_ORDER:
            progress(stage, [])
        return {"content": [], "is_error": True}

    monkeypatch.setattr(tools, "_checked_contract", refuses)
    with _child() as parent:
        _up(parent)
        parent.send({"id": 7, "kind": "trial"})
        answer = _read(parent, until=_answered(7))[-1]
    assert answer == {
        "id": 7,
        "kind": "trial",
        "ok": False,
        "detail": "the save refused the trial's contract",
    }


def test_an_exception_in_the_trial_fails_it_without_a_failed_message(trial_dir, monkeypatch):
    def no_room(*args, **kwargs):
        raise OSError("no room for the trial's folder")

    monkeypatch.setattr(tempfile, "mkdtemp", no_room)
    with _child() as parent:
        _up(parent)
        parent.send({"id": 7, "kind": "trial"})
        got = _read(parent, until=_answered(7))
    assert _kinds(got) == ["trial"]
    assert got[-1]["ok"] is False
    assert got[-1]["detail"] == "OSError: no room for the trial's folder"


def test_a_trial_that_raises_after_writing_its_tree_still_removes_it(trial_dir, monkeypatch):
    real = Path.write_text
    written: list[Path] = []

    def second_fails(self, *args, **kwargs):
        if written:
            raise OSError("the disk filled")
        written.append(self)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", second_fails)
    with _child() as parent:
        _up(parent)
        parent.send({"id": 7, "kind": "trial"})
        got = _read(parent, until=_answered(7))
    assert got[-1]["ok"] is False and written
    assert not list(trial_dir.glob("alk-trial-*"))


# P2a


@pytest.fixture
def stand_in_module(tmp_path, monkeypatch):
    """Writes a stand-in module importable by name, and puts back whatever it replaced."""
    monkeypatch.syspath_prepend(str(tmp_path))
    for attribute in ("_copy_prompt", "model_problems"):
        monkeypatch.setattr(tools, attribute, getattr(tools, attribute))
    names: list[str] = []

    def write(name: str, text: str) -> str:
        (tmp_path / f"{name}.py").write_text(text, encoding="utf-8")
        names.append(name)
        return name

    yield write
    for name in names:
        sys.modules.pop(name, None)


def test_a_stand_in_that_loads_replaces_the_checks_it_names(stand_in_module):
    name = stand_in_module(
        "save_child_stand_in_ok",
        "def copy_prompt(contract, source_root):\n"
        "    return ['stood in']\n"
        "STAGES = {('tools', '_copy_prompt'): copy_prompt,\n"
        "          ('fi.alk.harness.tools', 'model_problems'): copy_prompt}\n",
    )
    with _child() as parent:
        _up(parent)
        parent.send({"id": 3, "kind": "stand_in", "module": name})
        answer = _read(parent, until=_answered(3))[-1]
    assert answer == {"id": 3, "kind": "stand_in", "ok": True, "detail": ""}
    stood_in = sys.modules[name].copy_prompt
    assert tools._copy_prompt is stood_in
    assert tools.model_problems is stood_in


def test_a_stand_in_that_does_not_load_says_why():
    with _child() as parent:
        _up(parent)
        parent.send({"id": 3, "kind": "stand_in", "module": "no_such_stand_in_module"})
        answer = _read(parent, until=_answered(3))[-1]
    assert answer["ok"] is False
    assert answer["detail"].startswith("ModuleNotFoundError: ")


def test_a_stand_in_naming_a_missing_check_changes_nothing(stand_in_module):
    before = tools._copy_prompt
    name = stand_in_module(
        "save_child_stand_in_typo",
        "def stand(*args):\n"
        "    return []\n"
        "STAGES = {('tools', '_copy_prompt'): stand, ('tools', '_copy_promt'): stand}\n",
    )
    with _child() as parent:
        _up(parent)
        parent.send({"id": 3, "kind": "stand_in", "module": name})
        answer = _read(parent, until=_answered(3))[-1]
    assert answer["ok"] is False
    assert "_copy_promt" in answer["detail"]
    assert tools._copy_prompt is before


def test_a_stand_in_cannot_replace_a_module_outside_the_kit(stand_in_module, monkeypatch):
    monkeypatch.setattr(os, "getpid", os.getpid)
    name = stand_in_module(
        "save_child_stand_in_outside",
        "STAGES = {('os', 'getpid'): lambda: 0}\n",
    )
    with _child() as parent:
        _up(parent)
        parent.send({"id": 3, "kind": "stand_in", "module": name})
        answer = _read(parent, until=_answered(3))[-1]
    assert answer["ok"] is False
    assert os.getpid() != 0


# P3


_REACHING_TREE = {
    "agent/main.py": "from lib import trace\nimport playwright\n",
    "lib/__init__.py": "",
    "lib/trace.py": "import traceai_openai\n",
}


@pytest.fixture
def calls(monkeypatch):
    """Records each check the session job calls, in order, through the real one."""
    seen: list[tuple[str, Any]] = []
    scan = tools._tools_in_agent_folder
    detect = instrumentation_evidence.detect_instrumentation
    walk = agent_type_check.code_the_agent_reaches

    def scanning(scan_root, subfolder, reached=None):
        seen.append(("scan", subfolder))
        return scan(scan_root, subfolder, reached)

    def detecting(source_root, reached=()):
        reached = list(reached)
        seen.append(("instrumentation", reached))
        return detect(source_root, reached)

    def walking(source_root, subfolder):
        seen.append(("walk", subfolder))
        return walk(source_root, subfolder)

    monkeypatch.setattr(tools, "_tools_in_agent_folder", scanning)
    monkeypatch.setattr(instrumentation_evidence, "detect_instrumentation", detecting)
    monkeypatch.setattr(agent_type_check, "code_the_agent_reaches", walking)
    return seen


def test_with_a_folder_the_scan_runs_first_and_instrumentation_reads_its_reach(tmp_path, calls):
    root = _write(tmp_path, _REACHING_TREE)
    with _child() as parent:
        _up(parent)
        got = _session(parent, 4, root, "agent")
    assert _kinds(got) == ["scan", "instrumentation"]
    assert got[0] == {"id": 4, "kind": "scan", "found": ["playwright"], "cut": []}
    assert got[1] == {"id": 4, "kind": "instrumentation", "value": "traceai"}
    assert [name for name, _ in calls] == ["scan", "instrumentation"]
    assert calls[1][1], "instrumentation read the scan's reach"


def test_without_a_folder_instrumentation_runs_first(tmp_path, calls):
    root = _write(tmp_path, {"main.py": "import traceai_openai\nimport playwright\n"})
    with _child() as parent:
        _up(parent)
        got = _session(parent, 4, root, None)
    assert _kinds(got) == ["instrumentation", "scan"]
    assert got[0]["value"] == "traceai"
    assert got[1]["found"] == ["playwright"]
    assert [name for name, _ in calls] == ["instrumentation", "scan"]


def test_a_scan_of_another_root_has_instrumentation_walk_the_agents_reach(tmp_path, calls):
    root = _write(tmp_path, _REACHING_TREE)
    with _child() as parent:
        _up(parent)
        got = _session(parent, 4, root, "agent", scan_root=None)
    assert got[1]["value"] == "traceai"
    assert [name for name, _ in calls] == ["scan", "walk", "instrumentation"]


def test_a_missing_folder_gives_none(tmp_path, calls):
    root = _write(tmp_path, _REACHING_TREE)
    with _child() as parent:
        _up(parent)
        got = _session(parent, 4, root, "nowhere")
    assert got == [
        {"id": 4, "kind": "scan", "found": [], "cut": []},
        {"id": 4, "kind": "instrumentation", "value": "none"},
    ]


def test_a_scan_that_raised_has_instrumentation_walk_afresh(tmp_path, calls, monkeypatch):
    root = _write(tmp_path, _REACHING_TREE)
    partial = tmp_path / "half-read.py"

    def breaks(scan_root, subfolder, reached=None):
        calls.append(("scan", subfolder))
        reached.append(partial)
        raise RuntimeError("the scan broke")

    monkeypatch.setattr(tools, "_tools_in_agent_folder", breaks)
    with _child() as parent:
        _up(parent)
        got = _session(parent, 4, root, "agent")
    failed, traced = got
    assert failed["kind"] == "failed" and failed["part"] == "scan"
    assert (failed["error"], failed["message"]) == ("RuntimeError", "the scan broke")
    assert "RuntimeError: the scan broke" in failed["trace"]
    assert traced == {"id": 4, "kind": "instrumentation", "value": "traceai"}
    assert [name for name, _ in calls] == ["scan", "walk", "instrumentation"]
    assert partial not in calls[-1][1]


# P4, P6


def test_a_part_that_raises_is_sent_as_failed_and_the_other_part_and_the_loop_go_on(
    tmp_path, monkeypatch
):
    root = _write(tmp_path, {"main.py": "import playwright\n"})

    def raises(*args, **kwargs):
        raise ValueError("instrumentation broke")

    monkeypatch.setattr(instrumentation_evidence, "detect_instrumentation", raises)
    with _child() as parent:
        _up(parent)
        got = _session(parent, 5, root, None)
        failed, scan = got
        assert failed["kind"] == "failed" and failed["part"] == "instrumentation"
        assert failed["error"] == "ValueError"
        assert "ValueError: instrumentation broke" in failed["trace"]
        assert scan["kind"] == "scan" and scan["found"] == ["playwright"]
        parent.send({"id": 6, "kind": "stand_in", "module": "no_such_stand_in_module"})
        assert _read(parent, until=_answered(6))[-1]["kind"] == "stand_in"


def test_a_save_that_raises_is_sent_as_failed_and_the_loop_goes_on(tmp_path, monkeypatch):
    def raises(*args, **kwargs):
        raise KeyError("save broke")

    monkeypatch.setattr(tools, "_checked_contract", raises)
    with _child() as parent:
        _up(parent)
        parent.send(_save_request(8, tmp_path, dict(save_child.TRIAL_PAYLOAD)))
        got = _read(parent, until=_answered(8))
        assert _kinds(got) == ["failed"]
        assert got[-1]["part"] == "save" and got[-1]["error"] == "KeyError"
        parent.send({"id": 9, "kind": "stand_in", "module": "no_such_stand_in_module"})
        assert _read(parent, until=_answered(9))[-1]["kind"] == "stand_in"


# P5


def _trial_contract() -> AgentContract:
    return AgentContract.model_validate(
        tools._without_nulls(tools.unwrapped(save_child.TRIAL_PAYLOAD))
    )


def test_the_save_sends_each_stage_then_one_done_with_what_it_set(tmp_path, monkeypatch):
    given: dict[str, Any] = {}

    def checks(payload, *, progress, asked, held, **kwargs):
        given.update(kwargs, payload=payload)
        progress("contract", ["first"])
        asked.add("model")
        held["prompt-quote"] = "kept from before"
        held["prompt-quote-confirmed"] = "a new quote"
        for stage in ["tool_evidence", "prompt_copy", "model", "runtime"]:
            progress(stage, [])
        return _trial_contract()

    monkeypatch.setattr(tools, "_checked_contract", checks)
    with _child() as parent:
        _up(parent)
        parent.send(
            _save_request(
                8,
                tmp_path,
                dict(save_child.TRIAL_PAYLOAD),
                asked=["prompt-quote"],
                held={"prompt-quote": "kept from before", "another call's": "left alone"},
                instrumentation="none",
            )
        )
        got = _read(parent, until=_answered(8))
    assert _kinds(got) == ["stage"] * 5 + ["done"]
    assert [m["stage"] for m in got[:5]] == save_child._STAGE_ORDER
    assert got[0] == {
        "id": 8,
        "kind": "stage",
        "stage": "contract",
        "problems": ["first"],
        "asked": ["prompt-quote"],
        "held": {},
    }
    written = {"prompt-quote": "kept from before", "prompt-quote-confirmed": "a new quote"}
    assert got[1]["asked"] == ["model", "prompt-quote"]
    assert got[1]["held"] == written
    done = got[-1]
    assert done == {
        "id": 8,
        "kind": "done",
        "contract": _trial_contract().model_dump(mode="json"),
        "response": None,
        "asked": ["model", "prompt-quote"],
        "held": written,
    }
    assert given["source_root"] == tmp_path
    assert given["subfolder"] == "agent"
    assert given["require_setup_contract"] is False
    assert given["instrumentation"] == "none"
    assert given["payload"] == save_child.TRIAL_PAYLOAD


def test_a_refused_save_sends_its_response_in_done(tmp_path, monkeypatch):
    response = {"content": [{"type": "text", "text": "Not accepted."}], "is_error": True}

    def refuses(payload, *, progress, **kwargs):
        for stage in save_child._STAGE_ORDER:
            progress(stage, ["no"] if stage == "prompt_copy" else [])
        return response

    monkeypatch.setattr(tools, "_checked_contract", refuses)
    with _child() as parent:
        _up(parent)
        parent.send(_save_request(8, tmp_path, dict(save_child.TRIAL_PAYLOAD)))
        got = _read(parent, until=_answered(8))
    assert _kinds(got) == ["stage"] * 5 + ["done"]
    assert got[2]["problems"] == ["no"]
    assert got[-1]["contract"] is None
    assert got[-1]["response"] == response


def test_the_real_checks_report_every_stage_for_an_accepted_and_a_refused_payload(tmp_path):
    root = _write(tmp_path, save_child.TRIAL_TREE)
    refused = dict(
        save_child.TRIAL_PAYLOAD,
        system_prompt_from=[{"path": "agent/absent.py", "start_line": 1, "end_line": 3}],
    )
    with _child() as parent:
        _up(parent)
        parent.send(_save_request(8, root, dict(save_child.TRIAL_PAYLOAD)))
        accepted = _read(parent, until=_answered(8))
        parent.send(_save_request(9, root, refused, held={"another call's": "left alone"}))
        not_accepted = _read(parent, until=_answered(9))
    for got in (accepted, not_accepted):
        assert _kinds(got) == ["stage"] * 5 + ["done"]
        assert [m["stage"] for m in got[:5]] == save_child._STAGE_ORDER
    assert accepted[-1]["contract"]["instrumentation"] == "traceai"
    assert accepted[-1]["response"] is None
    assert not_accepted[-1]["contract"] is None
    assert not_accepted[-1]["response"]["is_error"] is True
    assert not_accepted[2]["problems"], "the prompt's lines are missing"
    assert "another call's" not in not_accepted[-1]["held"]


# P7


def test_a_log_line_with_a_trace_reaches_the_parent_as_text_and_logging_is_put_back(
    tmp_path, monkeypatch
):
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level

    def logs(payload, *, progress, **kwargs):
        # The child's own logger masks, which keeps the trace only as text.
        try:
            raise ValueError("a fault worth a trace")
        except ValueError:
            save_child.logger.exception("the check fell over")
        save_child.logger.debug("below the parent's level")
        return {"content": [], "is_error": True}

    monkeypatch.setattr(tools, "_checked_contract", logs)
    with _child(install_logging=True) as parent:
        _up(parent)
        assert [type(h) for h in root.handlers] == [save_child.PipeLogHandler]
        parent.send(
            {
                "id": 1,
                "kind": "session",
                "scan_root": None,
                "source_root": None,
                "subfolder": None,
                "log_level": logging.INFO,
            }
        )
        _read(parent, until=lambda message: message["kind"] == "scan")
        assert root.level == logging.INFO
        parent.send(_save_request(2, tmp_path, dict(save_child.TRIAL_PAYLOAD)))
        got = _read(parent, until=_answered(2))
    logs_sent = [m for m in got if m["kind"] == "log" and m["name"] == save_child.logger.name]
    assert len(logs_sent) == 1
    line = logs_sent[0]
    assert (line["id"], line["level"]) == (None, logging.ERROR)
    assert line["message"] == "the check fell over"
    assert line["trace"].startswith("Traceback")
    assert "ValueError: a fault worth a trace" in line["trace"]
    assert root.handlers == handlers
    assert root.level == level


def test_a_log_line_on_a_closed_pipe_is_dropped():
    parent, child = multiprocessing.Pipe()
    child.close()
    parent.close()
    handler = save_child.PipeLogHandler(save_child._Pipe(child))
    record = logging.LogRecord("fi.alk.harness.tools", logging.WARNING, __file__, 1, "x", None, None)
    handler.handle(record)


# The shared preload


def test_both_checkers_preload_one_list_that_names_the_save_checks():
    assert scan_folders._PRELOAD is forkserver_preload.PRELOAD
    for name in (
        "fi.alk.harness.framework_detection",
        "fi.alk.harness.tools",
        "fi.alk.harness.runtime_repair",
        "fi.alk.harness.save_child",
    ):
        assert name in forkserver_preload.PRELOAD
    source = Path(forkserver_preload.__file__).read_text(encoding="utf-8")
    assert not [
        node for node in ast.walk(ast.parse(source)) if isinstance(node, (ast.Import, ast.ImportFrom))
    ]


def test_ci_runs_this_file():
    workflows = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "sdk-smoke.yml"
    assert "tests/harness/test_save_child.py" in workflows.read_text(encoding="utf-8")
