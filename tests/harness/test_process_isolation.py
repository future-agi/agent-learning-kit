"""Per-user isolation: customer processes run as their own OS users, and nothing else does."""

from __future__ import annotations

import os
import pwd
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fi.alk.harness import hosted_authoring_entrypoint, hosted_entrypoint, privilege
from fi.alk.harness import process_runtime as pr
from fi.alk.harness.privilege import IsolationMode, IsolationUnavailable
from fi.alk.harness.provider_lifecycle import (
    LifecycleInvocation,
    run_lifecycle_invocation,
)

_ALL = "00000000000000eb"  # chown, dac_override, fowner, kill, setgid, setuid


@pytest.fixture(autouse=True)
def _fixed_user_after_each_test(monkeypatch):
    monkeypatch.setattr(privilege, "_mode", IsolationMode.FIXED_USER)


@pytest.fixture
def per_user(monkeypatch):
    monkeypatch.setattr(privilege, "_mode", IsolationMode.PER_USER)


def _status(tmp_path: Path, capabilities: str) -> Path:
    path = tmp_path / "status"
    path.write_text(f"Name:\tpython\nCapEff:\t{capabilities}\n", encoding="utf-8")
    return path


# --- the mode ----------------------------------------------------------------------------------


def test_the_granted_capabilities_turn_on_per_user_isolation(tmp_path, monkeypatch):
    cleared, umasks = [], []
    monkeypatch.setattr(privilege, "_clear_ambient_capabilities", lambda: cleared.append(1))
    monkeypatch.setattr(privilege.os, "umask", lambda mask: umasks.append(mask) or 0o022)

    mode = privilege.establish_isolation(
        {privilege.ISOLATION_ENV: "required"}, status_path=_status(tmp_path, _ALL)
    )

    assert mode is IsolationMode.PER_USER
    assert privilege.isolation_mode() is IsolationMode.PER_USER
    assert cleared == [1]  # no capability reaches a child
    assert umasks == [0o077]


@pytest.mark.parametrize("capabilities", ["0000000000000000", "00000000000000c0"])
def test_without_the_grant_every_process_stays_the_harness_s_own_user(
    tmp_path, monkeypatch, capabilities
):
    monkeypatch.setattr(
        privilege, "_clear_ambient_capabilities", lambda: pytest.fail("cleared")
    )

    mode = privilege.establish_isolation({}, status_path=_status(tmp_path, capabilities))

    assert mode is IsolationMode.FIXED_USER


def test_no_proc_filesystem_is_fixed_user(tmp_path):
    assert (
        privilege.establish_isolation({}, status_path=tmp_path / "absent")
        is IsolationMode.FIXED_USER
    )


@pytest.mark.parametrize("capabilities", ["0000000000000000", "00000000000000c0"])
def test_a_required_isolation_without_the_capabilities_refuses_to_start(
    tmp_path, capabilities
):
    with pytest.raises(IsolationUnavailable, match="process_isolation_unavailable"):
        privilege.establish_isolation(
            {privilege.ISOLATION_ENV: "required"},
            status_path=_status(tmp_path, capabilities),
        )


@pytest.mark.parametrize(
    "entrypoint,argv",
    [
        (hosted_authoring_entrypoint.main, []),
        (hosted_entrypoint.main, ["job.json", "--source", "s", "--output", "o"]),
    ],
)
def test_an_entrypoint_that_cannot_isolate_exits_as_infrastructure(
    monkeypatch, capsys, entrypoint, argv
):
    def unavailable():
        raise IsolationUnavailable("process_isolation_unavailable: no capabilities")

    module = sys.modules[entrypoint.__module__]
    monkeypatch.setattr(module, "establish_isolation", unavailable)

    # 2, not 78 (a deterministic environment failure): the platform retries it as
    # infrastructure, within its attempt budget.
    assert entrypoint(argv) == 2
    assert "process_isolation_unavailable" in capsys.readouterr().err


# --- the declared-user seams -------------------------------------------------------------------


def test_a_declared_user_runs_with_no_groups_and_the_usual_umask(per_user, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        pr.subprocess, "run", lambda args, **kwargs: seen.update(kwargs) or "done"
    )

    assert pr.declared_user_run(["true"], user=2001, group=2001) == "done"

    assert seen["extra_groups"] == [] and seen["umask"] == 0o022
    assert (seen["user"], seen["group"]) == (2001, 2001)


def test_without_isolation_the_seam_passes_its_arguments_through(monkeypatch):
    seen = {}
    monkeypatch.setattr(pr.subprocess, "run", lambda args, **kwargs: seen.update(kwargs))

    pr.declared_user_run(["true"], user=os.getuid(), group=os.getgid())

    # The fixed sandbox user cannot setgroups(); today's arguments stay exactly as they were.
    assert "extra_groups" not in seen and "umask" not in seen


