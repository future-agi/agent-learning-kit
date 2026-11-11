"""The save checker's parent side, against a fake child that speaks the pipe's messages.

The fake runs through the same forkserver context as the real child, so kills, restarts, deaths
and the watchdog act on real processes. Each child takes its script from the plan by the order it
started in, and writes its pid and every request it got next to the test, for the asserts.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import threading
import sys
import time
from collections.abc import Callable
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

import pytest

from fi.alk.harness import save_checker
from fi.alk.harness.contract import AgentContract
from fi.alk.harness.save_checker import (
    STAGES,
    NotInTime,
    Refused,
    Saved,
    SaveChecker,
    SaveChecksFailed,
    ScanAnswer,
    SessionChecksFailed,
    StandInFailed,
    default_mode,
    rss_bytes,
)

PAYLOAD = {
    "agent": "trial",
    "modality": "chat",
    "real_use_cases": ["o"],
    "hard_constraints": ["be polite"],
    "tools": [{"name": "t0", "args": ["x"]}],
}
CONTRACT = AgentContract.model_validate(PAYLOAD).model_dump(mode="json")
REFUSAL = {"content": [{"type": "text", "text": "Not accepted: the child said so."}], "is_error": True}
SCAN_LATE = "not checked in time: the folder's checks are still running; submit it once, alone"
SAVE_LATE = "not checked in time: submit it once, alone"
CUT = ScanAnswer([], frozenset({"deadline"}))
FOUND = ScanAnswer(["playwright"], frozenset())
# Long enough for a child to start on a loaded machine; the rows that test a deadline set theirs.
WAIT = 60.0
MIB = 1024 * 1024
FAKE_LOGGER = "tests.save_checker.fake"


def _stage(name: str, problems=(), asked=(), held=None) -> list[Any]:
    return [
        "send",
        {
            "kind": "stage",
            "stage": name,
            "problems": list(problems),
            "asked": list(asked),
            "held": dict(held or {}),
        },
    ]


SCAN = ["send", {"kind": "scan", "found": ["playwright"], "cut": []}]
INSTRUMENTATION = ["send", {"kind": "instrumentation", "value": "traceai"}]
DONE = ["send", {"kind": "done", "contract": CONTRACT, "response": None, "asked": [], "held": {}}]
GOOD_SAVE = [*(_stage(name) for name in STAGES), DONE]
FAILED_SCAN = [
    "send",
    {"kind": "failed", "part": "scan", "error": "OSError", "message": "boom", "trace": "trace"},
]
FAILED_INSTRUMENTATION = [
    "send",
    {
        "kind": "failed",
        "part": "instrumentation",
        "error": "RuntimeError",
        "message": "boom",
        "trace": "trace",
    },
]
FAILED_SAVE = [
    "send",
    {"kind": "failed", "part": "save", "error": "KeyError", "message": "'x'", "trace": "trace"},
]
NEXT_CHILD_LINE = [
    "send",
    {
        "kind": "log",
        "name": FAKE_LOGGER,
        "level": logging.WARNING,
        "message": "the next child is up",
        "trace": None,
    },
]
# Each kind's runs: the nth request of a kind takes run n, the last one repeating.
DEFAULTS: dict[str, list[list[list[Any]]]] = {
    "trial": [[["send", {"kind": "trial", "ok": True, "detail": ""}]]],
    "stand_in": [[["send", {"kind": "stand_in", "ok": True, "detail": ""}]]],
    "session": [[SCAN, INSTRUMENTATION]],
    "save": [GOOD_SAVE],
}


def _step(conn: Connection, rid: int | None, step: list[Any], kept: list[bytes]) -> None:
    op, arg = step
    if op == "sleep":
        time.sleep(arg)
    elif op == "send":
        conn.send({"id": rid, **arg})
    elif op == "die":
        os._exit(arg)
    elif op == "grow":
        # Written through, so every page is resident.
        kept.append(b"x" * arg)
    elif op == "logs":
        until = time.monotonic() + arg
        while time.monotonic() < until:
            conn.send(
                {
                    "id": None,
                    "kind": "log",
                    "name": FAKE_LOGGER,
                    "level": logging.WARNING,
                    "message": "flood",
                    "trace": None,
                }
            )
            time.sleep(0.002)


def _fake_child(plan: dict[str, Any], conn: Connection) -> None:
    folder = Path(plan["folder"])
    index = 0
    while True:
        try:
            fd = os.open(folder / f"child-{index}", os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            index += 1
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    scripts = plan["children"]
    script = scripts[min(index, len(scripts) - 1)]
    kept: list[bytes] = []
    for step in script.get("up", []):
        _step(conn, None, step, kept)
    conn.send({"id": None, "kind": "up", "pid": os.getpid()})
    counts: dict[str, int] = {}
    while True:
        try:
            request = conn.recv()
        except (EOFError, OSError):
            return
        kind = request["kind"]
        with open(folder / f"requests-{index}", "a", encoding="utf-8") as log:
            log.write(json.dumps(request) + "\n")
        runs = script.get(kind) or DEFAULTS[kind]
        run = runs[min(counts.get(kind, 0), len(runs) - 1)]
        counts[kind] = counts.get(kind, 0) + 1
        for step in run:
            _step(conn, request["id"], step, kept)


def _checker(
    tmp_path: Path, *children: dict[str, Any], stand_in: str | None = None
) -> tuple[SaveChecker, Path]:
    folder = tmp_path / "fake"
    folder.mkdir()
    checker = SaveChecker(
        scan_root=tmp_path, source_root=tmp_path, subfolder=None, mode="child", stand_in=stand_in
    )
    checker._target = functools.partial(
        _fake_child, {"folder": str(folder), "children": list(children) or [{}]}
    )
    return checker, folder


def _pids(folder: Path) -> list[int]:
    pids = []
    index = 0
    while (folder / f"child-{index}").exists():
        text = (folder / f"child-{index}").read_text()
        pids.append(int(text) if text else 0)
        index += 1
    return pids


def _requests(folder: Path, index: int) -> list[dict[str, Any]]:
    path = folder / f"requests-{index}"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _kinds(folder: Path, index: int) -> list[str]:
    return [request["kind"] for request in _requests(folder, index)]


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def _eventually(condition: Callable[[], bool], seconds: float = 20.0) -> bool:
    until = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > until:
            return False
        time.sleep(0.02)
    return True


async def _until(condition: Callable[[], bool], seconds: float = WAIT) -> None:
    until = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < until, "the checker's child never got there"
        await asyncio.sleep(0.02)


async def _up(checker: SaveChecker) -> None:
    checker.start()
    await _until(lambda: checker._child is not None and checker._child.ready)


async def _idle(checker: SaveChecker) -> None:
    """Waits for a ready child with nothing in it, as after a restart."""
    checker.start()
    await _until(
        lambda: checker._child is not None
        and checker._child.ready
        and checker._child.request is None
        and checker._child.killed is None
        and not checker._child.gone
    )


async def _scan(checker: SaveChecker, seconds: float = WAIT) -> ScanAnswer | NotInTime:
    return await checker.scan(deadline=asyncio.get_running_loop().time() + seconds)


async def _save(
    checker: SaveChecker,
    seconds: float = WAIT,
    asked: set[str] | None = None,
    held: dict[str, str] | None = None,
) -> Saved | Refused | NotInTime:
    return await checker.check(
        dict(PAYLOAD),
        require_setup_contract=False,
        asked=set() if asked is None else asked,
        held={} if held is None else held,
        deadline=asyncio.get_running_loop().time() + seconds,
    )


def _text(result: Any) -> str:
    return result.response["content"][0]["text"]


def _readers() -> int:
    return sum(
        1 for thread in threading.enumerate() if thread.name == "save-checker-reader" and thread.is_alive()
    )


@pytest.fixture
def warnings(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.WARNING)
    return caplog


@pytest.fixture
def thread_checks(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Stands in for today's in-process checks, recording how thread mode called them."""
    from fi.alk.harness import tools

    calls: list[dict[str, Any]] = []

    def checked(payload: dict[str, Any], **kwargs: Any) -> AgentContract:
        calls.append(kwargs)
        return AgentContract.model_validate(CONTRACT)

    def scanned(scan_root: Any, subfolder: Any, reached: Any = None) -> tuple[list[str], frozenset[str]]:
        calls.append({"scan": scan_root})
        return ["in-thread"], frozenset()

    monkeypatch.setattr(tools, "_checked_contract", checked)
    monkeypatch.setattr(tools, "_tools_in_agent_folder", scanned)
    return calls


