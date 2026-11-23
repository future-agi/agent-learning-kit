"""Shared helpers for the test files of the framework reference agents under
`examples/harness/`. This module is not a test file; it is the only one importing the private
authoring helpers of `test_bundle_author_v2`.
"""

from __future__ import annotations

import ast
import contextlib
import importlib
import inspect
import json
import os
import shutil
import subprocess
import sys
import traceback
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from types import ModuleType

from test_bundle_author_v2 import (
    _authoring,
    _github_job,
    _write_callable_contract,
    _write_chat_contract,
)

from fi.alk.harness.bundle_author_v2 import author_bundle_v2
from fi.alk.harness.framework_detection import detect_framework, folder_findings

REPO = Path(__file__).resolve().parents[2]
TRACE_HOOK = REPO / "src/fi/alk/harness/livekit_tool_trace_bootstrap.py"
TOOLS = ("look_up_order", "start_return", "return_policy")
# The contract writers a test file passes to `build_control_service`.
write_callable_contract = _write_callable_contract
write_chat_contract = _write_chat_contract
DETECTION_CASES = ("run_folder", "subfolder", "in_place", "shadowed")


# Copying and stand-ins ----------------------------------------------------------------------


def copy_example(src: Path, dest: Path, ignore: Sequence[str] = ()) -> Path:
    """Copy an example folder to `dest`, leaving out bytecode and any `ignore` patterns."""
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", *ignore))
    return dest


def write_stand_ins(root: Path, files: Mapping[str, str]) -> Path:
    """Write `{relative_path: source}` under `root`, making the folders in between."""
    for relative, source in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return root


# Importing the example ----------------------------------------------------------------------


def _top_level(modules: Sequence[str]) -> set[str]:
    return {name.split(".")[0] for name in modules}


@contextlib.contextmanager
def fresh_import(
    tmp_path: Path,
    monkeypatch,
    example: Path,
    *,
    module: str = "agent",
    stand_in: Mapping[str, str] | None = None,
    set_env: Mapping[str, str] | None = None,
    clear_env: Sequence[str] = (),
    purge: Sequence[str] = (),
    copy_package: bool = False,
    reset_each_load: bool = False,
    ignore: Sequence[str] = (),
) -> Iterator[Callable[..., object]]:
    """Yield `load(**env)`, which imports `module` of the example afresh.

    - `stand_in`: `{relative_path: source}` of the stand-in libraries, put first on the path;
      none when the real library is used.
    - `clear_env` is removed and `set_env` set before the first load. `load(**env)` sets `env`
      first, and with `reset_each_load` clears `clear_env` before that, so each import sees
      only the environment it was given.
    - `purge`: top-level module names dropped from `sys.modules` before every load, with the
      example's own, so one test can import twice under different environments.
    - `copy_package`: copy the whole example (minus `ignore`) for every load, for a package
      such as an ADK app; otherwise only `agent.py` is copied, once.
    Everything imported is removed again at teardown.
    """
    saved = dict(sys.modules)
    clear_names = tuple(clear_env)
    purged = _top_level([*purge, module])

    def reset_environment() -> None:
        for name in clear_names:
            # Set, then removed, so that on teardown a variable the example sets at import is
            # taken out again instead of leaking into the next test.
            monkeypatch.setenv(name, "placeholder")
            monkeypatch.delenv(name)

    def purge_modules() -> None:
        for name in list(sys.modules):
            if name.split(".")[0] in purged:
                del sys.modules[name]

    reset_environment()
    for name, value in (set_env or {}).items():
        monkeypatch.setenv(name, value)
    run = tmp_path / "run"
    if not copy_package:
        run.mkdir()
        shutil.copy2(example / "agent.py", run / "agent.py")
        monkeypatch.syspath_prepend(str(run))
    if stand_in is not None:
        monkeypatch.syspath_prepend(str(write_stand_ins(tmp_path / "stand-in", stand_in)))
    purge_modules()
    copies: list[Path] = []

    def load(**environment: str):
        if reset_each_load:
            reset_environment()
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        purge_modules()
        if copy_package:
            copy = copy_example(example, tmp_path / f"run-{len(copies)}", ignore)
            copies.append(copy)
            monkeypatch.syspath_prepend(str(copy))
        return importlib.import_module(module)

    try:
        yield load
    finally:
        for name in list(sys.modules):
            if name not in saved:
                del sys.modules[name]
        sys.modules.update(saved)


