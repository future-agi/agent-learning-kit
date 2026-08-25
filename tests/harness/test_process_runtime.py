"""The execution half of the provisioner (`process_runtime.py`), per `hosted-execution-seams.md`
v1.8 §2b/§3/§4. Manifests here are built directly through `EnvironmentBundleV2.model_validate` —
no digest sealing, no on-disk bundle — since every rule under test needs nothing but the parsed
model's own field values plus the job-supplied inputs (instances, secrets, credentials). Digest
and file-content verification are `test_process_preflight.py`'s job, not this module's.

No Docker, no real postgres/redis/rabbitmq anywhere: managed-engine spawn is exercised only
through fakes (`ProcessRunner`, `sync_run`), since §0 assumes those binaries are on the snapshot's
PATH and this test lane must not require that. Real subprocess spawn IS exercised, through tiny
`python3 -c ...` stand-in scripts — proving `default_process_runner` genuinely spawns, captures
output, and reports liveness, without any engine-specific plumbing.

P5's fix pass (F1/F2/F3/F7) made real filesystem operations security-relevant, so those are
exercised directly against real symlinks/tmp dirs where a fake would hide the exact class of bug
being fixed. `os.chown`/`Popen(user=...)` to a FOREIGN uid needs root, which this lane does not
have and must not require — those paths are verified STRUCTURALLY: a fake `chown`/`user_resolver`
is injected and the call it WOULD make is asserted, rather than performing the real privileged
syscall.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import time
import types
from pathlib import Path
from typing import Any, Callable

import pytest

from fi.alk.harness.bundle import CapabilityProtocol
from fi.alk.harness.bundle_v2 import (
    BUNDLE_V2_SCHEMA_VERSION,
    CapabilityV2,
    EnvironmentBundleV2,
    ProcessUser,
    SourceProcess,
)
from fi.alk.harness import process_runtime as pr

# --- shared manifest builder -----------------------------------------------------------------
#
# One postgres store (job-shared, `template_database`), one `tools-api` http-capability source
# process, one `agent` control-service source process claiming the job's only `target_provider`
# secret — the same shape `test_process_preflight.py` uses, so port/ordinal numbers a reader
# already knows from that file carry over here.


def _body(mutate: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": BUNDLE_V2_SCHEMA_VERSION,
        "name": "demo",
        "digest": "sha256:" + "0" * 64,
        "runtime": {"kind": "process", "control_service": "agent", "evidence_seam": "http_tool"},
        "processes": [
            {
                "name": "postgres", "kind": "managed", "engine": "postgres", "version": "16",
                "user": "svc-data", "depends_on": [],
            },
            {
                "name": "tools-api", "kind": "source", "working_directory": "services/tools-api",
                "build_commands": [["npm", "ci"]], "run_command": ["node", "server.js"],
                "environment": {
                    "DATABASE_URL": "{{DATABASE_URL}}", "PORT": "{{PORT_tools-api}}",
                    "TMPDIR": "{{WORLD_DIR}}",
                },
                "secret_purposes": [], "user": "svc-tools", "depends_on": ["postgres"],
            },
            {
                "name": "agent", "kind": "source", "working_directory": ".",
                "build_commands": [["pip", "install", "-r", "requirements.txt"]],
                "run_command": ["python3", "agent.py"],
                "environment": {
                    "DATABASE_URL": "{{DATABASE_URL}}", "TOOLS_API_URL": "{{TOOLS_API_URL}}",
                    "NAME": "agent-w{{WORLD_INDEX}}",
                },
                "secret_purposes": ["target_provider"], "user": "svc-agent",
                "depends_on": ["postgres", "tools-api"],
            },
        ],
        "capabilities": {
            "database": {
                "protocol": "postgres", "service": "postgres", "configuration_name": "DATABASE_URL",
            },
            "tools": {
                "protocol": "http", "service": "tools-api", "configuration_name": "TOOLS_API_URL",
            },
        },
        "readiness": [
            {
                "capability": "tools", "path": "/health", "timeout_seconds": 5,
                "interval_seconds": 0.1,
            },
        ],
        "seed": {
            "stores": [
                {
                    "capability": "database", "migrations": [], "seed_files": [],
                    "baseline": {
                        "strategy": "template_database", "inputs_digest": "sha256:" + "a" * 64
                    },
                    "sentinel": {"query": "SELECT 1", "expected": "1"},
                }
            ]
        },
        "provenance": {
            "source_kind": "repository", "repository": "org/repo", "source_digest": "c" * 64
        },
        "metadata": {},
    }
    return mutate(body) if mutate is not None else body


def _manifest(
    mutate: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> EnvironmentBundleV2:
    return EnvironmentBundleV2.model_validate(_body(mutate))


def _source_process(**overrides: Any) -> SourceProcess:
    fields: dict[str, Any] = {
        "name": "svc", "kind": "source", "working_directory": ".", "build_commands": [],
        "run_command": ["python3", "-c", "pass"], "environment": {}, "secret_purposes": [],
        "user": ProcessUser.SVC_TOOLS, "depends_on": [],
    }
    fields.update(overrides)
    return SourceProcess(**fields)


def _solo_port_plan(process_name: str, *, ordinal: int = 0) -> pr.PortPlan:
    """A single-process `PortPlan` for tests that spawn a `_source_process()` standalone, off the
    shared `_manifest()` topology — `spawn_source_process` always looks its own process up in the
    plan it is given, so the plan must actually know that process's name."""
    return pr.PortPlan(
        ordinals={process_name: ordinal}, job_shared=frozenset(), fixed_ports={},
        effective_instances=1, degraded_reason=None,
    )


class FakeHandle:
    """A `SpawnedProcess` fake: no real subprocess, just a captured-output buffer."""

    def __init__(self, output: str = "") -> None:
        self._output = output
        self.terminated = False

    def is_running(self) -> bool:
        return not self.terminated

    def captured_output(self) -> str:
        return self._output

    def terminate(self) -> None:
        self.terminated = True


class _FakePasswd:
    """Stands in for `pwd.struct_passwd` — only `pw_uid`/`pw_gid` are ever read by this module."""

    def __init__(self, uid: int, gid: int) -> None:
        self.pw_uid = uid
        self.pw_gid = gid


def _fake_user_resolver(known: dict[str, tuple[int, int]]) -> Callable[[str], _FakePasswd | None]:
    def resolver(username: str) -> _FakePasswd | None:
        if username in known:
            uid, gid = known[username]
            return _FakePasswd(uid, gid)
        return None

    return resolver


def _reap(handle: Any, *, attempts: int = 50, interval: float = 0.05) -> None:
    """Waits for a real spawned subprocess to exit. `pytest.fail`s loudly on exhaustion rather
    than falling through to an assertion that would blame the wrong thing — a hung reap here means
    the child never exited, not that its output was wrong."""
    for _ in range(attempts):
        if not handle.is_running():
            return
        time.sleep(interval)
    else:
        pytest.fail(f"subprocess did not exit within {attempts * interval}s")


# --- §2b port allocation ------------------------------------------------------------------------


def test_ordinals_follow_the_authored_processes_array_order() -> None:
    plan = pr.plan_ports(_manifest(), instances=3)
    assert plan.ordinals == {"postgres": 0, "tools-api": 1, "agent": 2}


def test_a_template_database_managed_engine_is_job_shared() -> None:
    """§2b: `template_database` -> once per job; `14000 + ordinal`, the same in every world."""
    plan = pr.plan_ports(_manifest(), instances=3)
    assert plan.is_job_shared("postgres")
    assert plan.port_for("postgres", 0) == 14000
    assert plan.port_for("postgres", 2) == 14000


def test_source_processes_use_the_per_world_stride_formula() -> None:
    """§2b: `15000 + 100*world_index + ordinal`."""
    plan = pr.plan_ports(_manifest(), instances=3)
    assert plan.port_for("tools-api", 0) == 15001
    assert plan.port_for("tools-api", 1) == 15101
    assert plan.port_for("agent", 2) == 15202


def test_a_datadir_copy_managed_engine_is_per_world_not_job_shared() -> None:
    manifest = _manifest(
        lambda body: {**body, "seed": {"stores": [
            {**body["seed"]["stores"][0], "baseline": {
                "strategy": "datadir_copy", "inputs_digest": "sha256:" + "a" * 64
            }},
        ]}}
    )
    plan = pr.plan_ports(manifest, instances=2)
    assert not plan.is_job_shared("postgres")
    assert plan.port_for("postgres", 0) == 15000
    assert plan.port_for("postgres", 1) == 15100


def test_a_managed_engine_with_no_seed_entry_at_all_defaults_to_per_world() -> None:
    """§2b: "per-world is the only safe default" when a managed engine has no `seed.stores`
    entry — a shared engine one world reset could otherwise corrupt for the others.

    T4, p5-round1-review: a REDIS capability with no store entry (§2c: implicitly `empty`, safe
    with no `seed` block at all) rather than the old postgres one — a postgres capability with no
    store entry is preflight-*invalid* (`seed_missing`), so the old fixture proved this branch on
    a manifest that could never reach the provisioner for real. `postgres`'s own `seed.stores`
    entry (`template_database`) is left in place, unrelated to the branch under test.
    """
    manifest = _manifest(
        lambda body: {
            **body,
            "processes": [*body["processes"], {
                "name": "cache", "kind": "managed", "engine": "redis", "version": "7",
                "user": "svc-data", "depends_on": [],
            }],
            "capabilities": {
                **body["capabilities"],
                "cache": {"protocol": "redis", "service": "cache", "configuration_name": None},
            },
        }
    )
    plan = pr.plan_ports(manifest, instances=2)
    assert not plan.is_job_shared("cache")
    assert plan.port_for("cache", 0) != plan.port_for("cache", 1)