# -- the pipe's rules ---------------------------------------------------------------------------


def test_a_save_waits_for_both_parts_and_a_message_for_another_request_is_dropped(
    tmp_path: Path,
) -> None:
    stale_stage = ["send", {**_stage("contract", ["stale"], ["stale"])[1], "id": 10_000}]
    checker, folder = _checker(
        tmp_path,
        {
            "session": [
                [
                    ["send", {"id": 10_000, "kind": "scan", "found": ["stale"], "cut": []}],
                    SCAN,
                    ["sleep", 0.5],
                    INSTRUMENTATION,
                ]
            ],
            "save": [[stale_stage, *GOOD_SAVE]],
        },
    )
    asked: set[str] = set()

    async def main() -> tuple[Any, Any]:
        saved = await _save(checker, asked=asked)
        return saved, await _scan(checker)

    saved, scanned = asyncio.run(main())

    assert isinstance(saved, Saved) and saved.contract.agent == "trial"
    assert scanned == FOUND
    assert "stale" not in asked
    assert _kinds(folder, 0) == ["trial", "session", "save"]
    # Only known once the instrumentation part had answered.
    assert _requests(folder, 0)[2]["instrumentation"] == "traceai"
    assert checker.ran_in_child == 1


def test_a_save_past_its_deadline_kills_the_child_and_a_new_one_starts(
    tmp_path: Path, warnings: pytest.LogCaptureFixture
) -> None:
    checker, folder = _checker(tmp_path, {"save": [[_stage("contract"), ["sleep", 60]]]}, {})

    async def main() -> Any:
        await _scan(checker)
        return await _save(checker, seconds=0.5)

    result = asyncio.run(main())

    assert isinstance(result, NotInTime) and _text(result) == SAVE_LATE
    first = _pids(folder)[0]
    assert _eventually(lambda: _gone(first))
    assert _eventually(lambda: checker._child is not None and checker._child.ready)
    assert len(_pids(folder)) == 2
    # The trial passed and the cache is complete: the new child gets neither again.
    assert _kinds(folder, 1) == []
    assert (
        "save checker: the save's deadline passed in tool_evidence, prompt_copy, model, runtime; "
        "nothing saved" in warnings.text
    )
    assert checker._deaths == 0