# Detection ----------------------------------------------------------------------------------


def assert_detected(
    example: Path,
    framework: str,
    shadow_name: str | Sequence[str],
    *,
    where: str,
    tmp_path: Path,
    subfolder: str,
    ignore: Sequence[str] = (),
) -> None:
    """Check one way the example is found as `framework`; `where` is one of `DETECTION_CASES`.

    - `run_folder`: the example is the run folder, with no blocker.
    - `subfolder`: it sits inside a bigger tree that also holds a package named `shadow_name`
      deep down, and is found with no blocker.
    - `in_place`: in the repository, with nothing named `shadow_name` on its path or inside.
    - `shadowed`: a module named `shadow_name` in the folder stops the folder being found as
      `framework` at all, so the cases above prove something.
    `shadow_name` may be several names, the first being the one planted.
    """
    names = (shadow_name,) if isinstance(shadow_name, str) else tuple(shadow_name)
    expected = {"name": framework, "version": None}
    if where == "run_folder":
        run = copy_example(example, tmp_path / "run", ignore)
        assert detect_framework(run) == expected
        assert folder_findings(run).blockers == frozenset()
    elif where == "subfolder":
        repo = tmp_path / "repo"
        copy_example(example, repo / subfolder, ignore)
        # A package of the same name deep in the tree must not make the library import look
        # like the customer's own module.
        nested = repo / "src/pkg" / names[0]
        nested.mkdir(parents=True)
        (nested / "__init__.py").write_text("", encoding="utf-8")
        (nested / "score.py").write_text("def score():\n    return 1\n", encoding="utf-8")
        (repo / "main.py").write_text("print('hello')\n", encoding="utf-8")
        assert detect_framework(repo / subfolder, repo_root=repo) == expected
        assert folder_findings(repo / subfolder, repo_root=repo).blockers == frozenset()
    elif where == "in_place":
        shadows = {part for name in names for part in (name, f"{name}.py")}
        assert not shadows & set(example.relative_to(REPO).parts)
        assert not [
            path
            for path in example.rglob("*")
            if path.name in shadows and "__pycache__" not in path.parts
        ]
        assert detect_framework(example, repo_root=REPO) == expected
        assert folder_findings(example, repo_root=REPO).blockers == frozenset()
    elif where == "shadowed":
        run = copy_example(example, tmp_path / "run", ignore)
        assert detect_framework(run) == expected
        (run / f"{names[0]}.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
        found = detect_framework(run)
        assert found.get("name") != framework, f"a shadowed import is still detected: {found!r}"
    else:
        raise ValueError(f"unknown case {where!r}")


# The build ----------------------------------------------------------------------------------


def build_control_service(
    tmp_path: Path,
    contract_writer: Callable[[Path], None],
    module: str,
    *,
    subfolder: str,
    example: Path,
    tools: Sequence[str] = TOOLS,
    ignore: Sequence[str] = (),
    secret_aliases: Sequence[str] = ("OPENAI_API_KEY", "OPENAI_BASE_URL"),
):
    """Build the example through the hosted path and return its control-service process.

    `contract_writer(authoring)` writes the contract (wrap `write_callable_contract` or
    `write_chat_contract`); the tools are then declared as entrypoints of `module`.
    `secret_aliases` are the job's secret names, the ones the example reads.
    """
    source = tmp_path / "source"
    copy_example(example, source / subfolder, ignore)
    (source / "README.md").write_text("many examples\n", encoding="utf-8")
    authoring = _authoring(tmp_path)
    contract_writer(authoring)
    contract_path = authoring / "contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["tool_entrypoints"] = [
        {"tool": name, "mode": "import", "module": module, "callable": name}
        for name in tools
    ]
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    bundle = author_bundle_v2(
        source=source,
        job=_github_job(
            subfolder,
            with_secrets=True,
            secret_aliases=tuple(secret_aliases),
        ),
        authoring=authoring,
        output=tmp_path / "bundle",
    )
    return next(p for p in bundle.processes if p.name == bundle.runtime.control_service)


