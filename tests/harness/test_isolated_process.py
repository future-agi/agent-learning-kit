from __future__ import annotations

import asyncio
import os
import shutil
import signal
import stat
import sys
from pathlib import Path

import pytest

from fi.alk.harness.isolated_process import _reap, run_json_worker


@pytest.mark.parametrize("interpreter", [sys.executable, shutil.which("python3.10")])
def test_worker_sigterm_cleans_up_once_on_supported_interpreters(interpreter):
    if interpreter is None:
        pytest.skip("Python 3.10 is not installed")
    import fi.alk.harness.isolated_process as isolated

    script = (
        "import asyncio, importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('worker', {isolated.__file__!r})\n"
        "worker = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(worker)\n"
        "async def main():\n"
        " try:\n"
        "  print('ready', flush=True)\n"
        "  await asyncio.sleep(30)\n"
        " finally:\n"
        "  print('cleaning', flush=True)\n"
        "  await asyncio.sleep(.1)\n"
        "  print('cleaned', flush=True)\n"
        "worker.run_worker(main)\n"
    )

    async def run():
        process = await asyncio.create_subprocess_exec(
            interpreter,
            "-c",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            assert await asyncio.wait_for(process.stdout.readline(), 5) == b"ready\n"
            process.send_signal(signal.SIGTERM)
            assert await asyncio.wait_for(process.stdout.readline(), 5) == b"cleaning\n"
            process.send_signal(signal.SIGTERM)
            stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
            assert stdout == b"cleaned\n"
            assert b"AttributeError" not in stderr
        finally:
            await _reap(process, grace_seconds=0.1)

    asyncio.run(run())


@pytest.fixture
def worker_env(tmp_path: Path) -> dict[str, str]:
    (tmp_path / "fixture_worker.py").write_text(
        "import json, os, sys, time, subprocess\n"
        "from pathlib import Path\n"
        "p = json.load(sys.stdin)\n"
        "if p.get('output'):\n"
        " print('worker stdout', flush=True)\n"
        " print('worker stderr', file=sys.stderr, flush=True)\n"
        "if p.get('child'):\n"
        " subprocess.Popen([sys.executable, '-c', p['child']])\n"
        "if p.get('started'): Path(p['started']).touch()\n"
        "time.sleep(p.get('delay', 0))\n"
        "Path(sys.argv[1]).write_text(json.dumps({'marker': os.environ['MARKER'], 'tmp': os.environ['TMPDIR']}))\n"
    )
    return {**os.environ, "PYTHONPATH": str(tmp_path), "MARKER": "parent"}


@pytest.mark.parametrize("suppress", [False, True])
def test_worker_output_is_inherited_unless_suppressed(tmp_path, worker_env, capfd, suppress):
    options = {"suppress_output": True} if suppress else {}
    result = asyncio.run(
        run_json_worker(
            "fixture_worker",
            {"output": True},
            environ=worker_env,
            work_directory=tmp_path,
            **options,
        )
    )
    assert result["marker"] == "parent"
    captured = capfd.readouterr()
    assert captured.out == ("" if suppress else "worker stdout\n")
    assert captured.err == ("" if suppress else "worker stderr\n")


def test_concurrent_workers_have_private_environment_and_scratch(tmp_path, worker_env):
    async def run():
        return await asyncio.gather(
            *(
                run_json_worker(
                    "fixture_worker",
                    {"delay": 0.1},
                    environ={**worker_env, "MARKER": marker},
                    work_directory=tmp_path,
                )
                for marker in ("first", "second")
            )
        )

    first, second = asyncio.run(run())
    assert [first["marker"], second["marker"]] == ["first", "second"]
    assert first["tmp"] != second["tmp"]
    assert worker_env["MARKER"] == "parent"
    assert not Path(first["tmp"]).exists()
    assert not Path(second["tmp"]).exists()


def test_cancellation_kills_descendants_before_return(tmp_path, worker_env):
    started = tmp_path / "started"
    escaped = tmp_path / "escaped"
    child = f"import time; from pathlib import Path; time.sleep(1); Path({str(escaped)!r}).touch()"

    async def run():
        task = asyncio.create_task(
            run_json_worker(
                "fixture_worker",
                {"child": child, "started": str(started), "delay": 30},
                environ=worker_env,
                work_directory=tmp_path,
            )
        )
        async with asyncio.timeout(5):
            while not started.exists():
                await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(1.2)
        assert not escaped.exists()
        assert not list(tmp_path.glob("worker-*"))

    asyncio.run(run())


def test_cancellation_waits_for_async_cleanup_even_when_cancelled_twice(
    tmp_path, worker_env
):
    started = tmp_path / "started"
    cleaning = tmp_path / "cleaning"
    cleaned = tmp_path / "cleaned"
    (tmp_path / "cleanup_worker.py").write_text(
        "import asyncio, json, sys\n"
        "from pathlib import Path\n"
        "from fi.alk.harness.isolated_process import run_worker\n"
        "async def main():\n"
        " p = json.load(sys.stdin)\n"
        " try:\n"
        "  Path(p['started']).touch()\n"
        "  await asyncio.sleep(30)\n"
        " finally:\n"
        "  Path(p['cleaning']).touch()\n"
        "  await asyncio.sleep(0.2)\n"
        "  Path(p['cleaned']).touch()\n"
        "run_worker(main)\n"
    )

    async def run():
        task = asyncio.create_task(
            run_json_worker(
                "cleanup_worker",
                {
                    "started": str(started),
                    "cleaning": str(cleaning),
                    "cleaned": str(cleaned),
                },
                environ=worker_env,
                work_directory=tmp_path,
            )
        )
        async with asyncio.timeout(5):
            while not started.exists():
                await asyncio.sleep(0.01)
            task.cancel()
            while not cleaning.exists():
                await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert cleaned.exists()
        assert not list(tmp_path.glob("worker-*"))

    asyncio.run(run())


def test_uncooperative_worker_is_killed_after_cleanup_deadline():
    import sys

    async def run():
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(30)",
            stdout=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            assert await asyncio.wait_for(process.stdout.readline(), 5) == b"ready\n"
            await asyncio.wait_for(_reap(process, grace_seconds=0.05), 2)
            assert process.returncode == -9
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    asyncio.run(run())


def test_a_worker_imports_the_same_code_as_its_parent_under_a_filtered_environment(tmp_path, worker_env, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", worker_env["PYTHONPATH"])
    filtered = {key: value for key, value in worker_env.items() if key != "PYTHONPATH"}

    result = asyncio.run(
        run_json_worker("fixture_worker", {}, environ=filtered, work_directory=tmp_path)
    )

    assert result["marker"] == "parent"


def test_a_stopped_worker_seals_and_still_returns_its_result(tmp_path):
    """The stop reaches the worker as a signal it seals on (not SIGTERM's cleanup-only cancel), and the result comes back."""
    import fi.alk.harness.isolated_process as isolated

    (tmp_path / "stopping_worker.py").write_text(
        "import asyncio, json, sys\n"
        "from pathlib import Path\n"
        "from fi.alk.harness import isolated_process\n"
        "async def main():\n"
        " json.load(sys.stdin)\n"
        " try:\n"
        "  await asyncio.sleep(30)\n"
        "  sealed = 'finished'\n"
        " except asyncio.CancelledError:\n"
        "  if not isolated_process.stop_requested():\n"
        "   raise\n"
        "  sealed = 'stopped'\n"
        " Path(sys.argv[1]).write_text(json.dumps({'sealed': sealed}))\n"
        "isolated_process.run_worker(main, cancel_on_stop=True)\n"
    )
    src = str(Path(isolated.__file__).resolve().parents[3])
    environ = {**os.environ, "PYTHONPATH": os.pathsep.join([str(tmp_path), src])}

    async def run():
        started = asyncio.get_running_loop().time()
        stop = lambda: asyncio.get_running_loop().time() - started > 2  # noqa: E731
        result = await run_json_worker(
            "stopping_worker", {}, environ=environ, work_directory=tmp_path / "w", stop_requested=stop
        )
        return result, asyncio.get_running_loop().time() - started

    result, elapsed = asyncio.run(run())
    assert result == {"sealed": "stopped"}
    assert elapsed < 15


def test_a_stopped_worker_exits_without_waiting_on_a_call_stuck_in_a_thread(tmp_path):
    """A stopped call can leave a thread blocked on the agent (a tool waiting on its upstream); the worker hands back
    its sealed result and exits instead of waiting for that thread."""
    import fi.alk.harness.isolated_process as isolated

    (tmp_path / "stuck_worker.py").write_text(
        "import asyncio, json, sys, time\n"
        "from pathlib import Path\n"
        "from fi.alk.harness import isolated_process\n"
        "async def main():\n"
        " json.load(sys.stdin)\n"
        " try:\n"
        "  await asyncio.to_thread(time.sleep, 60)\n"
        " except asyncio.CancelledError:\n"
        "  if not isolated_process.stop_requested():\n"
        "   raise\n"
        " Path(sys.argv[1]).write_text(json.dumps({'sealed': 'stopped'}))\n"
        "isolated_process.run_worker(main, cancel_on_stop=True)\n"
    )
    src = str(Path(isolated.__file__).resolve().parents[3])
    environ = {**os.environ, "PYTHONPATH": os.pathsep.join([str(tmp_path), src])}

    async def run():
        started = asyncio.get_running_loop().time()
        stop = lambda: asyncio.get_running_loop().time() - started > 2  # noqa: E731
        result = await run_json_worker(
            "stuck_worker", {}, environ=environ, work_directory=tmp_path / "w", stop_requested=stop
        )
        return result, asyncio.get_running_loop().time() - started

    result, elapsed = asyncio.run(run())
    assert result == {"sealed": "stopped"}
    assert elapsed < 20


def test_a_new_work_directory_is_this_account_s_alone(tmp_path, worker_env):
    work = tmp_path / "workers"
    result = asyncio.run(run_json_worker("fixture_worker", {}, environ=worker_env, work_directory=work))
    assert result["marker"] == "parent"
    assert stat.S_IMODE(os.lstat(work).st_mode) == 0o700


@pytest.mark.parametrize("made", ["writable by others", "a link"])
def test_a_work_directory_another_account_could_change_is_not_used(tmp_path, worker_env, made):
    """A worker's result is written in its work directory: one others may write in, or a link to one, is passed
    over for a new private one, and the worker still runs."""
    work = tmp_path / "workers"
    if made == "a link":
        (tmp_path / "elsewhere").mkdir(mode=0o700)
        work.symlink_to(tmp_path / "elsewhere")
    else:
        work.mkdir()
        work.chmod(0o777)
    result = asyncio.run(run_json_worker("fixture_worker", {}, environ=worker_env, work_directory=work))
    assert result["marker"] == "parent"
    scratch = Path(result["tmp"]).resolve()
    assert not scratch.is_relative_to(work.resolve()) and not scratch.is_relative_to((tmp_path / "elsewhere").resolve())
    # The private directory used instead is gone with the worker.
    assert not scratch.parent.exists()


@pytest.mark.parametrize(("parent_mode", "used"), [(0o777, False), (0o1777, True)])
def test_a_private_work_directory_under_a_parent_others_may_write_is_used_only_if_sticky(
    tmp_path, worker_env, parent_mode, used
):
    """Another account that may write in the parent could rename the private directory away and put its own in
    its place; a sticky parent lets only the directory's owner move it."""
    parent = tmp_path / "shared"
    parent.mkdir()
    parent.chmod(parent_mode)
    work = parent / "workers"
    work.mkdir(mode=0o700)
    result = asyncio.run(run_json_worker("fixture_worker", {}, environ=worker_env, work_directory=work))
    assert result["marker"] == "parent"
    assert Path(result["tmp"]).resolve().is_relative_to(work.resolve()) is used