def test_the_session_job_cut_at_its_bound_is_cached_and_not_run_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warnings: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(save_checker, "SESSION_BOUND", 1.0)
    checker, folder = _checker(tmp_path, {"session": [[["sleep", 60]]]}, {})

    async def main() -> tuple[Any, Any]:
        return await _scan(checker), await _save(checker)

    scanned, saved = asyncio.run(main())

    assert scanned == CUT
    assert isinstance(saved, Saved)
    assert _kinds(folder, 0) == ["trial", "session"]
    assert _kinds(folder, 1) == ["save"]
    assert _requests(folder, 1)[0]["instrumentation"] == "none"
    # The restarted child never got `session`, so the save carries the parent's level.
    assert _requests(folder, 1)[0]["log_level"] == logging.getLogger().level
    assert (
        "save checker: the session's checks ran past 1 s; the scan and instrumentation counts as cut"
        in warnings.text
    )
    assert checker._deaths == 0


def test_a_death_during_the_session_job_keeps_what_answered_and_counts(
    tmp_path: Path, warnings: pytest.LogCaptureFixture
) -> None:
    checker, folder = _checker(tmp_path, {"session": [[INSTRUMENTATION, ["die", 3]]]}, {})

    async def main() -> tuple[Any, int, Any]:
        scanned = await _scan(checker)
        deaths = checker._deaths
        return scanned, deaths, await _save(checker)

    scanned, deaths, saved = asyncio.run(main())

    assert scanned == CUT
    assert deaths == 1
    assert isinstance(saved, Saved)
    assert _kinds(folder, 1) == ["save"]
    assert _requests(folder, 1)[0]["instrumentation"] == "traceai"
    assert "save checker: the child ended (exit code 3)" in warnings.text
    assert "the scan counts as cut" in warnings.text


def test_two_failed_instrumentation_parts_then_saves_go_on_with_none(
    tmp_path: Path, warnings: pytest.LogCaptureFixture
) -> None:
    checker, folder = _checker(tmp_path, {"session": [[SCAN, FAILED_INSTRUMENTATION]]})

    async def main() -> tuple[SessionChecksFailed, Any]:
        with pytest.raises(SessionChecksFailed) as first:
            await _save(checker)
        return first.value, await _save(checker)

    first, second = asyncio.run(main())

    assert (first.part, first.error, first.message) == ("instrumentation", "RuntimeError", "boom")
    assert isinstance(second, Saved)
    # Sent again to the same child after the first failure; cached as cut after the second.
    assert _kinds(folder, 0) == ["trial", "session", "session", "save"]
    assert _requests(folder, 0)[3]["instrumentation"] == "none"
    assert len(_pids(folder)) == 1
    # The type alone at ERROR: the message and trace can quote the customer's paths.
    assert "save checker: instrumentation failed in the child: RuntimeError\n" in warnings.text
    assert "boom" not in warnings.text
    assert "the session's instrumentation failed twice" in warnings.text