def assert_bindings_match(
    bindings: Sequence[Mapping[str, str]],
    module: ModuleType,
    module_name: str,
    tools: Sequence[str] = TOOLS,
) -> None:
    """Each binding names a plain function the example's `module` really defines, one per tool."""
    assert sorted(item["name"] for item in bindings) == sorted(tools)
    # The tracer binds by the function's code name and module, so read the source: it holds
    # whether or not an SDK decorator wraps the function.
    tree = ast.parse(inspect.getsource(module))
    plain = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    for item in bindings:
        assert item["module"] == module_name
        # Each tool is bound to its own function, not to another tool's.
        assert item["callable"] == item["name"], item
        assert item["callable"] in plain, f"{item['callable']} is not a top-level def"


# The key and the base URL -------------------------------------------------------------------


def _refusal_problem(
    load: Callable[..., ModuleType],
    call: Callable[[ModuleType], object] | None,
    environment: Mapping[str, str],
    base_url: str,
    canary: str,
) -> str | None:
    """What is wrong with one case's refusal, or None."""
    refused: BaseException | None = None
    if call is None:
        try:
            load(**environment)
        except RuntimeError as exc:
            refused = exc
        except Exception as exc:  # noqa: BLE001 - reported, so every case still runs
            return f"the import failed with {type(exc).__name__}, not a refusal"
        if refused is None:
            return "the import was not refused"
    else:
        try:
            module = load(**environment)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            return f"the import failed, where the call should refuse: {type(exc).__name__}"
        try:
            call(module)
        except RuntimeError as exc:
            refused = exc
        except Exception as exc:  # noqa: BLE001 - reported, so every case still runs
            return f"the call failed with {type(exc).__name__}, not a refusal"
        if refused is None:
            return "the call was not refused"
    if base_url not in str(refused):
        return f"the refusal does not name {base_url}"
    return _key_in_example_frames(refused, canary)


def _key_in_example_frames(error: BaseException, canary: str) -> str | None:
    """Where the key shows in an error a traceback would print, or None."""
    if canary in "".join(traceback.format_exception(error)):
        return "the key is in the error or its traceback"
    trace = error.__traceback__
    while trace is not None:
        frame = trace.tb_frame
        name = frame.f_code.co_filename
        # The example's own frame: a traceback that prints variables would show it. A library
        # file of the same name is not the example's.
        if name.endswith("agent.py") and "site-packages" not in name:
            if canary in repr(frame.f_locals):
                return "the key is in a variable of the example's frame"
        trace = trace.tb_next
    return None


def assert_a_failing_turn_keeps_the_key_out_of_its_frames(
    load: Callable[..., ModuleType],
    call: Callable[[ModuleType], object],
    make_it_fail: Callable[[], None],
    *,
    key: str = "OPENAI_API_KEY",
    base_url: str = "OPENAI_BASE_URL",
) -> None:
    """A turn that fails after the base-URL check (a connection error, a 401, a model error)
    carries the key nowhere a traceback would print: not in the error, not in the example's
    frames. `make_it_fail()` makes the library raise once the module is loaded."""
    canary = "secret-canary-key"
    module = load(**{key: canary, base_url: "http://127.0.0.1:9/v1"})
    make_it_fail()
    try:
        call(module)
    except Exception as exc:  # noqa: BLE001 - any failure the library raises
        problem = _key_in_example_frames(exc, canary)
        assert problem is None, problem
    else:
        raise AssertionError("the turn did not fail, so nothing was checked")