def test_the_empty_baseline_strategy_managed_engine_is_per_world() -> None:
    """T5, p5-round1-review: `empty` is one of §2b's four instancing branches
    (`template_database` job-shared; `datadir_copy`, `empty`, and no-seed-entry-at-all all
    per-world) and had no direct test — only `datadir_copy` and the no-entry case were covered.
    Redis is the only catalog engine whose strategies include `empty`
    (`bundle_v2._ENGINE_STRATEGIES`)."""
    manifest = _manifest(
        lambda body: {
            **body,
            "processes": [*body["processes"], {
                "name": "cache", "kind": "managed", "engine": "redis", "version": "7",
                "user": "svc-data", "depends_on": [],
            }],
            "capabilities": {
                **body["capabilities"],
                "cache": {
                    "protocol": "redis", "service": "cache", "configuration_name": "CACHE_URL",
                },
            },
            "seed": {"stores": [
                body["seed"]["stores"][0],
                {
                    "capability": "cache", "migrations": [], "seed_files": [],
                    "baseline": {"strategy": "empty", "inputs_digest": "sha256:" + "b" * 64},
                    "sentinel": {"key": "_seeded", "expected": "1"},
                },
            ]},
        }
    )
    plan = pr.plan_ports(manifest, instances=2)
    assert not plan.is_job_shared("cache")
    assert plan.port_for("cache", 0) == 15003  # ordinal 3 (postgres,tools-api,agent,cache), world 0
    assert plan.port_for("cache", 1) == 15103  # ordinal 3, world 1


def test_fixed_port_is_honored_exactly_and_forces_effective_instances_to_one() -> None:
    manifest = _manifest(
        lambda body: {
            **body,
            "processes": [
                body["processes"][0],
                {**body["processes"][1], "fixed_port": 8081},
                body["processes"][2],
            ],
        }
    )
    plan = pr.plan_ports(manifest, instances=5)
    assert plan.effective_instances == 1
    assert plan.degraded_reason == "fixed_port"
    # Honored exactly, in every world index — there is only ever world 0 once degraded, but the
    # allocator itself must not silently fall back to the formula for any index it is asked for.
    assert plan.port_for("tools-api", 0) == 8081
    assert plan.port_for("tools-api", 3) == 8081


def test_no_fixed_port_leaves_parallelism_undegraded() -> None:
    plan = pr.plan_ports(_manifest(), instances=5)
    assert plan.effective_instances == 5
    assert plan.degraded_reason is None


def test_port_band_boundary_ordinal_99_world_7_equals_15799() -> None:
    """T6, p5-round1-review: the tiling is exact with zero slack (ordinal in [0,99], world in
    [0,7], per §2e item 7's caps) — nothing exercised the actual boundary before."""
    plan = pr.PortPlan(
        ordinals={"last": 99}, job_shared=frozenset(), fixed_ports={}, effective_instances=8,
        degraded_reason=None,
    )
    assert plan.port_for("last", 7) == 15799


def test_job_shared_and_per_world_port_bands_are_disjoint() -> None:
    """T6: 900 ports of slack between [14000,14099] (job-shared) and [15000,15799] (per-world) —
    no ordinal/world combination within the contract's own caps can alias across bands."""
    plan = pr.PortPlan(
        ordinals={"shared": 99, "world": 99}, job_shared=frozenset({"shared"}),
        fixed_ports={}, effective_instances=8, degraded_reason=None,
    )
    job_shared_ports = {plan.port_for("shared", w) for w in range(8)}
    per_world_ports = {plan.port_for("world", w) for w in range(8)}
    assert job_shared_ports == {14099}  # ordinal 99, the same port in every world
    assert per_world_ports == {15099, 15199, 15299, 15399, 15499, 15599, 15699, 15799}
    assert job_shared_ports.isdisjoint(per_world_ports)


# --- §2b placeholder renderer --------------------------------------------------------------------


def test_configuration_name_placeholder_renders_the_capabilitys_address(tmp_path: Path) -> None:
    manifest = _manifest()
    plan = pr.plan_ports(manifest, instances=2)
    credentials = pr.generate_engine_credentials(manifest, token=lambda: "PW")
    endpoints = pr.build_endpoints(manifest, world_index=1, port_plan=plan, credentials=credentials)
    addresses = pr.configuration_addresses_from_endpoints(endpoints)
    agent = manifest.processes[2]
    rendered = pr.render_environment(
        agent, world_index=1, world_dir=tmp_path / "worlds" / "w1" / "agent",
        port_plan=plan, configuration_addresses=addresses,
    )
    assert rendered["DATABASE_URL"] == "postgresql://harness:PW@localhost:14000/w1"
    assert rendered["TOOLS_API_URL"] == "http://localhost:15101"
    assert rendered["NAME"] == "agent-w1"


def test_world_dir_db_name_host_and_port_placeholders(tmp_path: Path) -> None:
    manifest = _manifest(
        lambda body: {
            **body,
            "processes": [
                body["processes"][0],
                {
                    **body["processes"][1],
                    "environment": {
                        **body["processes"][1]["environment"],
                        "SCRATCH": "{{WORLD_DIR}}",
                        "DB": "{{DB_NAME}}",
                        "PEER": "{{HOST_postgres}}:{{PORT_postgres}}",
                    },
                },
                body["processes"][2],
            ],
        }
    )
    plan = pr.plan_ports(manifest, instances=1)
    credentials = pr.generate_engine_credentials(manifest, token=lambda: "PW")
    endpoints = pr.build_endpoints(manifest, world_index=0, port_plan=plan, credentials=credentials)
    addresses = pr.configuration_addresses_from_endpoints(endpoints)
    world_dir = tmp_path / "worlds" / "w0" / "tools-api"
    tools_api = manifest.processes[1]
    rendered = pr.render_environment(
        tools_api, world_index=0, world_dir=world_dir, port_plan=plan,
        configuration_addresses=addresses,
    )
    assert rendered["SCRATCH"] == str(world_dir)
    assert rendered["DB"] == "w0"
    assert rendered["PEER"] == "localhost:14000"


def test_world_index_placeholder_renders_the_bare_integer() -> None:
    manifest = _manifest()
    plan = pr.plan_ports(manifest, instances=1)
    rendered = pr.render_template(
        "world-{{WORLD_INDEX}}", process_name="agent", world_index=3, world_dir=Path("/x"),
        port_plan=plan, configuration_addresses={},
    )
    assert rendered == "world-3"


def test_an_unresolvable_token_is_an_internal_error_not_a_preflight_code() -> None:
    """`preflight_bundle` already validated every token against the closed vocabulary before this
    ever runs — a token this function cannot resolve is a bug, not a bundle defect, so it is NOT
    one of `PreflightError`'s §2e codes."""
    manifest = _manifest()
    plan = pr.plan_ports(manifest, instances=1)
    with pytest.raises(pr.ProcessRuntimeError) as excinfo:
        pr.render_template(
            "{{NOT_A_REAL_TOKEN}}", process_name="agent", world_index=0, world_dir=Path("/x"),
            port_plan=plan, configuration_addresses={},
        )
    assert excinfo.value.code == "internal_unknown_placeholder"
    assert excinfo.value.stage == "render"


def test_a_port_placeholder_naming_an_unknown_process_is_also_an_internal_error() -> None:
    manifest = _manifest()
    plan = pr.plan_ports(manifest, instances=1)
    with pytest.raises(pr.ProcessRuntimeError) as excinfo:
        pr.render_template(
            "{{PORT_ghost}}", process_name="agent", world_index=0, world_dir=Path("/x"),
            port_plan=plan, configuration_addresses={},
        )
    assert excinfo.value.code == "internal_unknown_placeholder"


def test_build_environment_is_merged_raw_and_never_templated(tmp_path: Path) -> None:
    """§2b: `build_environment` "takes NO placeholders at all" — proven end to end through
    `spawn_source_process`'s env, since `render_environment`/`render_template` are never even
    called on it."""
    build_dir = tmp_path / "build" / "svc"
    build_dir.mkdir(parents=True)
    world_dir = tmp_path / "worlds" / "w0" / "svc"
    process = _source_process(build_environment={"TMPDIR": "{{WORLD_DIR}}"})
    captured: dict[str, Any] = {}

    def fake_runner(argv, *, cwd, env, log_path, user=None, group=None):
        captured["env"] = env
        return FakeHandle()

    plan = _solo_port_plan("svc")
    pr.spawn_source_process(
        process, build_dir=build_dir, world_dir=world_dir, world_index=0, port_plan=plan,
        configuration_addresses={}, secret_values={}, secret_purposes={}, runner=fake_runner,
    )
    assert captured["env"]["TMPDIR"] == "{{WORLD_DIR}}"


def test_render_capability_address_raises_for_an_unsupported_protocol() -> None:
    """F10, p5-round1-review: a `mongodb`/`s3`/`kafka`/... capability has no defined address
    shape at this seam (§3 names exactly two worked examples) — this used to silently render a
    bare `<scheme>://host:port`, which is not a working address for any of them."""
    capability = CapabilityV2(
        protocol="mongodb", service="svc", configuration_name="MONGO_URL"
    )
    with pytest.raises(pr.ProcessRuntimeError) as excinfo:
        pr.render_capability_address(capability, port=15005, world_index=0, credentials=None)
    assert excinfo.value.code == "unsupported_capability_protocol"


def test_render_capability_address_raises_for_missing_postgres_credentials() -> None:
    """S11, p5-round1-review: a typed raise for this precondition, not a bare `assert` (stripped
    under `python -O`) — the same defect class as P4's N8(2)."""
    capability = CapabilityV2(
        protocol="postgres", service="postgres", configuration_name="DATABASE_URL"
    )
    with pytest.raises(pr.ProcessRuntimeError) as excinfo:
        pr.render_capability_address(capability, port=14000, world_index=0, credentials=None)
    assert excinfo.value.code == "internal_missing_credentials"