def test_two_calls_in_one_message_after_a_first_failure_send_one_session(tmp_path: Path) -> None:
    checker, folder = _checker(
        tmp_path,
        {"session": [[SCAN, FAILED_INSTRUMENTATION], [["sleep", 0.3], SCAN, INSTRUMENTATION]]},
    )

    async def main() -> list[Any]:
        with pytest.raises(SessionChecksFailed):
            await _save(checker)
        return list(await asyncio.gather(_save(checker), _save(checker)))

    results = asyncio.run(main())

    assert all(isinstance(result, Saved) for result in results)
    assert _kinds(folder, 0) == ["trial", "session", "session", "save", "save"]
    assert checker.ran_in_child == 2


def test_a_failed_scan_is_sent_again_and_an_answer_after_a_cut_is_dropped(tmp_path: Path) -> None:
    checker, folder = _checker(
        tmp_path, {"session": [[FAILED_SCAN, INSTRUMENTATION], [FAILED_SCAN, SCAN]]}
    )

    async def main() -> tuple[SessionChecksFailed, Any, Any]:
        with pytest.raises(SessionChecksFailed) as first:
            await _scan(checker)
        second = await _scan(checker)
        # Time for the real answer that follows the second failure to arrive.
        await asyncio.sleep(0.5)
        return first.value, second, await _scan(checker)

    first, second, third = asyncio.run(main())

    assert first.part == "scan"
    assert second == third == CUT
    assert _kinds(folder, 0) == ["trial", "session", "session"]


def test_two_deaths_move_the_checks_into_this_process_for_good(
    tmp_path: Path, warnings: pytest.LogCaptureFixture, thread_checks: list[dict[str, Any]]
) -> None:
    checker, folder = _checker(tmp_path, {"save": [[["die", 7]]]}, stand_in="tests.fake_stand_in")

    async def main() -> list[Any]:
        return [await _save(checker) for _ in range(3)]

    first, second, third = asyncio.run(main())

    assert isinstance(first, NotInTime) and isinstance(second, NotInTime)
    assert isinstance(third, Saved)
    # The restarted child's stand_in answer didn't reset the count.
    assert _kinds(folder, 0) == ["trial", "stand_in", "session", "save"]
    assert _kinds(folder, 1) == ["stand_in", "save"]
    assert checker.ran_in_child == 2
    assert "save checker: running the checks in this process: the child ended 2 times in a row" in (
        warnings.text
    )
    # Thread mode takes the cached instrumentation, and a cached scan's reach isn't passed.
    assert thread_checks[-1]["instrumentation"] == "traceai"
    assert thread_checks[-1]["reached"] is None
    assert _eventually(lambda: all(_gone(pid) for pid in _pids(folder)))


def test_deadline_kills_in_a_row_stay_in_the_child(
    tmp_path: Path, warnings: pytest.LogCaptureFixture
) -> None:
    checker, folder = _checker(tmp_path, {"save": [[["sleep", 60]]]})

    async def main() -> list[Any]:
        await _scan(checker)
        results = []
        for _ in range(3):
            await _idle(checker)
            results.append(await _save(checker, seconds=0.5))
        await _idle(checker)
        return results

    results = asyncio.run(main())

    assert all(isinstance(result, NotInTime) for result in results)
    assert checker.ran_in_child == 3
    assert checker._deaths == 0
    assert len(_pids(folder)) == 4
    assert "running the checks in this process" not in warnings.text
    assert "the child ended" not in warnings.text


def test_a_save_whose_child_dies_answers_with_the_problems_that_arrived(tmp_path: Path) -> None:
    checker, folder = _checker(
        tmp_path,
        {
            "save": [
                [
                    _stage(
                        "contract",
                        ["missing-storage-decision: declare data_store.kind"],
                        ["model"],
                        {"prompt-quote": "q"},
                    ),
                    ["die", 5],
                ]
            ]
        },
        {},
    )
    asked, held = {"tools"}, {"other": "kept"}

    result = asyncio.run(_save(checker, asked=asked, held=held))

    assert isinstance(result, Refused)
    assert "missing-storage-decision" in _text(result)
    assert asked == {"tools", "model"}
    assert held == {"other": "kept", "prompt-quote": "q"}
    assert checker._deaths == 1


def test_a_restart_leaves_one_reader_thread(tmp_path: Path) -> None:
    before = _readers()
    checker, folder = _checker(tmp_path, {"save": [[["sleep", 60]]]}, {})

    async def main() -> None:
        await _scan(checker)
        await _save(checker, seconds=0.3)
        await _idle(checker)

    asyncio.run(main())

    assert len(_pids(folder)) == 2
    assert _eventually(lambda: _readers() == before + 1)


