"""Per-user isolation: customer processes run as their own OS users, and nothing else does."""

from __future__ import annotations

import os
import pwd
import shutil
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


def _hardlinks(tmp_path: Path, value: str = "1") -> Path:
    path = tmp_path / "protected_hardlinks"
    path.write_text(f"{value}\n", encoding="ascii")
    return path


# --- the mode ----------------------------------------------------------------------------------


def test_the_granted_capabilities_turn_on_per_user_isolation(tmp_path, monkeypatch):
    cleared, umasks, enabled = [], [], []
    monkeypatch.setattr(privilege, "_clear_ambient_capabilities", lambda: cleared.append(1))
    monkeypatch.setattr(privilege.os, "umask", lambda mask: umasks.append(mask) or 0o022)
    monkeypatch.setattr(privilege.containment, "enable", enabled.append)
    subtree = privilege.containment.Containment(tmp_path / "cgroup")

    mode = privilege.establish_isolation(
        {privilege.ISOLATION_ENV: "required"},
        status_path=_status(tmp_path, _ALL),
        protected_hardlinks=_hardlinks(tmp_path),
        contain=lambda: subtree,
    )

    assert mode is IsolationMode.PER_USER
    assert privilege.isolation_mode() is IsolationMode.PER_USER
    assert cleared == [1]  # no capability reaches a child
    assert umasks == [0o077]
    assert enabled == [subtree]  # every customer execution in a cgroup of its own


def test_a_required_isolation_without_containment_refuses_to_start(tmp_path, monkeypatch):
    monkeypatch.setattr(
        privilege, "_clear_ambient_capabilities", lambda: pytest.fail("isolation went on")
    )

    with pytest.raises(IsolationUnavailable, match="process_isolation_unavailable: .*cgroup"):
        privilege.establish_isolation(
            {privilege.ISOLATION_ENV: "required"},
            status_path=_status(tmp_path, _ALL),
            protected_hardlinks=_hardlinks(tmp_path),
            contain=lambda: None,
        )


def test_without_containment_or_a_request_it_runs_as_before_and_says_so(
    tmp_path, monkeypatch, caplog
):
    # A local Docker harness: the capabilities, but a read-only cgroupfs and no request.
    monkeypatch.setattr(
        privilege, "_clear_ambient_capabilities", lambda: pytest.fail("isolation went on")
    )
    monkeypatch.setattr(privilege.os, "umask", lambda mask: pytest.fail("umask changed"))

    mode = privilege.establish_isolation(
        {},
        status_path=_status(tmp_path, _ALL),
        protected_hardlinks=_hardlinks(tmp_path),
        contain=lambda: None,
    )

    assert mode is IsolationMode.FIXED_USER
    assert "per-user isolation is off: no writable cgroup" in caplog.text


@pytest.mark.parametrize("value", ["0", None], ids=["off", "unreadable"])
def test_a_required_isolation_where_a_user_can_hard_link_any_file_refuses_to_start(
    tmp_path, monkeypatch, value
):
    # The link-count checks before the harness acts on a user's file hold only with this on.
    monkeypatch.setattr(
        privilege, "_clear_ambient_capabilities", lambda: pytest.fail("isolation went on")
    )
    hardlinks = tmp_path / "absent" if value is None else _hardlinks(tmp_path, value)

    with pytest.raises(IsolationUnavailable, match="process_isolation_unavailable: .*hard-link"):
        privilege.establish_isolation(
            {privilege.ISOLATION_ENV: "required"},
            status_path=_status(tmp_path, _ALL),
            protected_hardlinks=hardlinks,
            contain=lambda: pytest.fail("a cgroup subtree was made"),
        )


def test_where_a_user_can_hard_link_any_file_it_runs_as_before_and_says_so(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(
        privilege, "_clear_ambient_capabilities", lambda: pytest.fail("isolation went on")
    )
    subtree = privilege.containment.Containment(tmp_path / "cgroup")

    mode = privilege.establish_isolation(
        {},
        status_path=_status(tmp_path, _ALL),
        protected_hardlinks=_hardlinks(tmp_path, "0"),
        contain=lambda: subtree,
    )

    assert mode is IsolationMode.FIXED_USER
    assert "fs.protected_hardlinks is not 1" in caplog.text


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
        pr.containment, "run", lambda args, **kwargs: seen.update(kwargs) or "done"
    )

    assert pr.declared_user_run(["true"], user=2001, group=2001) == "done"

    assert seen["extra_groups"] == [] and seen["umask"] == 0o022
    assert (seen["user"], seen["group"]) == (2001, 2001)