# --- secrets: purpose filtering (F13) -----------------------------------------------------------


def test_select_process_secrets_matches_only_the_claimed_purpose() -> None:
    process = _source_process(secret_purposes=["target_provider"])
    selected = pr.select_process_secrets(
        process,
        secret_values={"A": "1", "B": "2"},
        secret_purposes={"A": "target_provider", "B": "source_checkout"},
    )
    assert selected == {"A": "1"}


def test_select_process_secrets_hard_excludes_source_checkout_even_if_claimed() -> None:
    """F13, p5-round1-review: §1 states `source_checkout` is gateway-only and never uploaded to
    the guest — preflight's `secret_unclaimed`/`secret_missing` pair is scoped to
    `target_provider` only, so a process CAN legally claim `source_checkout` too. This function
    must not depend on the gateway alone to keep that promise."""
    process = _source_process(secret_purposes=["target_provider", "source_checkout"])
    selected = pr.select_process_secrets(
        process,
        secret_values={"A": "target-provider-value", "B": "checkout-value-must-not-move"},
        secret_purposes={"A": "target_provider", "B": "source_checkout"},
    )
    assert selected == {"A": "target-provider-value"}


# --- env construction (F12/F14) -----------------------------------------------------------------


def test_allowlisted_ambient_env_keeps_only_the_fixed_set() -> None:
    source = {
        "PATH": "/bin", "HOME": "/root", "LANG": "C", "TZ": "UTC", "TMPDIR": "/tmp",
        "LC_ALL": "C", "SECRET_TOKEN": "leak-me", "AWS_SECRET_ACCESS_KEY": "leak-me-too",
    }
    env = pr._allowlisted_ambient_env(source)
    assert env == {
        "PATH": "/bin", "HOME": "/root", "LANG": "C", "TZ": "UTC", "TMPDIR": "/tmp", "LC_ALL": "C",
    }


def test_spawn_source_process_env_does_not_inherit_an_arbitrary_ambient_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F12, p5-round1-review: §2b enumerates exactly what a process receives — the ambient
    `svc-control` environment (a future bearer token, a `FUTUREAGI_*` marker) is not on that
    list."""
    monkeypatch.setenv("FUTUREAGI_PROVISIONER_MARKER", "leak-me")
    build_dir = tmp_path / "build" / "svc"
    build_dir.mkdir(parents=True)
    world_dir = tmp_path / "worlds" / "w0" / "svc"
    process = _source_process()
    plan = _solo_port_plan("svc")
    captured: dict[str, Any] = {}

    def fake_runner(argv, *, cwd, env, log_path, user=None, group=None):
        captured["env"] = env
        return FakeHandle()

    pr.spawn_source_process(
        process, build_dir=build_dir, world_dir=world_dir, world_index=0, port_plan=plan,
        configuration_addresses={}, secret_values={}, secret_purposes={}, runner=fake_runner,
    )
    assert "FUTUREAGI_PROVISIONER_MARKER" not in captured["env"]


def test_base_process_env_path_prepend_has_no_empty_trailing_element(tmp_path: Path) -> None:
    """F14, p5-round1-review: an unset/empty inherited `PATH` used to leave a trailing `:`, and
    POSIX `execvp` reads an empty PATH element as "current directory" — cwd for build/run is the
    customer's own tree, so a repo shipping an executable literally named `ls`/`git`/`sh` could
    shadow the real one for any bare-name argv[0]."""
    env = pr._base_process_env(tmp_path / "build" / "svc", base={})
    assert "" not in env["PATH"].split(":")


# --- §2b copy-based build trees -------------------------------------------------------------------


def test_build_process_tree_copies_the_working_directory(tmp_path: Path) -> None:
    (tmp_path / "source" / "svc").mkdir(parents=True)
    (tmp_path / "source" / "svc" / "main.py").write_text("print(1)\n")
    (tmp_path / "source" / "svc" / "sub").mkdir()
    (tmp_path / "source" / "svc" / "sub" / "helper.py").write_text("print(2)\n")
    process = _source_process(working_directory="svc", build_commands=[])
    build_dir = pr.build_process_tree(
        process, source_root=tmp_path / "source", build_root=tmp_path / "build"
    )
    assert build_dir == tmp_path / "build" / "svc"
    assert (build_dir / "main.py").read_text() == "print(1)\n"
    assert (build_dir / "sub" / "helper.py").read_text() == "print(2)\n"


def test_build_commands_get_the_venv_and_node_modules_path_prepend(tmp_path: Path) -> None:
    (tmp_path / "source" / "svc").mkdir(parents=True)
    calls: list[list[str]] = []

    def fake_run(step, *, cwd, env, **kwargs):
        calls.append(env["PATH"].split(":")[:2])
        return subprocess.CompletedProcess(step, 0)

    process = _source_process(
        working_directory="svc", build_commands=[["npm", "ci"], ["npm", "build"]]
    )
    build_dir = pr.build_process_tree(
        process, source_root=tmp_path / "source", build_root=tmp_path / "build", run=fake_run
    )
    expected = [str(build_dir / ".venv" / "bin"), str(build_dir / "node_modules" / ".bin")]
    assert len(calls) == 2  # every step, not just the first
    assert calls[0] == expected
    assert calls[1] == expected


def test_build_environment_is_merged_into_the_build_step_env(tmp_path: Path) -> None:
    (tmp_path / "source" / "svc").mkdir(parents=True)
    captured: dict[str, Any] = {}

    def fake_run(step, *, cwd, env, **kwargs):
        captured["FOO"] = env.get("FOO")
        return subprocess.CompletedProcess(step, 0)

    process = _source_process(
        working_directory="svc", build_commands=[["true"]], build_environment={"FOO": "bar"}
    )
    pr.build_process_tree(
        process, source_root=tmp_path / "source", build_root=tmp_path / "build", run=fake_run
    )
    assert captured["FOO"] == "bar"


def test_build_runs_every_step_once_per_call_in_order(tmp_path: Path) -> None:
    (tmp_path / "source" / "svc").mkdir(parents=True)
    order: list[list[str]] = []

    def fake_run(step, *, cwd, env, **kwargs):
        order.append(step)
        return subprocess.CompletedProcess(step, 0)

    process = _source_process(
        working_directory="svc", build_commands=[["step-a"], ["step-b"], ["step-c"]]
    )
    pr.build_process_tree(
        process, source_root=tmp_path / "source", build_root=tmp_path / "build", run=fake_run
    )
    assert order == [["step-a"], ["step-b"], ["step-c"]]


def test_build_process_trees_builds_each_source_process_exactly_once(tmp_path: Path) -> None:
    """No world loop exists inside `build_process_trees` at all — structural proof of "once per
    job," not "once per world.\""""
    (tmp_path / "source" / "services" / "tools-api").mkdir(parents=True)
    (tmp_path / "source" / ".").mkdir(exist_ok=True)
    manifest = _manifest()
    calls: list[str] = []

    def fake_run(step, *, cwd, env, **kwargs):
        calls.append(str(cwd))
        return subprocess.CompletedProcess(step, 0)

    context = pr.SpawnContext(
        work_directory=tmp_path, port_plan=pr.plan_ports(manifest, instances=4), credentials={},
        secret_values={}, secret_purposes={}, sync_run=fake_run,
    )
    build_dirs = pr.build_process_trees(manifest, source_root=tmp_path / "source", context=context)
    assert set(build_dirs) == {"tools-api", "agent"}  # every `source` process, no managed ones
    # tools-api has 1 build step, agent has 1 build step — exactly 2 calls total, never per-world.
    assert len(calls) == 2


def test_a_nonzero_build_step_is_build_failed(tmp_path: Path) -> None:
    (tmp_path / "source" / "svc").mkdir(parents=True)

    def fake_run(step, *, cwd, env, **kwargs):
        return subprocess.CompletedProcess(step, 1, stdout="", stderr="npm ERR! boom")

    process = _source_process(working_directory="svc", build_commands=[["npm", "ci"]])
    with pytest.raises(pr.ProcessRuntimeError) as excinfo:
        pr.build_process_tree(
            process, source_root=tmp_path / "source", build_root=tmp_path / "build", run=fake_run
        )
    assert excinfo.value.stage == "build"
    assert excinfo.value.code == "build_failed"
    assert "boom" in str(excinfo.value)


def test_a_missing_interpreter_is_runtime_unsupported(tmp_path: Path) -> None:
    """§0 (v1.8): "a repo needing an interpreter the snapshot lacks fails at BUILD time... the
    build step's failure is reported `runtime_unsupported`.\""""
    (tmp_path / "source" / "svc").mkdir(parents=True)

    def fake_run(step, *, cwd, env, **kwargs):
        raise FileNotFoundError(f"no such file: {step[0]!r}")

    process = _source_process(
        working_directory="svc", build_commands=[["python3.13", "-m", "venv", ".venv"]]
    )
    with pytest.raises(pr.ProcessRuntimeError) as excinfo:
        pr.build_process_tree(
            process, source_root=tmp_path / "source", build_root=tmp_path / "build", run=fake_run
        )
    assert excinfo.value.stage == "build"
    assert excinfo.value.code == "runtime_unsupported"