def test_a_stage_exception_fails_the_call_and_is_not_a_death(
    tmp_path: Path, warnings: pytest.LogCaptureFixture
) -> None:
    checker, folder = _checker(tmp_path, {"save": [[_stage("contract"), FAILED_SAVE], GOOD_SAVE]})

    async def main() -> tuple[SaveChecksFailed, Any]:
        with pytest.raises(SaveChecksFailed) as failed:
            await _save(checker)
        return failed.value, await _save(checker)

    failed, saved = asyncio.run(main())

    assert (failed.error, failed.message) == ("KeyError", "'x'")
    assert isinstance(saved, Saved)
    assert len(_pids(folder)) == 1
    assert checker._deaths == 0
    assert "save checker: save failed in the child: KeyError\n" in warnings.text
    assert "'x'" not in warnings.text


def test_a_kill_while_the_child_logs_and_the_next_childs_lines_arrive(
    tmp_path: Path, warnings: pytest.LogCaptureFixture
) -> None:
    checker, folder = _checker(
        tmp_path, {"save": [[["logs", 60]]]}, {"up": [NEXT_CHILD_LINE]}
    )

    async def main() -> tuple[Any, Any]:
        await _scan(checker)
        cut = await _save(checker, seconds=0.5)
        return cut, await _save(checker)

    cut, saved = asyncio.run(main())

    assert isinstance(cut, NotInTime)
    assert isinstance(saved, Saved)
    lines = [record.getMessage() for record in warnings.records if record.name == FAKE_LOGGER]
    assert "flood" in lines
    assert "the next child is up" in lines


def test_the_watchdog_kills_a_child_that_grows_past_its_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warnings: pytest.LogCaptureFixture
) -> None:
    checker, folder = _checker(tmp_path, {"save": [[["grow", 300 * MIB], ["sleep", 60]]]}, {})

    async def main() -> Any:
        await _scan(checker)
        resident = save_checker._rss(_pids(folder)[0])
        assert resident is not None
        monkeypatch.setattr(save_checker, "WATCH_CAP", resident + 150 * MIB)
        return await _save(checker)

    result = asyncio.run(main())

    assert isinstance(result, NotInTime)
    assert "save checker: the child ended (watchdog)" in warnings.text
    assert checker._deaths == 1


def test_the_cache_written_after_its_loop_closed_is_found_on_a_new_loop(
    tmp_path: Path, warnings: pytest.LogCaptureFixture
) -> None:
    checker, folder = _checker(tmp_path, {"session": [[["sleep", 1.0], SCAN, INSTRUMENTATION]]})

    async def early() -> Any:
        await _up(checker)
        return await _scan(checker, seconds=0.2)

    first = asyncio.run(early())
    assert isinstance(first, NotInTime) and _text(first) == SCAN_LATE
    assert _eventually(lambda: checker._scan is not None)

    # A scan in this process would find nothing in an empty folder, so this is the child's answer.
    assert asyncio.run(_scan(checker)) == FOUND
    assert "save checker: a call from a second event loop runs in this process" in warnings.text


@pytest.mark.parametrize(
    ("trial", "why"),
    [
        (
            [["send", {"kind": "trial", "ok": False, "detail": "the scan found []"}]],
            "the trial failed: the scan found []",
        ),
        ([["sleep", 60]], "the trial did not answer within 0.5 s"),
        ([["die", 4]], "the child ended before the trial answered"),
    ],
)
def test_a_failed_or_late_trial_moves_the_checks_into_this_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings: pytest.LogCaptureFixture,
    thread_checks: list[dict[str, Any]],
    trial: list[Any],
    why: str,
) -> None:
    monkeypatch.setattr(save_checker, "TRIAL_SECONDS", 0.5)
    checker, folder = _checker(tmp_path, {"trial": [trial]})

    async def main() -> tuple[Any, Any]:
        return await _scan(checker), await _save(checker)

    scanned, saved = asyncio.run(main())

    assert scanned == ScanAnswer(["in-thread"], frozenset())
    assert isinstance(saved, Saved)
    assert checker.ran_in_child == 0
    assert f"save checker: running the checks in this process: {why}" in warnings.text
    # A failed trial isn't a death.
    assert checker._deaths == 0
    assert _eventually(lambda: all(_gone(pid) for pid in _pids(folder)))


def test_rss_bytes_reads_a_statm_line_and_a_ps_line() -> None:
    assert rss_bytes("statm", "12345 2048 300 10 0 900 0\n", 4096) == 2048 * 4096
    assert rss_bytes("ps", "  51200\n", 4096) == 51200 * 1024
    with pytest.raises(ValueError):
        rss_bytes("statm", "", 4096)
    with pytest.raises(ValueError):
        rss_bytes("ps", "RSS\n", 4096)


def test_ci_runs_this_file() -> None:
    workflows = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "sdk-smoke.yml"
    assert "tests/harness/test_save_checker.py" in workflows.read_text(encoding="utf-8")