@pytest.mark.parametrize(
    "launch",
    [
        lambda tmp_path: pr.declared_user_run(["true"]),
        lambda tmp_path: pr._run_build_step(["true"], cwd=tmp_path),
        lambda tmp_path: pr.default_process_runner(
            ["true"], cwd=tmp_path, env={}, log_path=tmp_path / "process.log"
        ),
    ],
    ids=["sync_run", "build_step", "process_runner"],
)
def test_isolation_refuses_a_customer_process_without_a_declared_user(
    per_user, tmp_path, monkeypatch, launch
):
    monkeypatch.setattr(
        pr.subprocess, "Popen", lambda *a, **k: pytest.fail("a process was started")
    )
    monkeypatch.setattr(
        pr.subprocess, "run", lambda *a, **k: pytest.fail("a process was started")
    )

    with pytest.raises(pr.ProcessRuntimeError) as refused:
        launch(tmp_path)

    assert refused.value.code == "internal_undeclared_user"
    assert refused.value.domain is pr.FailureDomain.INFRASTRUCTURE


def test_the_sandbox_resolves_declared_users_only_under_isolation(monkeypatch):
    assert pr.hosted_user_resolution() == {
        "user_resolver": pr.fixed_sandbox_user_resolver,
        "require_declared_user": False,
    }
    monkeypatch.setattr(privilege, "_mode", IsolationMode.PER_USER)
    assert pr.hosted_user_resolution() == {
        "user_resolver": pr.default_user_resolver,
        "require_declared_user": True,
    }


# --- provider lifecycle: repository code, so the process's own user --------------------------


def _invocation(tmp_path: Path) -> LifecycleInvocation:
    return LifecycleInvocation(
        argv=("python", "provider_target.py"),
        cwd=tmp_path,
        environment={},
        output_path=tmp_path / "receipt.json",
        timeout_seconds=5,
    )


def test_a_lifecycle_command_runs_as_the_identity_it_is_given(tmp_path):
    seen = []

    def run(argv, **kwargs):
        seen.append(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    run_lifecycle_invocation(
        _invocation(tmp_path), log_path=tmp_path / "log", run=run, user=2001, group=2001
    )
    run_lifecycle_invocation(_invocation(tmp_path), log_path=tmp_path / "log", run=run)

    assert (seen[0]["user"], seen[0]["group"]) == (2001, 2001)
    assert "user" not in seen[1]  # no identity: today's call, unchanged


def test_the_lifecycle_user_owns_its_directory_and_what_it_holds(tmp_path):
    lifecycle = tmp_path / "provider" / "w0"
    lifecycle.mkdir(parents=True)
    (lifecycle / "context.json").write_text("{}")
    owned = []
    provider = object.__new__(pr.ProcessRuntimeProvider)
    provider._context = SimpleNamespace(
        user_resolver=lambda name: SimpleNamespace(pw_uid=2001, pw_gid=2001),
        require_declared_user=True,
        chown=lambda path, uid, gid: owned.append((path.name, uid, gid)),
    )
    process = SimpleNamespace(name="agent", user=pr.ProcessUser.SVC_AGENT)

    assert provider._lifecycle_user(process, lifecycle) == (2001, 2001)
    assert sorted(owned) == [("context.json", 2001, 2001), ("w0", 2001, 2001)]


# --- layout and record -------------------------------------------------------------------------


def test_a_declared_user_can_pass_through_to_its_own_trees_but_not_list_them(tmp_path):
    previous = os.umask(0o077)
    try:
        pr._open_for_declared_users(tmp_path / "work", worlds=2)
    finally:
        os.umask(previous)

    for relative in (
        "", "build", "worlds", "managed", "provider", "worlds/w0", "worlds/w1"
    ):
        mode = (tmp_path / "work" / relative).stat().st_mode & 0o777
        assert mode == 0o711, relative


def test_the_build_record_names_the_isolation_mode(per_user):
    output = pr.BuildOutput(bundle_digest="sha256:" + "0" * 64, stores=[])

    assert output.to_json()["process_isolation"] == "per_user"


# --- the real boundary (Linux, as root: the hosted proof runs this) --------------------------


@pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() != 0,
    reason="needs Linux and the capabilities to change users",
)
def test_a_declared_user_child_has_no_capabilities_and_cannot_read_a_held_back_case(
    per_user, tmp_path
):
    private = tmp_path / "bundle" / "scenarios"
    private.mkdir(parents=True)
    private.chmod(0o700)
    (private / "held.json").write_text("CANARY")
    (private / "held.json").chmod(0o600)
    tmp_path.chmod(0o711)
    nobody = pwd.getpwnam("nobody")

    done = pr.declared_user_run(
        [
            "sh",
            "-c",
            "grep -E 'CapEff|Groups' /proc/self/status; cat "
            f"{private / 'held.json'} || echo DENIED",
        ],
        user=nobody.pw_uid,
        group=nobody.pw_gid,
        capture_output=True,
        text=True,
        cwd="/",
    )

    assert "CANARY" not in done.stdout and "DENIED" in done.stdout
    assert "CapEff:\t0000000000000000" in done.stdout
    assert any(line.split() == ["Groups:"] for line in done.stdout.splitlines())
