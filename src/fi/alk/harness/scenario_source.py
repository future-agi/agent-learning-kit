"""The scenario-source adapter: reads generated scenario documents out of the bundle (code-as-text,
on the on-disk layout `folder.py` documents) and turns them into the `Scenario`/`SubGoal` objects
`hosted_scheduler.py` actually drives.

Deliberately does NOT import `fi.alk.harness.folder` or `fi.alk.harness.scenario` for the model:
both exist at HEAD, but HEAD's `Scenario` carries no `scenario_key`/`scenario_id` (those are
pr63-only) and its default `extra="ignore"` would silently discard exactly the two fields the
scheduler needs off a `scenario.json` written in the newer shape. So this module reads
`scenario.json` as a plain dict and pulls fields out by key, mirroring the documented layout
instead of depending on either model -- see the report's design-decisions section for the
consequences of that choice (HEAD-model drift).

Karthik's Scenario Generation Contract (the `provision`/`begin` wire shapes) has not landed. This
module builds the bundle-reading + compiling + wrapping side only; `register_with_platform` below
is the one seam a later change wires in once that contract exists.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Sequence

if TYPE_CHECKING:
    from .hosted_entrypoint import ScenariosClient

# LAYOUT DECISION (contract-silent -- hosted-execution-seams.md v1.15 §2 never mentions scenario
# documents, and §7 assigns the on-disk layout to Karthik's contract, status "in review"). Scenario
# documents live at `<bundle_dir>/<SCENARIOS_DIRNAME>/<name>/...`, matching `folder.py`'s own
# `SCENARIOS` constant, so a write_folder destination of `<bundle_dir>` lands correctly with no
# translation. Kept as one module-level constant so a later contract can move it in one edit.
SCENARIOS_DIRNAME = "scenarios"

_CHECKS_DIRNAME = "checks"
_SCENARIO_JSON = "scenario.json"
_SETUP_PY = "setup.py"
_READY_PY = "ready.py"

# R1-5: divergence (a) (compile once, at load) moves every scenario file's compile+exec into
# `asyncio.to_thread(load_scenarios, ...)`, OUTSIDE every phase budget the scheduler enforces
# (`SETUP_TIMEOUT_SECONDS`/`READY_TIMEOUT_SECONDS`/`CHECK_TIMEOUT_SECONDS` all apply downstream, to
# `_run_phase`). Pathological module-level code (`while True: pass`, a blocking socket read) would
# otherwise hang the load with zero terminal events, no timeout catching it, ever. One generous
# wall-clock budget here converts that hang into a typed terminal instead -- a worker thread cannot
# actually be killed, so this accepts a leaked thread over an unbounded one, on the reasoning that a
# terminal event today is strictly better than none ever.
_LOAD_TIMEOUT_SECONDS = 60.0

# The TEXT of a judged sub-goal's check is never persisted by `folder.py`'s `write_folder` (only
# `SubGoal.deterministic()` entries get a `checks/<name>.py` file) -- this fixed marker stands in
# for it so `SubGoal.judged` (mandatory; read by plain attribute access in `hosted_scheduler.py`)
# is never empty for a sub-goal this reader knows is judged. The round-trip loss this represents is
# recorded under CONTRACT QUESTIONS in the report.
_JUDGED_MARKER = "judged (reason not persisted by folder.py's on-disk layout)"


class ScenarioDocumentInvalid(RuntimeError):
    """A scenario folder under `<bundle_dir>/scenarios/` is unreadable or malformed: missing
    `scenario.json`, invalid JSON, a field of the wrong shape, or a `setup.py`/`ready.py`/
    `checks/<goal>.py` that will not compile. Raised rather than skipped -- `folder.py`'s own
    `read_all` swallows exactly this and continues, which is right for a human editing a suite by
    hand and wrong for a hosted job, where a bad scenario silently vanishing from the run reads as
    a suite that passed with fewer scenarios than it should have."""


def bundle_has_scenarios(bundle_dir: Path) -> bool:
    """The LAYOUT DECISION's presence test: `<bundle_dir>/scenarios/` exists and at least one of
    its subdirectories holds a `scenario.json`. Deliberately narrow -- an empty or missing
    `scenarios/` must not flip the wiring decision away from the safe `NotWiredScenarioSource`
    default. A bundle that means to carry scenarios but got the layout wrong fails loudly once
    `load_scenarios` actually reads it, not silently by being treated as scenario-free here.

    An unreadable `scenarios/` directory (permission denied, race with deletion, etc.) reports
    False rather than raising `OSError` (R1-1): this call sits in `hosted_entrypoint.py`'s wiring
    `if` BEFORE the `try`/`except` that maps `ScenarioDocumentInvalid` to a typed terminal, so an
    escape here would kill the whole guest process with no terminal event at all. Falling back to
    `NotWiredScenarioSource`'s existing typed failure is the safe direction -- the alternative of
    raising here has nowhere typed to land.
    """
    root = bundle_dir / SCENARIOS_DIRNAME
    if not root.is_dir():
        return False
    try:
        children = list(root.iterdir())
    except OSError:
        return False
    return any((child / _SCENARIO_JSON).is_file() for child in children if child.is_dir())


def _judged_placeholder_check(world: Any, calls: Any) -> None:
    """The check for a judged (non-deterministic) sub-goal: always reports "held", via the same
    convention a deterministic check uses. `SubGoalResult.judged` -- not this return value -- is
    what has to tell downstream a real judge still has to run; see CONTRACT QUESTIONS in the
    report for the gap that leaves (a judged-only scenario reports "passed" before any judge runs).
    """
    del world, calls
    return None


def _compile_entry(
    source: str, *, label: str, entry: str, allow_empty: bool = True
) -> Callable[..., object]:
    """One scenario code-text -> a bare callable that raises, compiled ONCE here rather than per
    call. Mirrors `folder.py`'s `_run` in exactly two respects: `compile(source, name, "exec")`
    into a fresh, empty-dict namespace with default builtins, and (when `allow_empty`)
    empty/whitespace-only source is a no-op success. Deliberately diverges from `_run` in the two
    respects the brief calls out:
    (a) compiling here, at load, turns a syntax error into one typed terminal for the whole job
    instead of a per-scenario fault discovered mid-run; (b) the compiled function is returned
    BARE, never wrapped in `_run`'s `Outcome` -- `hosted_scheduler.py`'s `_run_phase` is what
    classifies a raised exception into `setup_crashed`/`ready_broken`/`check_broken`/a timeout, and
    an `Outcome` return here would swallow every one of those before `_run_phase` ever saw it.
    `_run`'s complaint-sentence return convention (a non-None, non-True, non-empty-string value
    means "did not hold") is left untranslated for the same reason: `hosted_scheduler.py`'s own
    `_classify_ready`/`_classify_check` already own that classification on the return-VALUE side of
    this boundary; only the raise-vs-return boundary belongs to this module.

    `allow_empty=False` is for `check` entries only (R1-2): an EXISTING `checks/<name>.py` that is
    empty or whitespace-only compiles to nothing, and handing back a no-op "held" callable -- the
    right behavior for "no setup/ready code here" -- would silently turn "there is no check" into
    "the check passed": a vacuous deterministic pass, which is exactly what `hosted_scheduler.py`
    forbids one scenario level up ("scenario declared zero sub_goals"). Absence of the file
    entirely is what means "judged" (see `_load_one`); an existing-but-empty file is malformed.
    """
    if not source.strip():
        if allow_empty:
            return lambda *args: None
        raise ScenarioDocumentInvalid(f"{label} defines no {entry}()")
    try:
        code = compile(source, f"<{label}>", "exec")
    except (SyntaxError, ValueError) as exc:
        # SyntaxError is the common case; a NUL byte in the source raises ValueError on some
        # interpreter versions (R1-1) rather than SyntaxError -- both are the same content defect.
        raise ScenarioDocumentInvalid(f"{label} would not compile: {exc}") from exc
    namespace: dict[str, Any] = {}
    try:
        exec(code, namespace)  # noqa: S102 - scenario code is meant to be exec'd; see CONTRACT QUESTIONS
    except (Exception, SystemExit, KeyboardInterrupt) as exc:  # noqa: BLE001 - see R1-1
        # A module-level `sys.exit()`/`raise SystemExit(...)` in the file itself is a malformed
        # document, not a request to shut the guest process down -- `SystemExit`/`KeyboardInterrupt`
        # are `BaseException`, not `Exception`, so a bare `except Exception` (the pre-R1-1 shape)
        # let them straight through this boundary and out of `run_job` with zero terminal events,
        # the guest exiting with whatever code the scenario file itself chose.
        raise ScenarioDocumentInvalid(f"{label} would not compile: {exc}") from exc
    function = namespace.get(entry)
    if not callable(function):
        raise ScenarioDocumentInvalid(f"{label} defines no {entry}()")
    return function


@dataclass(frozen=True)
class _CompiledSubGoal:
    """Satisfies `hosted_scheduler.SubGoal`: `name`/`judged` as plain attributes, `check` as a bare
    callable. Stored as instance DATA rather than a `def check(self, world, calls)` method so
    `goal.check(world, calls)` invokes the compiled function directly with exactly the two
    positional arguments `_run_phase` passes -- a real method would prepend `self` as a third."""

    name: str
    judged: str
    check: Callable[[Any, Any], object]


@dataclass(frozen=True)
class _CompiledScenario:
    """Satisfies `hosted_scheduler.Scenario`. `scenario_key`/`scenario_id` are carried VERBATIM
    from the document, including an empty `scenario_id` -- synthesizing one here would hide that
    pre-allocation has not actually run (see CONTRACT QUESTIONS: receipts carry `scenario_id ""`
    until that seam is wired). `setup`/`ready` are likewise stored as data, for the same reason as
    `_CompiledSubGoal.check` above."""

    scenario_key: str
    scenario_id: str
    sub_goals: tuple[_CompiledSubGoal, ...]
    setup: Callable[[Any], object]
    ready: Callable[[Any], object]


def _read_text(path: Path, *, label: str) -> str:
    """Missing is "" (mirrors `folder.py`'s own missing-setup/ready-is-empty convention); present
    but unreadable (permission denied, a directory instead of a file) or present but not valid
    UTF-8 is a typed `ScenarioDocumentInvalid`, never a raw `OSError`/`UnicodeDecodeError` escaping
    this module (R1-1) -- both are equally "this scenario folder is malformed", the same
    conclusion `_load_one`'s other reads already reach for a bad `scenario.json`.
    """
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ScenarioDocumentInvalid(f"{label}: cannot read {path.name}: {exc}") from exc


def _validate_subgoal_name(name: str, *, folder_name: str) -> None:
    """R1-3: `sub_goals[]` entries are used verbatim to build `checks/<name>.py` -- a path
    separator or a `..` segment lets a name escape `checks/` (and the sealed bundle) entirely: an
    absolute name execs an arbitrary file never hashed into the bundle's `files[]` (bypassing the
    §2e integrity seal), and a `../`-style traversal that resolves to nothing silently turns into a
    JUDGED sub-goal (`check_path.is_file()` is False) instead of a typed failure. Rejecting
    anything but a plain filename component closes both."""
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise ScenarioDocumentInvalid(
            f"{folder_name}: sub_goals name {name!r} is not a plain filename "
            "(no path separators, no '..', no leading '/')"
        )


def _load_one(folder: Path) -> _CompiledScenario:
    """One scenario folder -> a `Scenario`-protocol object. Mirrors `folder.py`'s documented
    layout (`scenario.json` + `setup.py` + `ready.py` + `checks/<goal>.py`) but reads
    `scenario.json` itself as a plain dict rather than through `fi.alk.harness.scenario.Scenario`
    -- see the module docstring. `folder.py`'s `read_folder` restores only `setup_code`/
    `ready_code` from a folder; it does not read `checks/` at all, so every `checks/<goal>.py` for
    each name in the document's `sub_goals` is read here, by this module, directly.
    """
    body_path = folder / _SCENARIO_JSON
    try:
        raw = body_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # UnicodeDecodeError is a ValueError, not an OSError -- widened alongside it (R1-1) so a
        # non-UTF-8 `scenario.json` is the same typed failure as an unreadable one, not an escape.
        raise ScenarioDocumentInvalid(
            f"{folder.name}: cannot read {_SCENARIO_JSON}: {exc}"
        ) from exc
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ScenarioDocumentInvalid(
            f"{folder.name}: {_SCENARIO_JSON} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(body, dict):
        raise ScenarioDocumentInvalid(f"{folder.name}: {_SCENARIO_JSON} is not a JSON object")

    scenario_key = body.get("scenario_key", "")
    if not isinstance(scenario_key, str):
        raise ScenarioDocumentInvalid(f"{folder.name}: scenario_key is not a string")
    scenario_id = body.get("scenario_id", "")
    if not isinstance(scenario_id, str):
        raise ScenarioDocumentInvalid(f"{folder.name}: scenario_id is not a string")
    sub_goal_names = body.get("sub_goals", [])
    if not isinstance(sub_goal_names, list) or not all(
        isinstance(name, str) for name in sub_goal_names
    ):
        raise ScenarioDocumentInvalid(f"{folder.name}: sub_goals is not a list of strings")
    for name in sub_goal_names:
        _validate_subgoal_name(name, folder_name=folder.name)

    setup_code = _read_text(folder / _SETUP_PY, label=folder.name)
    ready_code = _read_text(folder / _READY_PY, label=folder.name)
    setup = _compile_entry(setup_code, label=f"{folder.name}/{_SETUP_PY}", entry="setup")
    ready = _compile_entry(ready_code, label=f"{folder.name}/{_READY_PY}", entry="ready")

    sub_goals: list[_CompiledSubGoal] = []
    for name in sub_goal_names:
        check_path = folder / _CHECKS_DIRNAME / f"{name}.py"
        if check_path.is_file():
            check_code = _read_text(check_path, label=folder.name)
            check = _compile_entry(
                check_code, label=f"{folder.name}/{_CHECKS_DIRNAME}/{name}.py", entry="check",
                allow_empty=False,  # R1-2: an existing-but-empty check file is invalid, never a
                                    # vacuous pass -- absence of the file is what means "judged".
            )
            judged = ""
        else:
            # No `checks/<name>.py` -- per `write_folder`'s own `deterministic()` filter, this
            # name is a JUDGED sub-goal.
            judged = _JUDGED_MARKER
            check = _judged_placeholder_check
        sub_goals.append(_CompiledSubGoal(name=name, judged=judged, check=check))

    return _CompiledScenario(
        scenario_key=scenario_key,
        scenario_id=scenario_id,
        sub_goals=tuple(sub_goals),
        setup=setup,
        ready=ready,
    )


def load_scenarios(bundle_dir: Path) -> list[_CompiledScenario]:
    """Every scenario document under `<bundle_dir>/scenarios/`, compiled and wrapped, in the same
    sorted-by-folder-name order `folder.py`'s `read_all` uses. Raises `ScenarioDocumentInvalid` on
    the FIRST unreadable or malformed folder -- unlike `read_all`, which skips one and continues;
    a hosted job has nobody watching a suite by hand to notice a scenario silently missing from the
    count, so a folder this reader cannot use fails the whole job instead of shrinking it quietly.
    """
    root = bundle_dir / SCENARIOS_DIRNAME
    if not root.is_dir():
        raise ScenarioDocumentInvalid(f"{root} is not a directory")
    try:
        entries = sorted(root.iterdir())
    except OSError as exc:
        # An unreadable `scenarios/` directory is the same typed failure as any other malformed
        # document (R1-1) -- this is inside `run_job`'s `try`/`except ScenarioDocumentInvalid`
        # (unlike `bundle_has_scenarios`'s own guard above), so raising here is the safe direction.
        raise ScenarioDocumentInvalid(f"{root}: cannot list scenario folders: {exc}") from exc
    scenarios: list[_CompiledScenario] = []
    for folder in entries:
        if not folder.is_dir():
            continue
        scenarios.append(_load_one(folder))
    if not scenarios:
        raise ScenarioDocumentInvalid(f"{root} contains no scenario folders")
    return scenarios


class BundleScenarioSource:
    """The real `ScenarioSource`: reads and compiles the bundle's own scenario documents.
    `hosted_entrypoint.run_job` wires this in only when the injected source is still the default
    `NotWiredScenarioSource` AND the bundle actually carries a `scenarios/` directory (the LAYOUT
    DECISION's presence test) -- an injected `ScenarioSource` (every test, every future caller)
    always wins over this one.
    """

    async def build(
        self,
        job: Any,
        bundle: Any,
        scenarios_client: "ScenariosClient",
        *,
        pool: Any,
        world_factory: Any,
        bundle_dir: Path,
    ) -> Sequence[_CompiledScenario]:
        del job, bundle, scenarios_client, pool, world_factory
        # `Path.read_text`/`iterdir`/`compile` are all blocking filesystem+CPU work -- run off the
        # event loop the same way `hosted_entrypoint.py` already does for `bundle_source.load` and
        # `preflight_bundle`, rather than stalling every other in-flight scenario behind it.
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(load_scenarios, bundle_dir), timeout=_LOAD_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError as exc:
            # R1-5: the underlying thread cannot actually be canceled/killed -- it is left running
            # in the background. Converting the hang into a typed terminal here is still strictly
            # better than the pre-fix behavior (no terminal event, ever): the job gets an honest,
            # bounded FAILED verdict instead of hanging until the platform's own wall clock gives up.
            raise ScenarioDocumentInvalid(
                f"{bundle_dir / SCENARIOS_DIRNAME}: loading scenario documents exceeded "
                f"{_LOAD_TIMEOUT_SECONDS:.0f}s"
            ) from exc


async def register_with_platform(
    scenarios_client: "ScenariosClient", scenarios: Sequence[_CompiledScenario]
) -> Sequence[_CompiledScenario]:
    """SEAM -- not called anywhere in this module, and not wired into `BundleScenarioSource.build`
    above. Once scenario-generation-contract.md section 3 publishes the `provision`/`begin` payload
    and response shapes, this is where they get built from `scenarios` and posted through
    `scenarios_client.provision(...)`/`.begin(...)`, and where the platform-assigned `scenario_id`s
    that `provision` returns get merged back onto each scenario before `build` hands the list to
    the scheduler. Left unimplemented rather than guessing a body the contract has not published
    (CONTRACT GAP) -- wiring this in is the one remaining integration step this module cannot
    finish alone.
    """
    del scenarios_client, scenarios
    raise NotImplementedError(
        "register_with_platform: scenario-generation-contract.md section 3 (the provision/begin "
        "wire shapes) has not landed -- this seam is intentionally left unwired"
    )