# -- what SaveChecker promises submit_contract ------------------------------------------------


def test_a_scan_not_in_time_kills_nothing_and_the_session_job_runs_once(tmp_path: Path) -> None:
    checker, folder = _checker(tmp_path, {"session": [[["sleep", 3.0], SCAN, INSTRUMENTATION]]})

    async def main() -> list[Any]:
        await _up(checker)
        first = await _scan(checker, seconds=0.3)
        second = await _scan(checker, seconds=0.3)
        return [first, second, await _scan(checker)]

    first, second, third = asyncio.run(main())

    assert isinstance(first, NotInTime) and _text(first) == SCAN_LATE
    assert isinstance(second, NotInTime)
    assert third == FOUND
    assert len(_pids(folder)) == 1
    assert not _gone(_pids(folder)[0])
    assert _kinds(folder, 0) == ["trial", "session"]


def test_a_save_cut_in_any_stage_saves_nothing_and_changes_nothing(tmp_path: Path) -> None:
    # The stages that answered set `asked` and `held` with no problem, as the prompt copy's
    # confirmed quote does: a cut applies none of it.
    children = [
        {"save": [[*(_stage(name, (), ["model"], {"prompt-quote-confirmed": "c"}) for name in STAGES[:at]), ["sleep", 60]]]}
        for at in range(5)
    ]
    checker, folder = _checker(tmp_path, *children, {})
    asked, held = {"tools"}, {"prompt-quote": "kept"}

    async def main() -> list[Any]:
        await _scan(checker)
        results = []
        for _ in STAGES:
            await _idle(checker)
            results.append(await _save(checker, seconds=0.5, asked=asked, held=held))
        return results

    results = asyncio.run(main())

    assert all(isinstance(result, NotInTime) for result in results), results
    assert all(_text(result) == SAVE_LATE for result in results)
    assert asked == {"tools"}
    assert held == {"prompt-quote": "kept"}


@pytest.mark.parametrize("at", range(1, len(STAGES)))
def test_a_save_cut_after_a_problem_answers_that_problem(tmp_path: Path, at: int) -> None:
    arrived = [
        _stage(STAGES[0], ["model-unconfirmed: the model isn't cited"], ["model"], {"prompt-quote": "q"}),
        *(_stage(name, (), ["model"], {"prompt-quote": "q"}) for name in STAGES[1:at]),
    ]
    checker, folder = _checker(tmp_path, {"save": [[*arrived, ["sleep", 60]]]}, {})
    asked, held = {"tools"}, {"other": "kept"}

    async def main() -> Any:
        await _scan(checker)
        return await _save(checker, seconds=0.5, asked=asked, held=held)

    result = asyncio.run(main())

    assert isinstance(result, Refused)
    assert "model-unconfirmed" in _text(result)
    assert asked == {"tools", "model"}
    assert held == {"other": "kept", "prompt-quote": "q"}


def test_a_save_in_time_is_saved_or_refused_as_done_says(tmp_path: Path) -> None:
    refused = [
        *(_stage(name, ["prompt-quote: not found"] if name == "prompt_copy" else ()) for name in STAGES),
        [
            "send",
            {
                "kind": "done",
                "contract": None,
                "response": REFUSAL,
                "asked": ["prompt-quote"],
                "held": {"prompt-quote": "q2"},
            },
        ],
    ]
    saved_run = [
        *(_stage(name) for name in STAGES),
        ["send", {**DONE[1], "asked": ["model"], "held": {"confirmed": "c"}}],
    ]
    checker, folder = _checker(tmp_path, {"save": [saved_run, refused]})
    asked, held = {"tools"}, {"prompt-quote": "q1"}

    async def main() -> tuple[Any, Any]:
        first = await _save(checker, asked=asked, held=held)
        snapshot = (set(asked), dict(held))
        return (first, snapshot), await _save(checker, asked=asked, held=held)

    (first, snapshot), second = asyncio.run(main())

    assert isinstance(first, Saved) and first.contract == AgentContract.model_validate(CONTRACT)
    assert snapshot == ({"tools", "model"}, {"prompt-quote": "q1", "confirmed": "c"})
    assert isinstance(second, Refused) and second.response == REFUSAL
    assert asked == {"tools", "model", "prompt-quote"}
    assert held == {"prompt-quote": "q2", "confirmed": "c"}
    assert checker.ran_in_child == 2