def test_without_isolation_the_seam_passes_its_arguments_through(monkeypatch):
    seen = {}
    monkeypatch.setattr(pr.containment, "run", lambda args, **kwargs: seen.update(kwargs))

    pr.declared_user_run(["true"], user=os.getuid(), group=os.getgid())

    # The fixed sandbox user cannot setgroups(); today's arguments stay exactly as they were.
    assert "extra_groups" not in seen and "umask" not in seen


@pytest.mark.parametrize(
    "launch",
    [
        lambda tmp_path: pr.declared_user_run(["true"]),
        lambda tmp_path: pr.declared_user_run(["true"], cwd=tmp_path, timeout=5),
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


def test_the_lifecycle_user_owns_its_directory_and_what_it_holds(tmp_path, chown_path):
    lifecycle = tmp_path / "provider" / "w0"
    lifecycle.mkdir(parents=True)
    (lifecycle / "context.json").write_text("{}")
    # An output under nested folders the harness made with its private umask.
    (lifecycle / "receipts" / "deep").mkdir(parents=True)
    owned = []
    provider = object.__new__(pr.ProcessRuntimeProvider)
    provider._context = SimpleNamespace(
        user_resolver=lambda name: SimpleNamespace(pw_uid=2001, pw_gid=2001),
        require_declared_user=True,
        chown=lambda path, uid, gid, dir_fd=None: owned.append(
            (chown_path(path, dir_fd).name, uid, gid)
        ),
    )
    process = SimpleNamespace(name="agent", user=pr.ProcessUser.SVC_AGENT)

    assert provider._lifecycle_user(process, lifecycle) == (2001, 2001)
    assert sorted(owned) == [
        ("context.json", 2001, 2001),
        ("deep", 2001, 2001),
        ("receipts", 2001, 2001),
        ("w0", 2001, 2001),
    ]


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


def test_a_digest_rebuild_opens_the_directories_it_just_recreated(tmp_path, monkeypatch):
    work = tmp_path / "work"
    provider = object.__new__(pr.ProcessRuntimeProvider)
    provider._manifest = object()
    provider._bundle_digest = "old"
    provider._secrets_loaded = True

    def teardown(work_directory):
        for name in ("build", "worlds", "managed", "provider"):
            shutil.rmtree(work_directory / name, ignore_errors=True)

    class Stop(Exception):
        pass

    def stop(work_directory):
        raise Stop

    monkeypatch.setattr(pr, "isolation_mode", lambda: pr.IsolationMode.PER_USER)
    monkeypatch.setattr(pr, "_open_seed_files_for_declared_users", lambda *a: None)
    monkeypatch.setattr(provider, "_teardown_processes_and_directories", teardown, raising=False)
    monkeypatch.setattr(provider, "_verify_secret_refs_resolved", stop, raising=False)

    previous = os.umask(0o077)
    try:
        pr._open_for_declared_users(work, worlds=2)
        with pytest.raises(Stop):
            provider._provision_sync(
                SimpleNamespace(digest="new", seed=None, processes=[]),
                source=tmp_path,
                bundle_dir=tmp_path,
                work_directory=work,
                instances=2,
                require_declared_user=True,
            )
    finally:
        os.umask(previous)

    for relative in ("build", "worlds", "managed", "provider", "worlds/w0", "worlds/w1"):
        assert (work / relative).stat().st_mode & 0o777 == 0o711, relative


def test_a_store_s_user_can_read_its_declared_seed_files_and_nothing_else(tmp_path):
    bundle = tmp_path / "bundle"
    previous = os.umask(0o077)
    try:
        for relative in (
            "seed/source-schema.sql",
            "seed/world.sql",
            "db/v1/schema.sql",
            "seed/notes.txt",
            "scenarios/held-back/case.json",
            "manifest.json",
        ):
            (bundle / relative).parent.mkdir(parents=True, exist_ok=True)
            (bundle / relative).write_text("-- written by the harness\n")
        store = SimpleNamespace(
            migrations=["seed/source-schema.sql", "db/v1/schema.sql"],
            seed_files=["seed/world.sql", "seed/missing.sql"],
        )
        pr._open_seed_files_for_declared_users(
            SimpleNamespace(seed=SimpleNamespace(stores=[store])), bundle
        )
        pr._open_seed_files_for_declared_users(SimpleNamespace(seed=None), bundle)
    finally:
        os.umask(previous)

    def mode(relative: str) -> int:
        return (bundle / relative).stat().st_mode & 0o777

    for declared in ("seed/source-schema.sql", "seed/world.sql", "db/v1/schema.sql"):
        assert mode(declared) == 0o644, declared
    for passage in ("", "seed", "db", "db/v1"):
        assert mode(passage) == 0o711, passage
    # The held-back cases, and every file the manifest does not name, stay the harness's own.
    for private in ("scenarios", "scenarios/held-back"):
        assert mode(private) == 0o700, private
    for private in ("scenarios/held-back/case.json", "manifest.json", "seed/notes.txt"):
        assert mode(private) == 0o600, private


def test_per_user_provisioning_opens_the_seed_files_before_anything_runs(
    per_user, tmp_path, monkeypatch
):
    class Stop(Exception):
        pass

    opened = []

    def record(manifest, bundle_dir):
        opened.append((manifest, bundle_dir))
        raise Stop

    monkeypatch.setattr(pr, "_open_seed_files_for_declared_users", record)
    bundle = SimpleNamespace(digest="sha256:" + "0" * 64, seed=None, processes=[])
    provider = object.__new__(pr.ProcessRuntimeProvider)

    with pytest.raises(Stop):
        provider._provision_sync(
            bundle,
            source=tmp_path / "source",
            bundle_dir=tmp_path / "bundle",
            work_directory=tmp_path / "work",
            instances=1,
            require_declared_user=True,
        )
    assert opened == [(bundle, tmp_path / "bundle")]


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


# The harness as an entrypoint starts it: isolation first, then each phase in a phase worker.
_WORKSPACE_PHASES = r"""
import asyncio, json, os, pwd, random, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fi.alk.harness import privilege
from fi.alk.harness.hosted_scheduler import _invoke
from fi.alk.harness.scenario_source import _compile_entry
from fi.alk.harness.world.handle import HostedWorld

try:
    mode = privilege.establish_isolation()
except privilege.IsolationUnavailable:
    mode = None
if mode is not privilege.IsolationMode.PER_USER:
    print("SKIP")
    sys.exit(0)


class Store:
    def dsn(self):
        return "postgresql://unused"

    def freeze(self):
        return "baseline"

    def restore(self, value):
        pass


agent = pwd.getpwnam("svc-agent")
root = Path(sys.argv[1])
root.mkdir(mode=0o700)
os.chown(root, agent.pw_uid, agent.pw_gid)
world = HostedWorld(
    Store(), 0, random.Random(0), {}, _validate_baseline=False,
    workspace_roots={"project": root},
)
phases = [
    ("setup", "def setup(world):\n    world.write_file('project', 'native.txt', 'from child')\n", world),
    # True only when the worker itself is refused the workspace and the harness reads it.
    ("ready", (
        "def ready(world):\n"
        "    import os\n"
        "    try:\n"
        f"        os.listdir({str(root)!r})\n"
        "    except PermissionError:\n"
        "        return world.read_file('project', 'native.txt') == 'from child'\n"
        "    return 'the phase worker opened the workspace itself'\n"
    ), world.read_only()),
    ("check", "def check(world):\n    return world.read_file('project', 'native.txt')\n", world.read_only()),
]
results = {}
for entry, source, handle in phases:
    fn = _compile_entry(source, label="workspace", entry=entry)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            results[entry] = asyncio.run(
                _invoke(fn, handle, timeout=60, phase=entry, executor=executor)
            )
    except Exception as exc:
        results[entry] = f"raised {type(exc).__name__}: {exc}"
written = root / "native.txt"
print(json.dumps({
    "results": results,
    "root_mode": oct(os.stat(root).st_mode & 0o777),
    "file_owner_is_agent": written.exists() and os.stat(written).st_uid == agent.pw_uid,
}))
"""


@pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() == 0,
    reason="needs Linux and the harness's own user holding the six capabilities",
)
def test_a_workspace_bundle_runs_setup_ready_and_check_under_per_user_isolation(tmp_path):
    import json
    import subprocess

    done = subprocess.run(
        [sys.executable, "-c", _WORKSPACE_PHASES, str(tmp_path / "workspace")],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if done.stdout.strip() == "SKIP":
        pytest.skip("per-user isolation is not available to this process")
    assert done.returncode == 0, done.stderr[-2000:]
    report = json.loads(done.stdout.strip().splitlines()[-1])

    # The workspace is the agent's, 0700: the phase worker cannot open it (ready), the harness
    # can, and what it writes stays the agent's.
    assert report["root_mode"] == "0o700"
    assert report["results"] == {"setup": None, "ready": True, "check": "from child"}
    assert report["file_owner_is_agent"]


def test_every_declared_process_user_exists_in_the_images_and_the_template_checks_it():
    """A bundle that declares a user the image lacks fails to spawn under per-user isolation, so each user the bundle
    may declare (the world's door's `svc-door` included) is made in both images and checked by the template build."""
    from fi.alk.harness.bundle_v2 import ProcessUser

    root = Path(__file__).resolve().parents[2]
    uids = {"svc-agent": 2001, "svc-tools": 2002, "svc-data": 2003, "svc-door": 2004}
    assert {one.value for one in ProcessUser} == set(uids)
    template = (root / "scripts" / "e2b-template.py").read_text()
    for image in ("Dockerfile.hosted", "Dockerfile.sandbox"):
        dockerfile = (root / image).read_text()
        for user, uid in uids.items():
            assert f"useradd --uid {uid} --gid {uid} --create-home --shell /usr/sbin/nologin {user}" in dockerfile
    for user, uid in uids.items():
        assert f'test "$(id -u {user})" = {uid}' in template


@pytest.mark.skipif(os.geteuid() == 0, reason="root may write any file")
def test_the_agent_s_user_can_write_nothing_in_the_harness_s_own_source(tmp_path):
    import stat

    from fi.alk.harness.privilege import writable_harness_source

    source = tmp_path / "fi"
    (source / "alk" / "harness").mkdir(parents=True)
    (source / "alk" / "harness" / "checks.py").write_text("# the grader\n")
    (source / "evals").mkdir()
    # As unpacked by the user that runs it: every part writable, the grader among it.
    found = writable_harness_source([source])
    assert str(source / "alk" / "harness" / "checks.py") in found and str(source / "evals") in found

    for path in [*source.rglob("*"), source]:
        path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
    try:
        assert writable_harness_source([source]) == []
    finally:
        for path in [source, *source.rglob("*")]:
            path.chmod(stat.S_IMODE(path.stat().st_mode) | 0o200)


def test_the_template_refuses_a_harness_source_its_user_can_write():
    root = Path(__file__).resolve().parents[2]
    template = (root / "scripts" / "e2b-template.py").read_text()
    assert '"harness-source-read-only"' in template
    assert "writable_harness_source" in template and "sys.exit(1 if found else 0)" in template


def test_a_proposer_s_candidate_in_the_certify_sandbox_gets_no_author_credential_and_no_verifier_evidence(
    tmp_path, monkeypatch
):
    """The fix engine's code reaches a sandbox only as a candidate agent version, run as the agent's user in the certify
    sandbox, beside the authoring credentials and the suite's proofs. Its environment carries none of those
    credentials, and under per-user isolation every proof the certify writes (the reference's and controls' verdicts,
    the live results) is the harness's alone."""
    from fi.alk.harness import live_proof, prove_suite
    from fi.alk.harness.proofs import PROOFS, record_proof
    from fi.alk.harness.prove import Proof

    names = sorted(hosted_authoring_entrypoint._PASSTHROUGH | hosted_authoring_entrypoint._OBSERVABILITY_PASSTHROUGH)
    monkeypatch.setattr(os, "environ", {"PATH": "/usr/bin"})
    hosted_authoring_entrypoint._configure_generation_environment({name: f"author-{name}" for name in names})
    hosted_authoring_entrypoint._configure_observability_environment({name: f"author-{name}" for name in names})
    candidate = pr._allowlisted_ambient_env(dict(os.environ))
    assert not [name for name in candidate if candidate[name].startswith("author-")]

    monkeypatch.setattr(privilege, "_mode", IsolationMode.PER_USER)
    before = os.umask(0o077)  # what establish_isolation sets under per-user isolation
    try:
        record_proof(tmp_path, "refund", Proof())
        live_proof.write_live(tmp_path, {"refund": {"with_solution": [], "controls": []}})
        prove_suite._write(tmp_path / "scenario-proofs.json", {})
    finally:
        os.umask(before)
    for name in (PROOFS, live_proof.LIVE_PROOFS, "scenario-proofs.json"):
        assert (tmp_path / name).stat().st_mode & 0o077 == 0, name