def test_a_missing_non_interpreter_build_tool_is_build_failed_not_runtime_unsupported(
    tmp_path: Path,
) -> None:
    """The missing-interpreter detection is scoped to python*/node* argv[0] patterns (§0 only
    promises those two families) — a missing custom build tool is a `build_failed`, not a
    `runtime_unsupported`, since the snapshot never promised it in the first place."""
    (tmp_path / "source" / "svc").mkdir(parents=True)

    def fake_run(step, *, cwd, env, **kwargs):
        raise FileNotFoundError(f"no such file: {step[0]!r}")

    process = _source_process(working_directory="svc", build_commands=[["some-custom-tool", "x"]])
    with pytest.raises(pr.ProcessRuntimeError) as excinfo:
        pr.build_process_tree(
            process, source_root=tmp_path / "source", build_root=tmp_path / "build", run=fake_run
        )
    assert excinfo.value.code == "build_failed"


def test_a_build_step_that_times_out_is_build_failed(tmp_path: Path) -> None:
    """F15, p5-round1-review: an install step wedged on a private registry with no DNS answer
    used to block the provisioner forever."""
    (tmp_path / "source" / "svc").mkdir(parents=True)

    def fake_run(step, *, cwd, env, **kwargs):
        raise subprocess.TimeoutExpired(cmd=step, timeout=kwargs.get("timeout", 0))

    process = _source_process(working_directory="svc", build_commands=[["npm", "ci"]])
    with pytest.raises(pr.ProcessRuntimeError) as excinfo:
        pr.build_process_tree(
            process, source_root=tmp_path / "source", build_root=tmp_path / "build", run=fake_run,
            build_step_timeout_seconds=5,
        )
    assert excinfo.value.code == "build_failed"


# --- F5: typed copy-phase failures ----------------------------------------------------------------


def test_build_process_tree_missing_working_directory_is_source_tree_unavailable(
    tmp_path: Path,
) -> None:
    (tmp_path / "source").mkdir()
    process = _source_process(working_directory="does-not-exist", build_commands=[])
    with pytest.raises(pr.ProcessRuntimeError) as excinfo:
        pr.build_process_tree(
            process, source_root=tmp_path / "source", build_root=tmp_path / "build"
        )
    assert excinfo.value.code == "source_tree_unavailable"
    assert excinfo.value.stage == "build"


def test_build_process_tree_working_directory_naming_a_file_is_source_tree_unavailable(
    tmp_path: Path,
) -> None:
    (tmp_path / "source").mkdir()
    (tmp_path / "source" / "svc").write_text("not a directory")
    process = _source_process(working_directory="svc", build_commands=[])
    with pytest.raises(pr.ProcessRuntimeError) as excinfo:
        pr.build_process_tree(
            process, source_root=tmp_path / "source", build_root=tmp_path / "build"
        )
    assert excinfo.value.code == "source_tree_unavailable"


def test_build_process_tree_wraps_a_copy_failure_as_source_tree_unavailable(
    tmp_path: Path,
) -> None:
    (tmp_path / "source" / "svc").mkdir(parents=True)
    process = _source_process(working_directory="svc", build_commands=[])

    def failing_copy(src: Path, dst: Path) -> None:
        raise PermissionError("no")

    with pytest.raises(pr.ProcessRuntimeError) as excinfo:
        pr.build_process_tree(
            process, source_root=tmp_path / "source", build_root=tmp_path / "build",
            copy=failing_copy,
        )
    assert excinfo.value.code == "source_tree_unavailable"


# --- F2 (BLOCKER): symlink safety, exercised against a real filesystem ---------------------------


def test_build_process_tree_rejects_a_symlink_escaping_the_source_root(tmp_path: Path) -> None:
    """F2, p5-round1-review — BLOCKER. A repo can ship a symlink pointing outside `/work/source`
    (e.g. at `/run/futureagi/capabilities.json`); `copytree`'s default (`symlinks=False`) used to
    dereference it and copy the TARGET's bytes into the customer's own build tree, read as
    svc-control. Verified against a REAL symlink and a REAL filesystem, not a fake copier — this
    is exactly the class of bug a fake would hide."""
    outside_secret = tmp_path / "outside_secret.txt"
    outside_secret.write_text("TOP SECRET")
    svc_dir = tmp_path / "source" / "svc"
    svc_dir.mkdir(parents=True)
    (svc_dir / "evil_link").symlink_to(outside_secret)

    process = _source_process(working_directory="svc", build_commands=[])
    with pytest.raises(pr.ProcessRuntimeError) as excinfo:
        pr.build_process_tree(
            process, source_root=tmp_path / "source", build_root=tmp_path / "build"
        )
    assert excinfo.value.code == "source_tree_unavailable"
    # Rejected BEFORE the copy, not after — nothing was ever materialized.
    assert not (tmp_path / "build" / "svc").exists()


def test_build_process_tree_preserves_a_within_tree_symlink_as_a_symlink(tmp_path: Path) -> None:
    """A link that stays inside the tree is legitimate and must keep working — `symlinks=True`
    copies it AS a link, it does not forbid it."""
    svc_dir = tmp_path / "source" / "svc"
    svc_dir.mkdir(parents=True)
    (svc_dir / "real.txt").write_text("hello")
    (svc_dir / "link.txt").symlink_to(svc_dir / "real.txt")

    process = _source_process(working_directory="svc", build_commands=[])
    build_dir = pr.build_process_tree(
        process, source_root=tmp_path / "source", build_root=tmp_path / "build"
    )
    assert (build_dir / "link.txt").is_symlink()
    assert (build_dir / "link.txt").read_text() == "hello"


def test_build_process_tree_rejects_a_symlinked_working_directory_path_component(
    tmp_path: Path,
) -> None:
    """F2: the resolve-then-`is_relative_to` check catches a symlinked PATH COMPONENT too, not
    just a symlinked leaf file — `working_directory: "services/tools-api"` passes the model
    layer's `_safe_relative` (no `..`, not absolute) even when `/work/source/services` is ITSELF
    a symlink pointing outside the checkout."""
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "services").symlink_to(tmp_path)  # escapes source_root entirely

    process = _source_process(working_directory="services/tools-api", build_commands=[])
    with pytest.raises(pr.ProcessRuntimeError) as excinfo:
        pr.build_process_tree(process, source_root=source_root, build_root=tmp_path / "build")
    assert excinfo.value.code == "source_tree_unavailable"


# --- F3 (BLOCKER): path-containment defense in depth ----------------------------------------------


def test_build_tree_dir_rejects_a_name_that_escapes_the_work_directory(tmp_path: Path) -> None:
    """F3, p5-round1-review — BLOCKER. Defense in depth, independent of the model-layer regex
    (`bundle_v2.SourceProcess`/`ManagedProcess.name`'s pattern) — these helpers take a plain
    `str`, so a caller bypassing model validation still cannot walk a name-derived directory
    outside `work_directory`. `pathlib` makes an absolute name a one-field catastrophe otherwise:
    `Path("/work/build") / "/etc"` IS `Path("/etc")`, which the old code then `rmtree`'d."""
    with pytest.raises(pr.ProcessRuntimeError) as excinfo:
        pr.build_tree_dir(tmp_path, "/etc")
    assert excinfo.value.code == "process_name_invalid"


def test_world_scratch_dir_rejects_a_traversal_name_that_truly_escapes(tmp_path: Path) -> None:
    # `world_scratch_dir` prepends TWO fixed levels ("worlds", "w<N>") before the name — 3 levels
    # of ".." is needed to escape `work_directory` itself (2 would only cancel the two prepended
    # levels and land back inside it, which is not an escape).
    with pytest.raises(pr.ProcessRuntimeError) as excinfo:
        pr.world_scratch_dir(tmp_path, 0, "../../../etc")
    assert excinfo.value.code == "process_name_invalid"


def test_managed_engine_data_dir_rejects_a_traversal_name_in_both_branches(tmp_path: Path) -> None:
    with pytest.raises(pr.ProcessRuntimeError) as excinfo:
        pr.managed_engine_data_dir(tmp_path, "/etc", world_index=None)
    assert excinfo.value.code == "process_name_invalid"
    with pytest.raises(pr.ProcessRuntimeError) as excinfo:
        pr.managed_engine_data_dir(tmp_path, "../../../etc", world_index=0)
    assert excinfo.value.code == "process_name_invalid"


def test_legitimate_process_names_are_unaffected_by_the_containment_check(tmp_path: Path) -> None:
    assert pr.build_tree_dir(tmp_path, "tools-api") == tmp_path / "build" / "tools-api"
    assert pr.world_scratch_dir(tmp_path, 2, "agent") == tmp_path / "worlds" / "w2" / "agent"
    assert pr.managed_engine_data_dir(tmp_path, "postgres", world_index=None) == (
        tmp_path / "managed" / "postgres"
    )


# --- F1 (BLOCKER): `user` honored — chown + privilege drop, verified structurally -----------------


def test_build_process_tree_chowns_the_copied_tree_to_the_resolved_user(tmp_path: Path) -> None:
    """F1, p5-round1-review — BLOCKER. The build tree (root AND every copied file, not just the
    directory) is chowned to the process's declared `user` after copy. `os.chown` to a foreign uid
    needs root, which this lane does not have and must not require — `chown` is faked here and the
    calls it WOULD make are asserted; see the module docstring."""
    (tmp_path / "source" / "svc").mkdir(parents=True)
    (tmp_path / "source" / "svc" / "main.py").write_text("print(1)\n")
    process = _source_process(working_directory="svc", build_commands=[])
    chowned: list[tuple[str, int, int]] = []

    pr.build_process_tree(
        process, source_root=tmp_path / "source", build_root=tmp_path / "build",
        user_resolver=_fake_user_resolver({"svc-tools": (1234, 5678)}),
        chown=lambda path, uid, gid: chowned.append((str(path), uid, gid)),
    )
    build_dir = tmp_path / "build" / "svc"
    assert (str(build_dir), 1234, 5678) in chowned
    assert (str(build_dir / "main.py"), 1234, 5678) in chowned