def test_a_call_on_a_second_loop_runs_in_this_process_and_kills_nothing(
    tmp_path: Path, warnings: pytest.LogCaptureFixture, thread_checks: list[dict[str, Any]]
) -> None:
    checker, folder = _checker(tmp_path, {})

    first = asyncio.run(_save(checker))

    async def elsewhere() -> list[Any]:
        return [await _save(checker), await _save(checker)]

    later = asyncio.run(elsewhere())

    assert isinstance(first, Saved)
    assert all(isinstance(result, Saved) for result in later)
    assert checker.ran_in_child == 1
    assert [call["instrumentation"] for call in thread_checks] == ["traceai", "traceai"]
    assert warnings.text.count("a call from a second event loop runs in this process") == 1
    assert len(_pids(folder)) == 1 and not _gone(_pids(folder)[0])


def test_a_cancel_while_waiting_kills_nothing(tmp_path: Path) -> None:
    checker, folder = _checker(
        tmp_path,
        {
            "session": [[["sleep", 1.0], SCAN, INSTRUMENTATION]],
            "save": [[["sleep", 1.0], *GOOD_SAVE]],
        },
    )

    async def main() -> Any:
        await _up(checker)
        # Cancelled while it waits on the session job.
        waiting = asyncio.ensure_future(_save(checker))
        await asyncio.sleep(0.3)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        # Cancelled while it waits on the lock behind a save in the child.
        first = asyncio.ensure_future(_save(checker))
        await _until(lambda: checker.ran_in_child == 1)
        behind = asyncio.ensure_future(_save(checker))
        await asyncio.sleep(0.2)
        behind.cancel()
        with pytest.raises(asyncio.CancelledError):
            await behind
        return await first

    result = asyncio.run(main())

    assert isinstance(result, Saved)
    assert len(_pids(folder)) == 1 and not _gone(_pids(folder)[0])
    assert checker.ran_in_child == 1


def test_a_cancel_while_its_own_request_is_in_the_child_kills_and_restarts_it(
    tmp_path: Path, warnings: pytest.LogCaptureFixture
) -> None:
    checker, folder = _checker(tmp_path, {"save": [[["sleep", 60]]]}, {})

    async def main() -> Any:
        await _scan(checker)
        task = asyncio.ensure_future(_save(checker))
        await _until(lambda: checker.ran_in_child == 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return await _save(checker)

    result = asyncio.run(main())

    assert isinstance(result, Saved)
    first, second = _pids(folder)
    assert _eventually(lambda: _gone(first))
    assert not _gone(second)
    assert checker._deaths == 0
    assert "the child ended" not in warnings.text


def test_no_child_outlives_close(tmp_path: Path) -> None:
    before = _readers()
    checker, folder = _checker(tmp_path, {})

    assert asyncio.run(_scan(checker)) == FOUND
    checker.close()
    checker.close()

    assert _eventually(lambda: all(_gone(pid) for pid in _pids(folder)))
    assert _readers() == before


def test_close_during_start_stops_what_start_started(tmp_path: Path) -> None:
    checker, folder = _checker(tmp_path, {})

    checker.start()
    began = time.monotonic()
    checker.close()

    assert time.monotonic() - began < save_checker.JOIN_SECONDS + 1.0
    assert checker._start_thread is not None and not checker._start_thread.is_alive()
    assert _eventually(lambda: all(_gone(pid) for pid in checker.pids))


def test_a_closed_checker_answers_in_this_process(
    tmp_path: Path, warnings: pytest.LogCaptureFixture, thread_checks: list[dict[str, Any]]
) -> None:
    checker, folder = _checker(tmp_path, {})
    assert asyncio.run(_scan(checker)) == FOUND
    checker.close()

    async def main() -> tuple[Any, Any]:
        return await _scan(checker), await _save(checker)

    scanned, saved = asyncio.run(main())

    assert scanned == FOUND
    assert isinstance(saved, Saved)
    assert checker.ran_in_child == 0
    assert thread_checks and thread_checks[-1]["instrumentation"] == "traceai"
    assert warnings.text.count("save checker: closed; running the checks in this process") == 1
    assert len(_pids(folder)) == 1


def test_a_stand_in_that_fails_raises_and_never_falls_back(
    tmp_path: Path, warnings: pytest.LogCaptureFixture
) -> None:
    checker, folder = _checker(
        tmp_path,
        {"stand_in": [[["send", {"kind": "stand_in", "ok": False, "detail": "ModuleNotFoundError: x"}]]]},
        stand_in="tests.missing_stand_in",
    )

    async def main() -> None:
        with pytest.raises(StandInFailed, match="ModuleNotFoundError"):
            await _scan(checker)
        with pytest.raises(StandInFailed):
            await _save(checker)

    asyncio.run(main())

    # The trial checks the real functions before any stand-in is put in place.
    assert _kinds(folder, 0) == ["trial", "stand_in"]
    assert checker.ran_in_child == 0
    assert "running the checks in this process" not in warnings.text
    assert _eventually(lambda: all(_gone(pid) for pid in _pids(folder)))


def test_a_job_with_no_roots_starts_no_child() -> None:
    checker = SaveChecker(scan_root=None, source_root=None, subfolder=None, mode="child")
    checker.start()

    assert checker.pids == [] and checker._start_thread is None


def test_the_default_mode_follows_the_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALK_SAVE_CHECKS", "thread")
    assert default_mode() == "thread"
    monkeypatch.delenv("ALK_SAVE_CHECKS")
    assert default_mode() == "child"


def test_a_child_never_up_after_a_passed_trial_is_a_death_and_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warnings: pytest.LogCaptureFixture
) -> None:
    from fi.alk.harness import save_checker

    monkeypatch.setattr(save_checker, "START_SECONDS", 0.5)
    checker, folder = _checker(tmp_path, {"save": [[["sleep", 60]]]}, {"up": [["sleep", 60]]}, {})

    async def main() -> tuple[Any, Any]:
        await _scan(checker)
        cut = await _save(checker, seconds=0.5)
        return cut, await _save(checker, seconds=10)

    cut, after = asyncio.run(main())

    # Not replaced, the session would answer "not checked in time" until it closed.
    assert isinstance(cut, NotInTime)
    assert isinstance(after, Saved), after
    assert len(_pids(folder)) == 3
    assert "save checker: the child ended (start)" in warnings.text