def assert_refuses_key_without_base_url(
    load: Callable[..., ModuleType],
    call: Callable[[ModuleType], object] | None,
    monkeypatch,
    *,
    key: str = "OPENAI_API_KEY",
    base_url: str = "OPENAI_BASE_URL",
    after_refusal: Callable[[], None] | None = None,
) -> None:
    """With `key` set and `base_url` unset, empty or blank, the example refuses.

    The refusal is a RuntimeError naming `base_url`: raised at import when `call` is None, and
    otherwise by `call(module)` after an import that succeeds. The key is nowhere in it, its
    traceback or the example's frames. `load(**env)` is the test file's fresh-import fixture;
    `after_refusal()` checks each refused case's side effects. Every case runs, and the
    failure names each case that went wrong.
    """
    canary = "secret-canary-key"
    problems = []
    for value in (None, "", "   "):
        monkeypatch.delenv(base_url, raising=False)
        environment = {key: canary} if value is None else {key: canary, base_url: value}
        problem = _refusal_problem(load, call, environment, base_url, canary)
        if problem is None and after_refusal is not None:
            try:
                after_refusal()
            except AssertionError as exc:
                problem = f"after the refusal: {exc}"
        if problem is not None:
            label = "unset" if value is None else repr(value)
            problems.append(f"{base_url} {label}: {problem}")
    assert not problems, "\n".join(problems)


# Order state -------------------------------------------------------------------------------


def assert_each_conversation_has_its_own_orders(
    module: ModuleType, call_tool: Callable[..., str] | None = None
) -> None:
    """A return started in one conversation is not there in another, and stays in its own.

    `call_tool(name, **arguments)` calls one tool the way its library does; by default the
    module's plain function of that name."""
    call = call_tool or (lambda name, **arguments: getattr(module, name)(**arguments))

    def in_conversation(thread: str, name: str, **arguments: str) -> str:
        token = module._CONVERSATION.set(thread)
        try:
            return call(name, **arguments)
        finally:
            module._CONVERSATION.reset(token)

    started = in_conversation("one", "start_return", order_id="A1001", reason="too small")
    assert "Return started" in started
    assert in_conversation("two", "look_up_order", order_id="A1001").endswith("return: none.")
    assert in_conversation("one", "look_up_order", order_id="A1001").endswith(
        "return: too small."
    )



def assert_tool_calls_keep_each_conversations_orders(
    turn: Callable[..., str],
) -> None:
    """The same as `assert_each_conversation_has_its_own_orders`, with each tool call made by
    the library in a turn of the callback, so the conversation is named the way the callback
    names it and reaches the tool however the library runs it.

    `turn(thread_id, name, **arguments)` runs one turn for that thread in which the library
    calls tool `name`, and returns what the tool returned."""
    # Not the names the plain-function check uses, so the two can run on one module.
    started = turn("three", "start_return", order_id="A1001", reason="too small")
    assert "Return started" in started
    assert turn("four", "look_up_order", order_id="A1001").endswith("return: none.")
    assert turn("three", "look_up_order", order_id="A1001").endswith("return: too small.")

# The tracer ---------------------------------------------------------------------------------


def run_tracer(
    argv: Sequence[str],
    hook_dir: Path,
    cwd: Path,
    env: Mapping[str, str | None],
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    """Run `argv` with the hook folder first on `PYTHONPATH`, and fail with its stderr.

    `env` is added to the current environment; a value of None removes the variable. Any
    `PYTHONPATH` in it follows the hook folder.
    """
    environment = {**os.environ}
    for name, value in env.items():
        if value is None:
            environment.pop(name, None)
        else:
            environment[name] = value
    paths = [str(hook_dir), *filter(None, environment.get("PYTHONPATH", "").split(os.pathsep))]
    environment["PYTHONPATH"] = os.pathsep.join(paths)
    completed = subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        cwd=cwd,
        env=environment,
        timeout=timeout,
    )
    assert completed.returncode == 0, completed.stderr[-3000:]
    return completed


def assert_pins(folder: Path, expected_lines: Sequence[str], python: str = "3.12") -> None:
    """The example pins exactly `expected_lines` in requirements.txt and `python`."""
    lines = (folder / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert lines == list(expected_lines)
    assert (folder / ".python-version").read_text(encoding="utf-8").strip() == python