def test_build_commands_run_under_the_resolved_user(tmp_path: Path) -> None:
    (tmp_path / "source" / "svc").mkdir(parents=True)
    captured: dict[str, Any] = {}

    def fake_run(step, *, cwd, env, **kwargs):
        captured["user"] = kwargs.get("user")
        captured["group"] = kwargs.get("group")
        return subprocess.CompletedProcess(step, 0)

    process = _source_process(working_directory="svc", build_commands=[["true"]])
    pr.build_process_tree(
        process, source_root=tmp_path / "source", build_root=tmp_path / "build", run=fake_run,
        user_resolver=_fake_user_resolver({"svc-tools": (1234, 5678)}),
        # `chown` faked too — real `os.chown` to a fake uid needs root, which this lane does not
        # have; the chown call itself is asserted separately (`test_build_process_tree_chowns_...`).
        chown=lambda path, uid, gid: None,
    )
    assert captured["user"] == 1234
    assert captured["group"] == 5678


def test_build_process_tree_falls_back_unprivileged_when_user_is_not_resolvable(
    tmp_path: Path,
) -> None:
    """The local test lane's own shape: no `svc-*` accounts on a dev box.
    `require_declared_user=False` (the default) must NOT raise — it runs unprivileged instead."""
    (tmp_path / "source" / "svc").mkdir(parents=True)
    process = _source_process(working_directory="svc", build_commands=[])
    build_dir = pr.build_process_tree(
        process, source_root=tmp_path / "source", build_root=tmp_path / "build",
        user_resolver=lambda name: None,
    )
    assert build_dir.is_dir()  # did not raise


def test_build_process_tree_raises_when_user_is_required_but_not_resolvable(
    tmp_path: Path,
) -> None:
    (tmp_path / "source" / "svc").mkdir(parents=True)
    process = _source_process(working_directory="svc", build_commands=[])
    with pytest.raises(pr.ProcessRuntimeError) as excinfo:
        pr.build_process_tree(
            process, source_root=tmp_path / "source", build_root=tmp_path / "build",
            user_resolver=lambda name: None, require_declared_user=True,
        )
    assert excinfo.value.code == "spawn_failed"


def test_spawn_source_process_applies_the_resolved_user_to_the_runner(tmp_path: Path) -> None:
    """Tests: assert the resolved user IS applied — the fake runner records `user=`."""
    build_dir = tmp_path / "build" / "svc"
    build_dir.mkdir(parents=True)
    world_dir = tmp_path / "worlds" / "w0" / "svc"
    process = _source_process()
    plan = _solo_port_plan("svc")
    captured: dict[str, Any] = {}
    chowned: list[tuple[str, int, int]] = []

    def fake_runner(argv, *, cwd, env, log_path, user=None, group=None):
        captured["user"] = user
        captured["group"] = group
        return FakeHandle()

    pr.spawn_source_process(
        process, build_dir=build_dir, world_dir=world_dir, world_index=0, port_plan=plan,
        configuration_addresses={}, secret_values={}, secret_purposes={}, runner=fake_runner,
        user_resolver=_fake_user_resolver({"svc-tools": (1234, 5678)}),
        chown=lambda path, uid, gid: chowned.append((str(path), uid, gid)),
    )
    assert captured["user"] == 1234
    assert captured["group"] == 5678
    # `{{WORLD_DIR}}` is chowned too — otherwise a process running as anyone but svc-control could
    # not write into its own per-world scratch directory at all.
    assert (str(world_dir), 1234, 5678) in chowned


def test_spawn_source_process_falls_back_unprivileged_when_user_is_not_resolvable(
    tmp_path: Path,
) -> None:
    build_dir = tmp_path / "build" / "svc"
    build_dir.mkdir(parents=True)
    world_dir = tmp_path / "worlds" / "w0" / "svc"
    process = _source_process()
    plan = _solo_port_plan("svc")
    captured: dict[str, Any] = {}

    def fake_runner(argv, *, cwd, env, log_path, user=None, group=None):
        captured["user"] = user
        return FakeHandle()

    pr.spawn_source_process(
        process, build_dir=build_dir, world_dir=world_dir, world_index=0, port_plan=plan,
        configuration_addresses={}, secret_values={}, secret_purposes={}, runner=fake_runner,
        user_resolver=lambda name: None,
    )
    assert captured["user"] is None


def test_spawn_source_process_raises_when_user_is_required_but_not_resolvable(
    tmp_path: Path,
) -> None:
    build_dir = tmp_path / "build" / "svc"
    build_dir.mkdir(parents=True)
    world_dir = tmp_path / "worlds" / "w0" / "svc"
    process = _source_process()
    plan = _solo_port_plan("svc")

    def fake_runner(argv, *, cwd, env, log_path, user=None, group=None):
        return FakeHandle()

    with pytest.raises(pr.ProcessRuntimeError) as excinfo:
        pr.spawn_source_process(
            process, build_dir=build_dir, world_dir=world_dir, world_index=0, port_plan=plan,
            configuration_addresses={}, secret_values={}, secret_purposes={}, runner=fake_runner,
            user_resolver=lambda name: None, require_declared_user=True,
        )
    assert excinfo.value.code == "spawn_failed"


def test_spawn_managed_process_chowns_data_dir_to_the_resolved_user(tmp_path: Path) -> None:
    manifest = _manifest()
    postgres = manifest.processes[0]
    creds = pr.EngineCredentials(username="harness", password="pw")
    data_dir = tmp_path / "pg"
    chowned: list[tuple[str, int, int]] = []

    def fake_sync_run(argv, **kwargs):
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "PG_VERSION").write_text("16\n")
        return subprocess.CompletedProcess(argv, 0)

    pr.spawn_managed_process(
        postgres, port=14000, data_dir=data_dir, credentials=creds,
        runner=lambda *a, **k: FakeHandle(), sync_run=fake_sync_run,
        user_resolver=_fake_user_resolver({"svc-data": (2222, 3333)}),
        chown=lambda path, uid, gid: chowned.append((str(path), uid, gid)),
    )
    assert (str(data_dir), 2222, 3333) in chowned
    # The pwfile is chowned to the same user too — `initdb` runs as that user (below) and must be
    # able to read its own 0600 file.
    assert any(uid == 2222 and gid == 3333 and path.endswith(".pwfile") for path, uid, gid in chowned)