def test_a_save_whose_deadline_passed_on_the_lock_is_not_sent(
    tmp_path: Path, warnings: pytest.LogCaptureFixture
) -> None:
    checker, folder = _checker(tmp_path, {"save": [[*(_stage(name) for name in STAGES), DONE]]})

    async def main() -> tuple[Any, Any]:
        await _scan(checker)
        await _idle(checker)
        late = await checker.check(
            {"agent": "x"},
            require_setup_contract=False,
            asked=set(),
            held={},
            deadline=asyncio.get_running_loop().time() - 0.001,
        )
        # The lock was given back: the next save in time is sent and saved.
        return late, await _save(checker)

    late, after = asyncio.run(main())

    assert isinstance(late, NotInTime)
    assert isinstance(after, Saved), after
    assert checker.ran_in_child == 1
    assert len(_pids(folder)) == 1


@pytest.mark.skipif(sys.version_info < (3, 11), reason="3.10 keeps wait_for, which can swallow it")
def test_a_cancel_in_the_same_tick_as_the_answer_still_cancels() -> None:
    from fi.alk.harness import save_checker

    async def main() -> None:
        loop = asyncio.get_running_loop()
        answer: asyncio.Future[str] = loop.create_future()
        waiting = asyncio.ensure_future(save_checker._by(answer, loop.time() + 5))
        await asyncio.sleep(0)

        def both() -> None:
            answer.set_result("done")
            waiting.cancel()

        loop.call_soon(both)
        with pytest.raises(asyncio.CancelledError):
            await waiting

    asyncio.run(main())


def test_a_session_answer_after_an_idle_death_does_not_reset_the_death_count(
    tmp_path: Path, warnings: pytest.LogCaptureFixture, thread_checks: list[dict[str, Any]]
) -> None:
    # A part fails, the idle child then dies, and the next child answers the session again
    # before it dies too: only a save's answer resets the count, so that is two in a row.
    checker, folder = _checker(
        tmp_path,
        {"session": [[SCAN, FAILED_INSTRUMENTATION, ["sleep", 0.5], ["die", 3]]]},
        {"save": [[["die", 7]]]},
    )

    async def main() -> tuple[Any, Any]:
        # No call waits on the failed part, so nothing sends the session again before the death.
        await _up(checker)
        await _until(lambda: checker._failures["instrumentation"] == 1 and checker._deaths == 1)
        cut = await _save(checker)
        return cut, await _save(checker)

    cut, after = asyncio.run(main())

    assert isinstance(cut, NotInTime)
    assert isinstance(after, Saved)
    assert checker.ran_in_child == 1
    assert "running the checks in this process: the child ended 2 times in a row" in warnings.text


def test_a_call_waiting_when_the_checker_closes_runs_in_this_process(
    tmp_path: Path, thread_checks: list[dict[str, Any]]
) -> None:
    checker, folder = _checker(tmp_path, {"session": [[["sleep", 5.0], SCAN, INSTRUMENTATION]]})

    async def main() -> Any:
        await _up(checker)
        waiting = asyncio.ensure_future(_save(checker, seconds=4))
        await asyncio.sleep(0.3)
        checker.close()
        return await waiting

    result = asyncio.run(main())

    # Not left to wait out its deadline: a closed checker runs the call here.
    assert isinstance(result, Saved)
    assert checker.ran_in_child == 0