def test_default_process_runner_forwards_user_and_group_to_popen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F1: `subprocess.Popen(user=, group=)` requires root/CAP_SETUID to actually switch identity
    — this lane does not have it and must not require it. Verified structurally: `Popen` itself is
    faked, and the kwargs it would have been called with are asserted."""
    captured: dict[str, Any] = {}

    class FakePopen:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured.update(kwargs)

        def poll(self) -> None:
            return None

    monkeypatch.setattr(pr.subprocess, "Popen", FakePopen)
    chowned: list[tuple[str, int, int]] = []
    pr.default_process_runner(
        ["true"], cwd=tmp_path, env={}, log_path=tmp_path / "x.log", user=1234, group=5678,
        chown=lambda path, uid, gid: chowned.append((str(path), uid, gid)),
    )
    assert captured["user"] == 1234
    assert captured["group"] == 5678
    # The log file the harness creates (before the child's privilege drop) is chowned to match —
    # otherwise nothing running as the child's own user could ever open it fresh afterward.
    assert chowned == [(str(tmp_path / "x.log"), 1234, 5678)]


def test_default_process_runner_does_not_chown_the_log_when_no_user_is_given(
    tmp_path: Path,
) -> None:
    chowned: list[Any] = []
    handle = pr.default_process_runner(
        ["python3", "-c", "pass"], cwd=tmp_path, env={"PATH": "/usr/bin:/bin"},
        log_path=tmp_path / "logs" / "proc.log",
        chown=lambda *args: chowned.append(args),
    )
    _reap(handle)
    assert chowned == []


# --- process spawn: real subprocesses via `python3 -c`, secret injection ------------------------


def test_default_process_runner_spawns_a_real_subprocess_and_captures_output(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "logs" / "proc.log"
    handle = pr.default_process_runner(
        ["python3", "-c", "print('hello-from-child')"], cwd=tmp_path, env={"PATH": "/usr/bin:/bin"},
        log_path=log_path,
    )
    _reap(handle)
    assert "hello-from-child" in handle.captured_output()


def test_a_process_claiming_the_purpose_receives_its_secret(tmp_path: Path) -> None:
    build_dir = tmp_path / "build" / "agent"
    build_dir.mkdir(parents=True)
    world_dir = tmp_path / "worlds" / "w0" / "agent"
    script = (
        "import os,sys; sys.stdout.write('SECRET=' + os.environ.get('LIVEKIT_API_KEY', '<none>'))"
    )
    process = _source_process(
        run_command=["python3", "-c", script], secret_purposes=["target_provider"]
    )
    plan = _solo_port_plan("svc")
    handle = pr.spawn_source_process(
        process, build_dir=build_dir, world_dir=world_dir, world_index=0, port_plan=plan,
        configuration_addresses={}, secret_values={"LIVEKIT_API_KEY": "abc123", "OTHER": "zzz"},
        secret_purposes={"LIVEKIT_API_KEY": "target_provider", "OTHER": "source_checkout"},
    )
    _reap(handle.handle)
    assert handle.handle.captured_output() == "SECRET=abc123"


def test_a_process_not_claiming_the_purpose_does_not_receive_the_secret(tmp_path: Path) -> None:
    build_dir = tmp_path / "build" / "svc"
    build_dir.mkdir(parents=True)
    world_dir = tmp_path / "worlds" / "w0" / "svc"
    script = (
        "import os,sys; sys.stdout.write('SECRET=' + os.environ.get('LIVEKIT_API_KEY', '<none>'))"
    )
    process = _source_process(run_command=["python3", "-c", script], secret_purposes=[])
    plan = _solo_port_plan("svc")
    handle = pr.spawn_source_process(
        process, build_dir=build_dir, world_dir=world_dir, world_index=0, port_plan=plan,
        configuration_addresses={}, secret_values={"LIVEKIT_API_KEY": "abc123"},
        secret_purposes={"LIVEKIT_API_KEY": "target_provider"},
    )
    _reap(handle.handle)
    assert handle.handle.captured_output() == "SECRET=<none>"


def test_spawn_source_process_creates_the_per_world_scratch_directory(tmp_path: Path) -> None:
    build_dir = tmp_path / "build" / "svc"
    build_dir.mkdir(parents=True)
    world_dir = tmp_path / "worlds" / "w2" / "svc"
    process = _source_process()
    plan = _solo_port_plan("svc")

    def fake_runner(argv, *, cwd, env, log_path, user=None, group=None):
        return FakeHandle()

    pr.spawn_source_process(
        process, build_dir=build_dir, world_dir=world_dir, world_index=2, port_plan=plan,
        configuration_addresses={}, secret_values={}, secret_purposes={}, runner=fake_runner,
    )
    assert world_dir.is_dir()


def test_spawn_source_process_cwd_is_the_build_tree_not_the_world_scratch_dir(
    tmp_path: Path,
) -> None:
    build_dir = tmp_path / "build" / "svc"
    build_dir.mkdir(parents=True)
    world_dir = tmp_path / "worlds" / "w0" / "svc"
    process = _source_process()
    plan = _solo_port_plan("svc")
    captured: dict[str, Any] = {}

    def fake_runner(argv, *, cwd, env, log_path, user=None, group=None):
        captured["cwd"] = cwd
        return FakeHandle()

    pr.spawn_source_process(
        process, build_dir=build_dir, world_dir=world_dir, world_index=0, port_plan=plan,
        configuration_addresses={}, secret_values={}, secret_purposes={}, runner=fake_runner,
    )
    assert captured["cwd"] == build_dir


def test_spawn_managed_process_requires_credentials_for_postgres(tmp_path: Path) -> None:
    manifest = _manifest()
    postgres = manifest.processes[0]
    with pytest.raises(pr.ProcessRuntimeError) as excinfo:
        pr.spawn_managed_process(
            postgres, port=14000, data_dir=tmp_path / "pg", credentials=None,
            runner=lambda *a, **k: FakeHandle(),
        )
    assert excinfo.value.code == "spawn_failed"


def test_spawn_managed_process_bootstraps_postgres_once_via_sync_run(tmp_path: Path) -> None:
    manifest = _manifest()
    postgres = manifest.processes[0]
    creds = pr.EngineCredentials(username="harness", password="pw")
    data_dir = tmp_path / "pg"
    bootstrap_calls: list[list[str]] = []
    run_calls: list[list[str]] = []

    def fake_sync_run(argv, **kwargs):
        bootstrap_calls.append(argv)
        (data_dir).mkdir(parents=True, exist_ok=True)
        (data_dir / "PG_VERSION").write_text("16\n")
        return subprocess.CompletedProcess(argv, 0)

    def fake_runner(argv, *, cwd, env, log_path, user=None, group=None):
        run_calls.append(argv)
        return FakeHandle()

    pr.spawn_managed_process(
        postgres, port=14000, data_dir=data_dir, credentials=creds, runner=fake_runner,
        sync_run=fake_sync_run,
    )
    assert len(bootstrap_calls) == 1
    assert bootstrap_calls[0][0] == "initdb"
    assert run_calls[0][0] == "postgres"
    # No pwfile left behind after bootstrap.
    assert not any(p.name.endswith(".pwfile") for p in data_dir.parent.glob(".*"))

    # A second spawn against the same already-initialized data_dir must not bootstrap again.
    pr.spawn_managed_process(
        postgres, port=14000, data_dir=data_dir, credentials=creds, runner=fake_runner,
        sync_run=fake_sync_run,
    )
    assert len(bootstrap_calls) == 1


# --- F7 (MAJOR): the pwfile is created 0600 atomically, not write-then-chmod ---------------------


def test_spawn_managed_process_creates_the_pwfile_with_o_excl_and_0600_atomically(
    tmp_path: Path,
) -> None:
    """F7, p5-round1-review. `write_text` then `os.chmod` creates the file at `0666 & ~umask`
    (typically 0644) and only narrows it afterward — a classic create-then-chmod TOCTOU that
    leaves the generated superuser password world-readable for the window in between.
    `os.open`'s own arguments are captured directly (the mode at CREATION, not observed after the
    fact, which cannot distinguish "created narrow" from "created wide, narrowed a moment later")."""
    import os as os_module

    manifest = _manifest()
    postgres = manifest.processes[0]
    creds = pr.EngineCredentials(username="harness", password="s3cr3t")
    data_dir = tmp_path / "pg"
    captured: dict[str, Any] = {}
    real_open = os_module.open

    def spy_open(path, flags, mode=0o777, *args, **kwargs):
        if str(path).endswith(".pwfile"):
            captured["flags"] = flags
            captured["mode"] = mode
        return real_open(path, flags, mode, *args, **kwargs)

    def fake_sync_run(argv, **kwargs):
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "PG_VERSION").write_text("16\n")
        return subprocess.CompletedProcess(argv, 0)

    pr.os.open = spy_open
    try:
        pr.spawn_managed_process(
            postgres, port=14000, data_dir=data_dir, credentials=creds,
            runner=lambda *a, **k: FakeHandle(), sync_run=fake_sync_run,
        )
    finally:
        pr.os.open = real_open

    assert captured["mode"] == 0o600
    assert captured["flags"] & os_module.O_EXCL
    assert captured["flags"] & os_module.O_CREAT


# --- §2b depends_on wait -----------------------------------------------------------------------


def test_depends_on_with_neither_readiness_nor_started_check_returns_immediately() -> None:
    manifest = _manifest(lambda body: {**body, "readiness": []})
    plan = pr.plan_ports(manifest, instances=1)
    spawned = pr.SpawnedWorldProcess("postgres", FakeHandle(), 14000, None)
    calls = {"n": 0}

    def fake_prober(**kwargs):
        calls["n"] += 1
        return True

    pr.wait_for_dependency(
        manifest, "postgres", world_index=0, port_plan=plan, spawned=spawned, prober=fake_prober,
    )
    assert calls["n"] == 0  # no readiness declared for `database` in this manifest -> immediate.


def test_depends_on_waits_for_the_capabilitys_readiness_probe() -> None:
    manifest = _manifest()  # `tools` capability has a declared readiness probe.
    plan = pr.plan_ports(manifest, instances=1)
    spawned = pr.SpawnedWorldProcess("tools-api", FakeHandle(), 15001, 0)
    seen: list[tuple[str, int]] = []
    calls = {"n": 0}

    def fake_prober(*, protocol, host, port, path, user=None, password=None, dbname=None):
        calls["n"] += 1
        seen.append((host, port))
        return calls["n"] >= 3

    ticks = {"t": 0.0}
    pr.wait_for_dependency(
        manifest, "tools-api", world_index=0, port_plan=plan, spawned=spawned, prober=fake_prober,
        clock=lambda: ticks["t"], sleep=lambda s: ticks.__setitem__("t", ticks["t"] + s),
    )
    assert calls["n"] == 3
    assert seen[0] == ("localhost", 15001)


def test_wait_for_dependency_requires_every_readiness_probe_backing_the_process() -> None:
    """F8, p5-round1-review: a process backing two capabilities is only "ready" once ALL of its
    declared readiness probes pass — matching `healthy()`'s own all-probes semantics (§4 point 3).
    A process used to be treated as ready by `depends_on` the moment the FIRST-listed probe
    passed, then immediately reported unhealthy by `healthy()` the instant the second had not."""
    manifest = _manifest(
        lambda body: {
            **body,
            "capabilities": {
                **body["capabilities"],
                "tools_admin": {
                    "protocol": "http", "service": "tools-api", "configuration_name": None,
                },
            },
            "readiness": [
                *body["readiness"],
                {
                    "capability": "tools_admin", "path": "/admin/health", "timeout_seconds": 5,
                    "interval_seconds": 0.1,
                },
            ],
        }
    )
    plan = pr.plan_ports(manifest, instances=1)
    spawned = pr.SpawnedWorldProcess("tools-api", FakeHandle(), 15001, 0)
    ready = {"/health": False, "/admin/health": False}
    seen_paths: list[str | None] = []

    def fake_prober(*, protocol, host, port, path, user=None, password=None, dbname=None):
        seen_paths.append(path)
        return ready[path]

    ticks = {"t": 0.0}

    def sleep(seconds: float) -> None:
        ticks["t"] += seconds
        if ticks["t"] >= 0.2:
            ready["/health"] = True
        if ticks["t"] >= 0.4:
            ready["/admin/health"] = True

    pr.wait_for_dependency(
        manifest, "tools-api", world_index=0, port_plan=plan, spawned=spawned, prober=fake_prober,
        clock=lambda: ticks["t"], sleep=sleep,
    )
    assert "/admin/health" in seen_paths
    assert ready["/health"] and ready["/admin/health"]


def test_depends_on_readiness_probe_timeout_is_a_typed_error() -> None:
    manifest = _manifest()
    plan = pr.plan_ports(manifest, instances=1)
    spawned = pr.SpawnedWorldProcess("tools-api", FakeHandle(), 15001, 0)
    ticks = {"t": 0.0}
    with pytest.raises(pr.ProcessRuntimeError) as excinfo:
        pr.wait_for_dependency(
            manifest, "tools-api", world_index=0, port_plan=plan, spawned=spawned,
            prober=lambda **kwargs: False,
            clock=lambda: ticks["t"], sleep=lambda s: ticks.__setitem__("t", ticks["t"] + s),
        )
    assert excinfo.value.stage == "depends_on"
    assert excinfo.value.code == "depends_on_timeout"


def test_started_check_log_marker_variant_waits_for_the_marker() -> None:
    """T2, p5-round1-review: the marker now APPEARS after N polls (a mutating fake, same idiom as
    `test_depends_on_waits_for_the_capabilitys_readiness_probe`'s `calls['n'] >= 3`) rather than
    being seeded into `captured_output()` up front — the old fixture could not distinguish "polls
    until the marker appears" from "checks once and returns.\""""
    manifest = _manifest(
        lambda body: {**body, "readiness": [], "processes": [
            body["processes"][0],
            {
                **body["processes"][1],
                "started_check": {"log_marker": "listening", "timeout_seconds": 5},
            },
            body["processes"][2],
        ]}
    )
    plan = pr.plan_ports(manifest, instances=1)
    handle = FakeHandle("booting...\n")
    spawned = pr.SpawnedWorldProcess("tools-api", handle, 15001, 0)
    calls = {"n": 0}

    def mutating_captured_output() -> str:
        calls["n"] += 1
        if calls["n"] >= 3:
            handle._output = "booting...\nlistening on :9\n"
        return handle._output

    handle.captured_output = mutating_captured_output
    ticks = {"t": 0.0}
    pr.wait_for_dependency(
        manifest, "tools-api", world_index=0, port_plan=plan, spawned=spawned,
        clock=lambda: ticks["t"], sleep=lambda s: ticks.__setitem__("t", ticks["t"] + s),
    )
    assert calls["n"] >= 3


def test_started_check_log_marker_timeout_is_a_typed_error() -> None:
    manifest = _manifest(
        lambda body: {**body, "readiness": [], "processes": [
            body["processes"][0],
            {
                **body["processes"][1],
                "started_check": {"log_marker": "listening", "timeout_seconds": 1},
            },
            body["processes"][2],
        ]}
    )
    plan = pr.plan_ports(manifest, instances=1)
    handle = FakeHandle("booting...\n")  # marker never appears
    spawned = pr.SpawnedWorldProcess("tools-api", handle, 15001, 0)
    ticks = {"t": 0.0}
    with pytest.raises(pr.ProcessRuntimeError) as excinfo:
        pr.wait_for_dependency(
            manifest, "tools-api", world_index=0, port_plan=plan, spawned=spawned,
            clock=lambda: ticks["t"], sleep=lambda s: ticks.__setitem__("t", ticks["t"] + s),
        )
    assert excinfo.value.code == "depends_on_timeout"


def test_started_check_port_variant_probes_the_dependencys_own_allocated_port() -> None:
    """F4, p5-round1-review — MAJOR. §2b (v1.8): `started_check.port` only SELECTS the port-probe
    variant — the dialed port is always the dependency's OWN allocated port (honoring
    `fixed_port`), never a literal. Exercised against a REAL bound socket on the port
    `port_plan.port_for` computes (via `fixed_port`, so the exact port is deterministic rather
    than depending on the formula's value not colliding with something else already listening)."""
    import socket as socket_module

    server = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM)
    server.bind(("localhost", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        manifest = _manifest(
            lambda body: {**body, "readiness": [], "processes": [
                body["processes"][0],
                {
                    **body["processes"][1], "fixed_port": port,
                    "started_check": {"port": True, "timeout_seconds": 5},
                },
                body["processes"][2],
            ]}
        )
        plan = pr.plan_ports(manifest, instances=1)
        assert plan.port_for("tools-api", 0) == port
        spawned = pr.SpawnedWorldProcess("tools-api", FakeHandle(), port, 0)
        pr.wait_for_dependency(
            manifest, "tools-api", world_index=0, port_plan=plan, spawned=spawned
        )
    finally:
        server.close()


def test_started_check_port_variant_dials_the_world_specific_formula_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F4: without `fixed_port`, the dialed port must be world-`world_index`'s OWN formula port —
    proven by monkeypatching the underlying TCP probe and asserting the exact port dialed, which a
    real-server-bind test cannot show as directly (it can only prove "some port worked")."""
    manifest = _manifest(
        lambda body: {**body, "readiness": [], "processes": [
            body["processes"][0],
            {**body["processes"][1], "started_check": {"port": True, "timeout_seconds": 5}},
            body["processes"][2],
        ]}
    )
    plan = pr.plan_ports(manifest, instances=3)
    expected_port = plan.port_for("tools-api", 2)
    assert expected_port == 15201  # ordinal 1, world 2 — NOT the same as world 0's 15001.
    dialed: list[tuple[str, int]] = []

    def fake_tcp_probe(host: str, port: int, *, timeout: float = 0.75) -> bool:
        dialed.append((host, port))
        return True

    monkeypatch.setattr(pr, "_tcp_probe", fake_tcp_probe)
    spawned = pr.SpawnedWorldProcess("tools-api", FakeHandle(), expected_port, 2)
    pr.wait_for_dependency(manifest, "tools-api", world_index=2, port_plan=plan, spawned=spawned)
    assert dialed == [("localhost", expected_port)]


def test_started_check_port_variant_timeout_when_nothing_listens() -> None:
    manifest = _manifest(
        lambda body: {**body, "readiness": [], "processes": [
            body["processes"][0],
            {
                **body["processes"][1], "fixed_port": 1,
                "started_check": {"port": True, "timeout_seconds": 1},
            },
            body["processes"][2],
        ]}
    )
    plan = pr.plan_ports(manifest, instances=1)
    spawned = pr.SpawnedWorldProcess("tools-api", FakeHandle(), 1, 0)
    ticks = {"t": 0.0}
    with pytest.raises(pr.ProcessRuntimeError) as excinfo:
        pr.wait_for_dependency(
            manifest, "tools-api", world_index=0, port_plan=plan, spawned=spawned,
            clock=lambda: ticks["t"], sleep=lambda s: ticks.__setitem__("t", ticks["t"] + s),
        )
    assert excinfo.value.code == "depends_on_timeout"


def test_spawn_world_spawns_in_dependency_order_and_waits_between(tmp_path: Path) -> None:
    manifest = _manifest(lambda body: {**body, "readiness": []})  # skip real readiness waits
    plan = pr.plan_ports(manifest, instances=2)
    credentials = pr.generate_engine_credentials(manifest, token=lambda: "PW")
    spawn_order: list[str] = []

    def fake_runner(argv, *, cwd, env, log_path, user=None, group=None):
        spawn_order.append(argv[0])
        return FakeHandle()

    def fake_sync_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0)

    context = pr.SpawnContext(
        work_directory=tmp_path, port_plan=plan, credentials=credentials, secret_values={},
        secret_purposes={}, runner=fake_runner, sync_run=fake_sync_run,
    )
    result = pr.spawn_world(manifest, world_index=0, context=context)
    assert set(result.handles) == {"postgres", "tools-api", "agent"}
    # postgres has no dependency; tools-api depends on postgres; agent depends on both.
    assert spawn_order.index("postgres") < spawn_order.index("node")  # tools-api's run_command[0]
    assert spawn_order.index("node") < spawn_order.index("python3")  # agent's run_command[0]


def test_spawn_world_reuses_job_shared_handles_across_worlds(tmp_path: Path) -> None:
    manifest = _manifest(lambda body: {**body, "readiness": []})
    plan = pr.plan_ports(manifest, instances=2)
    credentials = pr.generate_engine_credentials(manifest, token=lambda: "PW")
    spawned_argv0s: list[str] = []

    def fake_runner(argv, *, cwd, env, log_path, user=None, group=None):
        spawned_argv0s.append(argv[0])
        return FakeHandle()

    def fake_sync_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0)

    context = pr.SpawnContext(
        work_directory=tmp_path, port_plan=plan, credentials=credentials, secret_values={},
        secret_purposes={}, runner=fake_runner, sync_run=fake_sync_run,
    )
    world0 = pr.spawn_world(manifest, world_index=0, context=context)
    shared = {name: handle for name, handle in world0.handles.items() if plan.is_job_shared(name)}
    assert set(shared) == {"postgres"}
    spawned_argv0s.clear()
    world1 = pr.spawn_world(manifest, world_index=1, context=context, shared_handles=shared)
    assert "postgres" not in spawned_argv0s  # not respawned for world 1
    assert world1.handles["postgres"] is shared["postgres"]  # the very same handle, reused


# --- §4.3 healthy() ------------------------------------------------------------------------------


def test_healthy_dispatches_to_the_probe_for_each_declared_readiness_entry() -> None:
    manifest = _manifest()  # one readiness entry, for `tools`
    runtime = pr.EnvironmentRuntime(
        runtime_id="r1", world_index=0, bundle_digest="sha256:" + "0" * 64,
        state=pr.RuntimeState.PREPARING,
        endpoints={
            "tools": pr.RuntimeEndpoint(
                capability="tools", protocol="http", address="http://localhost:15001",
                configuration_name="TOOLS_API_URL",
            ),
        },
    )
    seen: list[tuple[str, int, str | None]] = []

    def fake_prober(*, protocol, host, port, path, user=None, password=None, dbname=None):
        seen.append((host, port, path))
        return True

    assert pr.probe_runtime_health(manifest, runtime, prober=fake_prober) is True
    assert seen == [("localhost", 15001, "/health")]


def test_healthy_is_false_when_a_declared_probe_fails() -> None:
    manifest = _manifest()
    runtime = pr.EnvironmentRuntime(
        runtime_id="r1", world_index=0, bundle_digest="sha256:" + "0" * 64,
        state=pr.RuntimeState.PREPARING,
        endpoints={
            "tools": pr.RuntimeEndpoint(
                capability="tools", protocol="http", address="http://localhost:15001",
                configuration_name="TOOLS_API_URL",
            ),
        },
    )
    assert pr.probe_runtime_health(manifest, runtime, prober=lambda **kwargs: False) is False


def test_healthy_is_false_when_a_declared_probes_capability_has_no_endpoint() -> None:
    manifest = _manifest()
    runtime = pr.EnvironmentRuntime(
        runtime_id="r1", world_index=0, bundle_digest="sha256:" + "0" * 64,
        state=pr.RuntimeState.PREPARING, endpoints={},
    )
    assert pr.probe_runtime_health(manifest, runtime, prober=lambda **kwargs: True) is False


def test_probe_runtime_health_parses_postgres_credentials_out_of_the_endpoint_address() -> None:
    """F9, p5-round1-review: `probe_runtime_health` only ever sees `EnvironmentRuntime`, not the
    job's credential map — the rendered
    `postgresql://harness:<pw>@localhost:<port>/w<N>` address already carries everything a real
    probe needs, so it is parsed back out rather than threaded separately."""
    manifest = _manifest(
        lambda body: {**body, "readiness": [
            {"capability": "database", "timeout_seconds": 5, "interval_seconds": 0.1},
        ]}
    )
    runtime = pr.EnvironmentRuntime(
        runtime_id="r1", world_index=0, bundle_digest="sha256:" + "0" * 64,
        state=pr.RuntimeState.PREPARING,
        endpoints={
            "database": pr.RuntimeEndpoint(
                capability="database", protocol="postgres",
                address="postgresql://harness:s3cr3t@localhost:14000/w0",
                configuration_name="DATABASE_URL",
            ),
        },
    )
    seen: dict[str, Any] = {}

    def fake_prober(*, protocol, host, port, path, user=None, password=None, dbname=None):
        seen.update(user=user, password=password, dbname=dbname)
        return True

    assert pr.probe_runtime_health(manifest, runtime, prober=fake_prober) is True
    assert seen == {"user": "harness", "password": "s3cr3t", "dbname": "w0"}


async def _run_healthy(*args: Any, **kwargs: Any) -> bool:
    return await pr.healthy(*args, **kwargs)


def test_healthy_transitions_follow_section_3_and_never_promote() -> None:
    """F6, p5-round1-review — MAJOR (T3). §3's `state` row fixes the legal transitions:
    `preparing->ready`, `ready->unhealthy`, and `unhealthy->ready` ONLY via re-provision reconcile
    (§4 point 1). `healthy()` is not a reconcile, so it may only ever DEMOTE — this replaces the
    old test, which asserted only `preparing->ready` then `ready->unhealthy` in sequence and never
    exercised `unhealthy->?` or `stopped->?` at all, so it could not have caught the promotion bug
    the old implementation actually had (`RuntimeState.READY if is_healthy else UNHEALTHY`, which
    unconditionally promotes ANY prior state to READY on a passing probe)."""
    import asyncio

    manifest = _manifest()
    endpoints = {
        "tools": pr.RuntimeEndpoint(
            capability="tools", protocol="http", address="http://localhost:15001",
            configuration_name="TOOLS_API_URL",
        ),
    }

    def make(state: pr.RuntimeState) -> pr.EnvironmentRuntime:
        return pr.EnvironmentRuntime(
            runtime_id="r1", world_index=0, bundle_digest="sha256:" + "0" * 64,
            state=state, endpoints=endpoints,
        )

    preparing = make(pr.RuntimeState.PREPARING)
    assert asyncio.run(_run_healthy(manifest, preparing, prober=lambda **kwargs: True)) is True
    assert preparing.state is pr.RuntimeState.READY  # preparing + healthy -> ready

    still_ready = make(pr.RuntimeState.READY)
    assert asyncio.run(_run_healthy(manifest, still_ready, prober=lambda **kwargs: True)) is True
    assert still_ready.state is pr.RuntimeState.READY  # ready stays ready

    demoted = make(pr.RuntimeState.READY)
    assert asyncio.run(_run_healthy(manifest, demoted, prober=lambda **kwargs: False)) is False
    assert demoted.state is pr.RuntimeState.UNHEALTHY  # ready + unhealthy probe -> demote

    stays_unhealthy = make(pr.RuntimeState.UNHEALTHY)
    ok = asyncio.run(_run_healthy(manifest, stays_unhealthy, prober=lambda **kwargs: True))
    assert ok is True  # the PROBE passed...
    assert stays_unhealthy.state is pr.RuntimeState.UNHEALTHY  # ...but state is NOT promoted

    stays_stopped = make(pr.RuntimeState.STOPPED)
    asyncio.run(_run_healthy(manifest, stays_stopped, prober=lambda **kwargs: True))
    assert stays_stopped.state is pr.RuntimeState.STOPPED  # only provision()/close() clear this


# --- default probers: real postgres/http exercise, no fakes --------------------------------------


def test_default_capability_prober_falls_back_to_tcp_when_psycopg_is_absent() -> None:
    """`psycopg` is not installed in this test lane (import-guarded) — the postgres branch must
    fall back to a bare TCP probe rather than raising `ImportError`.

    T1, p5-round1-review: the premise itself is asserted, so if `psycopg` is ever installed in
    this lane the test fails LOUDLY instead of silently exercising a real-connect code path under
    the same name and passing for the wrong reason."""
    assert importlib.util.find_spec("psycopg") is None, (
        "psycopg is installed in this test lane — this test's fallback premise no longer holds"
    )
    import socket as socket_module

    server = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM)
    server.bind(("localhost", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        assert pr.default_capability_prober(
            protocol=CapabilityProtocol.POSTGRES, host="localhost", port=port, path=None
        ) is True
    finally:
        server.close()


def test_default_capability_prober_tcp_probe_is_false_when_nothing_listens() -> None:
    assert pr.default_capability_prober(
        protocol=CapabilityProtocol.TCP, host="localhost", port=1, path=None
    ) is False


def test_probe_postgres_runs_a_real_select_1_when_credentials_are_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F9, p5-round1-review — MAJOR. Previously connected as `user="postgres", dbname="postgres"`
    (neither exists — the catalog's `initdb -U harness` creates only `harness`) and treated almost
    any resulting `OperationalError` as "answered, therefore ready." With real credentials, this
    must run an actual query — proven with a fake `psycopg` module, since no real server is
    available in this lane."""
    executed: list[str] = []

    class FakeConnection:
        def execute(self, query: str) -> None:
            executed.append(query)

        def close(self) -> None:
            pass

    def fake_connect(**kwargs: Any) -> FakeConnection:
        assert kwargs["user"] == "harness"
        assert kwargs["dbname"] == "w0"
        return FakeConnection()

    fake_module = types.ModuleType("psycopg")
    fake_module.connect = fake_connect
    fake_module.OperationalError = type("OperationalError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "psycopg", fake_module)

    assert pr._probe_postgres(
        "localhost", 14000, user="harness", password="pw", dbname="w0"
    ) is True
    assert executed == ["SELECT 1"]


def test_probe_postgres_treats_a_starting_up_server_as_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old substring-match heuristic treated `FATAL: the database system is starting up`
    (postgres's OWN response while still in recovery — it binds its listen socket early) as
    "answered, therefore ready." A real `SELECT 1` cannot be fooled by it."""
    fake_module = types.ModuleType("psycopg")
    operational_error = type("OperationalError", (Exception,), {})
    fake_module.OperationalError = operational_error

    class FakeConnection:
        def execute(self, query: str) -> None:
            raise operational_error("the database system is starting up")

        def close(self) -> None:
            pass

    fake_module.connect = lambda **kwargs: FakeConnection()
    monkeypatch.setitem(sys.modules, "psycopg", fake_module)

    assert pr._probe_postgres(
        "localhost", 14000, user="harness", password="pw", dbname="w0"
    ) is False


def test_probe_postgres_falls_back_to_tcp_when_credentials_are_not_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even when `psycopg` IS available, no generated credentials means no real query is
    possible — falls back to the same TCP-only check as the psycopg-absent case, never fabricates
    a connection attempt without something to authenticate with."""
    import socket as socket_module

    def fake_connect(**kwargs: Any) -> None:
        raise AssertionError("must not attempt to connect without credentials")

    fake_module = types.ModuleType("psycopg")
    fake_module.connect = fake_connect
    fake_module.OperationalError = Exception
    monkeypatch.setitem(sys.modules, "psycopg", fake_module)

    server = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM)
    server.bind(("localhost", 0))
    server.listen(1)
    try:
        port = server.getsockname()[1]
        assert pr._probe_postgres("localhost", port) is True  # no user/dbname given
    finally:
        server.close()


def test_probe_http_against_a_real_server_reports_2xx_as_ready() -> None:
    """T7, p5-round1-review: `_probe_http` — the default prober for the one capability the shared
    manifest actually declares a readiness probe for — had zero direct coverage; every readiness
    test in this file injects a fake `prober` instead."""
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib method name
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args: Any) -> None:  # silence stderr noise
            pass

    server = http.server.HTTPServer(("localhost", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        assert pr.default_capability_prober(
            protocol=CapabilityProtocol.HTTP, host="localhost", port=port, path="/health"
        ) is True
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_probe_http_reports_not_ready_when_nothing_listens() -> None:
    assert pr.default_capability_prober(
        protocol=CapabilityProtocol.HTTP, host="localhost", port=1, path="/health"
    ) is False
