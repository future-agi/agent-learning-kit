"""The execution half of the in-sandbox provisioner — `hosted-execution-seams.md` v1.8, §2b/§3/§4.

Builds one world's running processes from an already-`preflight_bundle`-cleared
`EnvironmentBundleV2`: port allocation, `{{...}}` placeholder rendering, copy-based build trees,
process spawn (managed engines and `source` processes), `depends_on` wait, and `healthy()`
readiness probing. This is `provision()` UP TO process spawn only — seed/baseline application,
the world-reset mechanism, and the conformance gate (§4) are a later phase, deliberately left out
so they compose on top of `spawn_world`/`build_process_tree` rather than reworking them. The
`RuntimeProvider` Protocol itself (`runtime.py`) is untouched here; a later phase wires a
Protocol-conforming, stateful adapter around the pure functions below.

Nothing here assumes Docker, a container runtime, or a network provider — §0: "No Docker inside
the sandbox." Every engine/process concept is a plain `subprocess`, matched against the module's
own `ProcessRunner`/`CapabilityProber` seams so tests substitute fakes; the one exception
(`default_capability_prober`'s postgres branch) import-guards `psycopg` and falls back to a bare
TCP probe when it is absent, the same fallback a bundle's own store would get before a role or
database exists to authenticate against.

§0/§2b `user`: every build tree and every spawned process is chowned/spawned under its declared
`user`, resolved through `pwd.getpwnam` (`default_user_resolver`). A dev box has none of the
snapshot's `svc-*` accounts, so resolution failing is the expected local-lane shape, not an error
by itself — `require_declared_user=False` (the default) falls back to running unprivileged and
logs it; a hosted caller that wants a missing user to be a typed failure instead sets it `True`.
"""

from __future__ import annotations

import logging
import os
import pwd
import re
import secrets
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Protocol, Sequence
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, JsonValue

from .bundle import CapabilityProtocol
from .bundle_v2 import (
    BaselineStrategy,
    CapabilityV2,
    EnvironmentBundleV2,
    ManagedEngine,
    ManagedProcess,
    ProcessUser,
    ReadinessProbeV2,
    SecretPurpose,
    SourceProcess,
)

logger = logging.getLogger(__name__)

# --- errors --------------------------------------------------------------------------------


class ProcessRuntimeError(RuntimeError):
    """A runtime-execution failure below the §2e preflight gate.

    The bundle has already passed `preflight_bundle` by the time any of this module runs, so
    these are process/filesystem/timing failures. v1.8 added §2f: a CLOSED table for the subset
    of these that cross the outbound seam (`source_tree_unavailable`, `build_failed`,
    `runtime_unsupported`, `spawn_failed`, `depends_on_timeout`, `unsupported_capability_protocol`),
    each mapped to a `FailureDomain` per §4.6 — every code this module raises THAT crosses the
    seam is in that table. A handful of `code` values prefixed `internal_`
    (`internal_unknown_placeholder`, `internal_missing_credentials`) are deliberately NOT in it:
    each marks a precondition `preflight_bundle` should already have made impossible, so it is a
    bug to fix here, not a failure the outbound seam ever needs a name for. `stage` names which
    phase failed (`build`, `spawn`, `depends_on`, `render`); `process` names the process involved,
    when there is one.
    """

    def __init__(self, stage: str, code: str, message: str, *, process: str | None = None) -> None:
        self.stage = stage
        self.code = code
        self.process = process
        located = f" ({process})" if process else ""
        super().__init__(f"{stage}/{code}{located}: {message}")


# --- §3 EnvironmentRuntime -------------------------------------------------------------------


class RuntimeState(str, Enum):
    PREPARING = "preparing"
    READY = "ready"
    UNHEALTHY = "unhealthy"
    STOPPED = "stopped"


class RuntimeEndpoint(BaseModel):
    capability: str
    protocol: str
    address: str
    configuration_name: str | None = None


class EnvironmentRuntime(BaseModel):
    """§3's per-world provisioner output. No `provider` field (§4 delta: "no remote provider
    exists at this seam") — the class attribute `RuntimeProvider.name` (`runtime.py`) is retained
    there for logging only, not carried onto each world's own record."""

    runtime_id: str
    world_index: int = Field(ge=0)
    bundle_digest: str
    state: RuntimeState
    endpoints: dict[str, RuntimeEndpoint] = Field(default_factory=dict)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


def new_runtime_id(bundle_digest: str, world_index: int) -> str:
    """Opaque per §3 ("nothing parses it") — still traceable in logs without decoding."""
    return f"{bundle_digest}:w{world_index}:{secrets.token_hex(4)}"


# --- §2b port allocation ---------------------------------------------------------------------

_PER_WORLD_BASE = 15000
_PER_WORLD_STRIDE = 100
_JOB_SHARED_BASE = 14000


def _ordinal_map(manifest: EnvironmentBundleV2) -> dict[str, int]:
    """§2b: "ordinal = the process's 0-based index in the `processes` array as authored" — one
    map, shared by both port ranges."""
    return {process.name: index for index, process in enumerate(manifest.processes)}


def _job_shared_process_names(manifest: EnvironmentBundleV2) -> frozenset[str]:
    """§2b instancing rule: a managed engine runs once per job iff some store backed by one of
    its capabilities uses `template_database`; `datadir_copy`, `empty`, or no `seed.stores` entry
    at all is per-world — per-world is the only safe default for an engine one world's reset
    could otherwise corrupt for the others. Only postgres's catalog entry permits
    `template_database` at all (`bundle_v2._ENGINE_STRATEGIES`), so this can never mark a redis
    or rabbitmq process job-shared.
    """
    service_by_capability = {slug: cap.service for slug, cap in manifest.capabilities.items()}
    strategies_by_service: dict[str, set[BaselineStrategy]] = {}
    if manifest.seed is not None:
        for store in manifest.seed.stores:
            service = service_by_capability.get(store.capability)
            if service is not None:
                strategies_by_service.setdefault(service, set()).add(store.baseline.strategy)
    return frozenset(
        process.name
        for process in manifest.processes
        if isinstance(process, ManagedProcess)
        and BaselineStrategy.TEMPLATE_DATABASE in strategies_by_service.get(process.name, set())
    )


@dataclass(frozen=True)
class PortPlan:
    """A job's whole port assignment, computed once from the manifest and the requested instance
    count. `fixed_port` (§2b) forces `effective_instances` to 1 — `port_for` honors the fixed
    value exactly for that process, in every world index, which is consistent because there can
    only ever be world 0 once `effective_instances` is 1. Emitting the `parallelism_degraded`
    event for `degraded_reason` is the scheduler's job (§2b), not this module's — this only
    records the fact.
    """

    ordinals: dict[str, int]
    job_shared: frozenset[str]
    fixed_ports: dict[str, int]
    effective_instances: int
    degraded_reason: str | None

    def is_job_shared(self, process_name: str) -> bool:
        return process_name in self.job_shared

    def port_for(self, process_name: str, world_index: int) -> int:
        if process_name in self.fixed_ports:
            return self.fixed_ports[process_name]
        ordinal = self.ordinals[process_name]
        if process_name in self.job_shared:
            return _JOB_SHARED_BASE + ordinal
        return _PER_WORLD_BASE + _PER_WORLD_STRIDE * world_index + ordinal


def plan_ports(manifest: EnvironmentBundleV2, *, instances: int) -> PortPlan:
    ordinals = _ordinal_map(manifest)
    job_shared = _job_shared_process_names(manifest)
    fixed_ports = {
        process.name: process.fixed_port
        for process in manifest.processes
        if isinstance(process, SourceProcess) and process.fixed_port is not None
    }
    return PortPlan(
        ordinals=ordinals,
        job_shared=job_shared,
        fixed_ports=fixed_ports,
        effective_instances=1 if fixed_ports else instances,
        degraded_reason="fixed_port" if fixed_ports else None,
    )


# --- engine credentials, generated once per job -----------------------------------------------


@dataclass(frozen=True)
class EngineCredentials:
    username: str
    password: str


def generate_engine_credentials(
    manifest: EnvironmentBundleV2, *, token: Callable[[], str] | None = None
) -> dict[str, EngineCredentials]:
    """§2b catalog: postgres/rabbitmq use role/user `harness`, "password generated per job";
    redis carries no auth in V1 and gets no entry here. Keyed by process name (not engine) since
    a job can carry more than one postgres process. `token` is injectable for deterministic
    tests; production callers rely on the `secrets` module default.
    """
    make_token = token or (lambda: secrets.token_urlsafe(24))
    return {
        process.name: EngineCredentials(username="harness", password=make_token())
        for process in manifest.processes
        if isinstance(process, ManagedProcess)
        and process.engine in (ManagedEngine.POSTGRES, ManagedEngine.RABBITMQ)
    }


def render_capability_address(
    capability: CapabilityV2,
    *,
    port: int,
    world_index: int,
    credentials: EngineCredentials | None,
) -> str:
    """§2b: `{{DATABASE_URL}}` etc. render "with the catalog role, the generated password, the
    allocated port, and `{{DB_NAME}}`." `{{DB_NAME}}` is always `w<N>` (§2b), including under
    `template_database`, where the logical database differs per world on one shared server."""
    host = "localhost"
    protocol = capability.protocol
    if protocol is CapabilityProtocol.POSTGRES:
        if credentials is None:
            # S11/P4's N8(2) precedent: a bare `assert` for a real precondition is stripped under
            # `python -O` — this is a should-never-happen internal invariant (`generate_engine_
            # credentials` always produces one for every postgres/rabbitmq `ManagedProcess`), not
            # a bundle defect, so it is `internal_`-prefixed, not a §2f code.
            raise ProcessRuntimeError(
                "render", "internal_missing_credentials",
                "postgres capability requires generated credentials but none were supplied",
            )
        auth = f"{credentials.username}:{credentials.password}"
        return f"postgresql://{auth}@{host}:{port}/w{world_index}"
    if protocol is CapabilityProtocol.AMQP:
        if credentials is None:
            raise ProcessRuntimeError(
                "render", "internal_missing_credentials",
                "amqp capability requires generated credentials but none were supplied",
            )
        return f"amqp://{credentials.username}:{credentials.password}@{host}:{port}/"
    if protocol is CapabilityProtocol.REDIS:
        return f"redis://{host}:{port}"
    if protocol is CapabilityProtocol.HTTP:
        return f"http://{host}:{port}"
    # F10, p5-round1-review: no other capability protocol (grpc, mongodb, s3, ...) has a defined
    # address shape at this seam — §3 names exactly two worked examples. A bare
    # `<scheme>://host:port` used to be handed to a customer process as its own
    # `{{CONFIGURATION_NAME}}` value, which is not a working address for any of those protocols;
    # failing loudly beats handing out an address that cannot work.
    raise ProcessRuntimeError(
        "render", "unsupported_capability_protocol",
        f"{protocol.value} has no defined address shape at this seam",
    )


def build_endpoints(
    manifest: EnvironmentBundleV2,
    *,
    world_index: int,
    port_plan: PortPlan,
    credentials: dict[str, EngineCredentials],
) -> dict[str, RuntimeEndpoint]:
    """§3's `endpoints` map, built fresh for one world — every declared capability gets an
    entry regardless of whether any process placeholder ever references its `configuration_name`
    (a null `configuration_name` is legal; the scheduler/simulator still read endpoints by
    capability slug, per §3's own reader list)."""
    endpoints: dict[str, RuntimeEndpoint] = {}
    for slug, capability in manifest.capabilities.items():
        port = port_plan.port_for(capability.service, world_index)
        address = render_capability_address(
            capability,
            port=port,
            world_index=world_index,
            credentials=credentials.get(capability.service),
        )
        endpoints[slug] = RuntimeEndpoint(
            capability=slug,
            protocol=capability.protocol.value,
            address=address,
            configuration_name=capability.configuration_name,
        )
    return endpoints


def configuration_addresses_from_endpoints(
    endpoints: dict[str, RuntimeEndpoint],
) -> dict[str, str]:
    return {
        endpoint.configuration_name: endpoint.address
        for endpoint in endpoints.values()
        if endpoint.configuration_name
    }


# --- §2b placeholder renderer ------------------------------------------------------------------

_PLACEHOLDER = re.compile(r"\{\{([^{}]+)\}\}")
_NAMED_PLACEHOLDER = re.compile(r"^(PORT|HOST)_(.+)$")


def render_template(
    value: str,
    *,
    process_name: str,
    world_index: int,
    world_dir: Path,
    port_plan: PortPlan,
    configuration_addresses: dict[str, str],
) -> str:
    """§2b's closed placeholder vocabulary. `preflight_bundle` has already validated every token
    in `value` against this exact vocabulary before this ever runs, so a token this function
    cannot resolve is an internal bug, not a bundle defect — raised as `ProcessRuntimeError`,
    never one of `PreflightError`'s §2e codes (those are preflight's alone; see its own
    docstring: "crosses the outbound seam")."""

    def resolve(match: re.Match[str]) -> str:
        token = match.group(1)
        if token == "WORLD_INDEX":
            return str(world_index)
        if token == "WORLD_DIR":
            return str(world_dir)
        if token == "DB_NAME":
            return f"w{world_index}"
        named = _NAMED_PLACEHOLDER.match(token)
        if named:
            kind, name = named.groups()
            if kind == "HOST":
                return "localhost"
            try:
                return str(port_plan.port_for(name, world_index))
            except KeyError:
                raise ProcessRuntimeError(
                    "render",
                    "internal_unknown_placeholder",
                    f"{{{{{token}}}}} names {name!r}, which is not in this bundle's processes",
                    process=process_name,
                ) from None
        if token in configuration_addresses:
            return configuration_addresses[token]
        raise ProcessRuntimeError(
            "render",
            "internal_unknown_placeholder",
            f"{{{{{token}}}}} has no resolution; preflight should have rejected this bundle",
            process=process_name,
        )

    return _PLACEHOLDER.sub(resolve, value)


def render_environment(
    process: SourceProcess,
    *,
    world_index: int,
    world_dir: Path,
    port_plan: PortPlan,
    configuration_addresses: dict[str, str],
) -> dict[str, str]:
    """`build_environment` is deliberately excluded — §2b: it "takes NO placeholders at all," so
    it is merged raw by the env builders below, never passed through this renderer."""
    return {
        key: render_template(
            value,
            process_name=process.name,
            world_index=world_index,
            world_dir=world_dir,
            port_plan=port_plan,
            configuration_addresses=configuration_addresses,
        )
        for key, value in process.environment.items()
    }


def select_process_secrets(
    process: SourceProcess, *, secret_values: dict[str, str], secret_purposes: dict[str, str]
) -> dict[str, str]:
    """§2b: "the provisioner injects every alias whose ref's `purpose` is listed [in
    `secret_purposes`], under the **alias** as the env-var name." `secret_purposes` maps each
    job-level alias to its `SecretRef.purpose` value — the same shape `process_preflight.
    preflight_bundle`'s own `secret_refs` argument uses, so a caller that already ran preflight
    has this for free.

    `SecretPurpose.SOURCE_CHECKOUT` is excluded unconditionally (F13, p5-round1-review): §1 states
    it is "gateway-only; never uploaded to the guest," and preflight does not forbid a process
    from legally *claiming* it (§2b's `secret_unclaimed`/`secret_missing` pair is scoped to
    `target_provider` only) — the guest should not depend on the gateway alone never putting one
    in `secrets.json` to keep that promise.
    """
    claimed = {
        purpose.value
        for purpose in process.secret_purposes
        if purpose is not SecretPurpose.SOURCE_CHECKOUT
    }
    return {
        alias: value
        for alias, value in secret_values.items()
        if secret_purposes.get(alias) in claimed
    }


# §2b enumerates exactly what a process receives: rendered `environment`, `build_environment`,
# purpose-matched secrets, and the PATH prepend below — the ambient `svc-control` environment is
# not on that list, and §1 marks the source untrusted (F12, p5-round1-review). A short, fixed
# allowlist of the interpreter/locale plumbing a process cannot run without, rather than
# `os.environ` wholesale — nothing here exports a secret today, but the entrypoint that will
# (a bearer token, a `FUTUREAGI_*` marker) is exactly the next thing wired on top of this module.
_INHERITED_ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "TZ", "TMPDIR")


def _allowlisted_ambient_env(source: dict[str, str]) -> dict[str, str]:
    env = {key: value for key, value in source.items() if key in _INHERITED_ENV_ALLOWLIST}
    env.update({key: value for key, value in source.items() if key.startswith("LC_")})
    return env


def _base_process_env(
    build_dir: Path, extra: dict[str, str] | None = None, *, base: dict[str, str] | None = None
) -> dict[str, str]:
    """§2b: `build_environment` "merged" env, plus the provisioner's own unconditional PATH
    prepend — applied last so it always wins even if `extra` (or the ambient environment) sets
    its own `PATH`, for both build and run per the contract's own words. `base` defaults to an
    allowlisted slice of the ambient environment (F12), not `os.environ` wholesale."""
    env = dict(base if base is not None else _allowlisted_ambient_env(os.environ))
    if extra:
        env.update(extra)
    prepend = [str(build_dir / ".venv" / "bin"), str(build_dir / "node_modules" / ".bin")]
    # F14, p5-round1-review: `filter(None, ...)` — an unset/empty `PATH` would otherwise leave a
    # trailing `:`, and POSIX `execvp` reads an empty PATH element as "current directory." cwd for
    # both build and run is the customer's own build tree, so that would let a repo shipping an
    # executable literally named `ls`/`git`/`sh` shadow the real one for any bare-name argv[0].
    env["PATH"] = os.pathsep.join(filter(None, [*prepend, env.get("PATH", "")]))
    return env


# --- §2b copy-based build trees -----------------------------------------------------------------

# §0 (v1.7): "a repo needing an interpreter the snapshot lacks fails at BUILD time... reported
# `runtime_unsupported`, naming what the snapshot ships." Detection surface: an exec of a build
# step's argv[0] that looks like an interpreter name (`python`, `python3`, `python3.11`, `node`,
# `node20`, ...) raising `FileNotFoundError` — anything else that fails to exec, or exits
# nonzero, is `build_failed`. This is a judgement call the contract states as a rule
# ("the build step's failure is reported `runtime_unsupported`") without naming a detection
# mechanism; §0 also promises "python 3.11 and 3.12, node 20 and 22" specifically, so the pattern
# is scoped to those two families rather than every possible interpreter name.
_INTERPRETER_PATTERN = re.compile(r"^(python\d*(\.\d+)?|node\d*)(\.exe)?$")


def _looks_like_missing_interpreter(argv0: str) -> bool:
    return bool(_INTERPRETER_PATTERN.match(Path(argv0).name))


# --- process identity: user resolution and path containment (F1/F3, p5-round1-review) ---------


def default_user_resolver(username: str) -> "pwd.struct_passwd | None":
    """§0: the base snapshot guarantees `svc-agent`/`svc-tools`/`svc-data` exist — on the hosted
    path this always resolves. A dev box has none of them; returning `None` rather than raising
    is what lets `_resolve_process_user`'s local-lane fallback work without a second seam."""
    try:
        return pwd.getpwnam(username)
    except KeyError:
        return None


def _resolve_process_user(
    user: ProcessUser,
    *,
    resolver: Callable[[str], "pwd.struct_passwd | None"],
    require: bool,
    process_name: str,
    stage: str,
) -> "pwd.struct_passwd | None":
    """F1, p5-round1-review: every build tree and every spawned process must run under its
    declared `user`, not the harness's own `svc-control` — otherwise an untrusted `agent` process
    runs with read access to the whole-attempt capabilities bearer (§0 step 4) and, since every
    process then shares one uid, mutual `/proc/<pid>/environ` visibility into every other
    process's injected secrets regardless of `secret_purposes`.

    `require=False` (the default, the local test lane's shape — no `svc-*` accounts on a dev box)
    logs and returns `None`, so the caller runs unprivileged rather than failing every local run.
    `require=True` is for a caller that knows it is on the hosted path, where the snapshot's own
    guarantee means resolution failing is itself an infrastructure fault worth a typed failure.
    """
    resolved = resolver(user.value)
    if resolved is None:
        if require:
            raise ProcessRuntimeError(
                stage, "spawn_failed",
                f"{user.value!r} has no passwd entry; the hosted snapshot must guarantee it",
                process=process_name,
            )
        logger.warning(
            "process %s declares user=%s but it is not resolvable on this host; running "
            "unprivileged (local test lane fallback, not the hosted path)",
            process_name, user.value,
        )
    return resolved


def _default_chown(path: Path, uid: int, gid: int) -> None:
    # `follow_symlinks=False`: F2 copies a within-tree symlink as a link, never dereferenced —
    # chowning through it would touch whatever it points at instead of the link itself.
    os.chown(path, uid, gid, follow_symlinks=False)


def _chown_tree(root: Path, *, uid: int, gid: int, chown: Callable[[Path, int, int], None]) -> None:
    chown(root, uid, gid)
    for dirpath, dirnames, filenames in os.walk(root):
        for name in (*dirnames, *filenames):
            chown(Path(dirpath) / name, uid, gid)


def _ensure_within(path: Path, root: Path, *, process_name: str, stage: str) -> Path:
    """Defense in depth for F3, p5-round1-review. The model layer now constrains a process `name`
    to `^[a-z0-9][a-z0-9_-]*$` (`bundle_v2.py`), which already makes a `/`, `..`, or absolute name
    unspellable — a process named `/etc` used to path-join into `build_dir = Path("/etc")`
    directly, which the very next line then `rmtree`'d as `svc-control`. This is the backstop for
    anything that reaches a name-derived directory without going through that validator, checked
    right before it is handed to `rmtree`/`mkdir`. Returns the ORIGINAL `path`, not
    `path.resolve()`'d — this is a containment check, not a normalization; returning the resolved
    form would silently rewrite every caller's path (e.g. through a `/tmp` -> `/private/tmp`
    symlink on macOS) for no reason connected to the check itself.
    """
    resolved = path.resolve()
    resolved_root = root.resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ProcessRuntimeError(
            stage, "process_name_invalid",
            f"{process_name!r} resolves to {resolved}, which escapes {resolved_root}",
            process=process_name,
        )
    return path


def _reject_escaping_symlinks(tree_root: Path, allowed_root: Path, *, process_name: str) -> None:
    """F2, p5-round1-review: §2e item 2's symlink rejection is scoped to the bundle's OWN files —
    nothing scans `/work/source` for a symlink pointing outside it, since the bundle "does NOT
    embed the repository source" (§2 preamble). Left unchecked, a repo can ship
    `config/creds.json -> /run/futureagi/capabilities.json` and have the provisioner (running as
    svc-control until F1's privilege drop even applies) materialize that 0600 file's bytes into
    the customer's own build tree. Walked against the SOURCE tree before anything is copied, so a
    rejection never partially materializes a tree first. `.resolve()` (not raw `os.readlink`)
    deliberately, so a relative target or a multi-hop symlink chain is followed all the way to
    where it actually lands, not just its first hop.
    """
    for entry in tree_root.rglob("*"):
        if not entry.is_symlink():
            continue
        target = entry.resolve()
        if not target.is_relative_to(allowed_root):
            raise ProcessRuntimeError(
                "build", "source_tree_unavailable",
                f"{entry.relative_to(tree_root)} is a symlink to {target}, which escapes "
                "/work/source",
                process=process_name,
            )


def _copytree_preserving_symlinks(src: Path, dst: Path) -> None:
    # F2: `symlinks=True` — copy a link as a link rather than dereferencing it. `shutil.
    # copytree`'s default (`symlinks=False`) follows every symlink and copies the TARGET's
    # contents as a regular file, which is what let a symlink out of the checkout read as
    # svc-control in the first place. A link that escaped `/work/source` was already rejected by
    # `_reject_escaping_symlinks` before this ever runs; one that stays inside the tree is copied
    # as-is and simply works (or dangles harmlessly) under the chowned, unprivileged build tree.
    shutil.copytree(src, dst, symlinks=True)


_DEFAULT_BUILD_STEP_TIMEOUT_SECONDS = 600.0


def build_process_tree(
    process: SourceProcess,
    *,
    source_root: Path,
    build_root: Path,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    copy: Callable[[Path, Path], None] | None = None,
    user_resolver: Callable[[str], "pwd.struct_passwd | None"] = default_user_resolver,
    require_declared_user: bool = False,
    chown: Callable[[Path, int, int], None] = _default_chown,
    build_step_timeout_seconds: float = _DEFAULT_BUILD_STEP_TIMEOUT_SECONDS,
) -> Path:
    """§2b: copy `source_root/<working_directory>` to `build_root/<name>/`, chowned to the
    process's `user` (F1), then argv-exec each `build_commands` step there under that same user —
    no shell, so `&&`/`$VAR`/globs/pipes do not work, which is why the model layer
    (`bundle_v2.SourceProcess._shape`) already rejects an empty step. Runs once; the caller is
    responsible for calling this exactly once per job, per process — this function itself has no
    per-job memory. Raises on the first failing step (`ProcessRuntimeError`, stage="build"); never
    partially succeeds silently past a failure.
    """
    build_dir = build_root / process.name
    _ensure_within(build_dir, build_root, process_name=process.name, stage="build")

    source_dir = source_root / process.working_directory
    resolved_source_root = source_root.resolve()
    resolved_source_dir = source_dir.resolve()
    if not resolved_source_dir.is_relative_to(resolved_source_root):
        # F2: a symlinked PATH COMPONENT (e.g. `/work/source/services` itself a symlink to `/`)
        # used to be silently followed by the old bare `.resolve()` — `working_directory` passes
        # the model layer's `_safe_relative` (no `..`, not absolute) without ever naming the
        # escape.
        raise ProcessRuntimeError(
            "build", "source_tree_unavailable",
            f"{process.working_directory} resolves outside /work/source",
            process=process.name,
        )
    if not resolved_source_dir.is_dir():
        # F5, p5-round1-review: named explicitly, before any copy attempt, rather than letting
        # `copytree`'s own `FileNotFoundError`/`NotADirectoryError` climb out untyped.
        raise ProcessRuntimeError(
            "build", "source_tree_unavailable",
            f"{process.working_directory} is absent or not a directory in the checkout",
            process=process.name,
        )
    _reject_escaping_symlinks(resolved_source_dir, resolved_source_root, process_name=process.name)

    if build_dir.exists():
        shutil.rmtree(build_dir)
    try:
        (copy or _copytree_preserving_symlinks)(resolved_source_dir, build_dir)
    except (OSError, shutil.Error) as exc:
        # F5: `PermissionError` on an unreadable subtree, `shutil.Error`'s aggregated failure on a
        # dangling symlink — every copy-phase failure is the same deterministic, non-retryable
        # fault as a missing directory, so it gets the same code.
        raise ProcessRuntimeError(
            "build", "source_tree_unavailable",
            f"copying {process.working_directory}: {exc}", process=process.name,
        ) from exc

    resolved_user = _resolve_process_user(
        process.user, resolver=user_resolver, require=require_declared_user,
        process_name=process.name, stage="build",
    )
    if resolved_user is not None:
        _chown_tree(build_dir, uid=resolved_user.pw_uid, gid=resolved_user.pw_gid, chown=chown)
    spawn_uid = resolved_user.pw_uid if resolved_user is not None else None
    spawn_gid = resolved_user.pw_gid if resolved_user is not None else None

    env = _base_process_env(build_dir, process.build_environment)
    for step in process.build_commands:
        try:
            result = run(
                step, cwd=build_dir, env=env, capture_output=True, text=True,
                timeout=build_step_timeout_seconds, user=spawn_uid, group=spawn_gid,
            )
        except subprocess.TimeoutExpired as exc:
            # F15, p5-round1-review: an install step wedged on a private registry with no DNS
            # answer used to block the provisioner forever — the only backstop was the gateway's
            # whole-job TTL, which arrives as SIGTERM to the entrypoint while this call is still
            # inside an uninterruptible `subprocess.run`.
            raise ProcessRuntimeError(
                "build", "build_failed",
                f"{step!r} exceeded the {build_step_timeout_seconds}s build-step timeout",
                process=process.name,
            ) from exc
        except FileNotFoundError as exc:
            if not build_dir.is_dir():
                # JC1 ruling (p5-round1-review): a vanished build tree plus a `python*`/`node*`
                # step raises the exact same `FileNotFoundError` as a missing interpreter — this
                # disambiguates a filesystem fault from an interpreter-availability one before the
                # argv[0] heuristic below ever gets a say.
                raise ProcessRuntimeError(
                    "build", "source_tree_unavailable",
                    f"{build_dir} vanished before {step!r} could run", process=process.name,
                ) from exc
            if _looks_like_missing_interpreter(step[0]):
                raise ProcessRuntimeError(
                    "build",
                    "runtime_unsupported",
                    f"{step[0]!r} is not on the snapshot's PATH; the snapshot ships python "
                    "3.11/3.12 and node 20/22 only",
                    process=process.name,
                ) from exc
            raise ProcessRuntimeError(
                "build", "build_failed", f"{step!r}: {exc}", process=process.name
            ) from exc
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()[:2000]
            raise ProcessRuntimeError(
                "build",
                "build_failed",
                f"{step!r} exited {result.returncode}" + (f": {stderr}" if stderr else ""),
                process=process.name,
            )
    return build_dir


# --- process spawn -------------------------------------------------------------------------------


class SpawnedProcess(Protocol):
    """What both a real `subprocess.Popen` wrapper and a test fake must provide — enough for
    `depends_on`'s `log_marker` variant and for cleanup, nothing engine-specific."""

    def is_running(self) -> bool: ...

    def captured_output(self) -> str: ...

    def terminate(self) -> None: ...


class ProcessRunner(Protocol):
    def __call__(
        self, argv: Sequence[str], *, cwd: Path, env: dict[str, str], log_path: Path,
        user: int | None = None, group: int | None = None,
    ) -> SpawnedProcess: ...


class CapabilityProber(Protocol):
    def __call__(
        self, *, protocol: CapabilityProtocol, host: str, port: int, path: str | None,
        user: str | None = None, password: str | None = None, dbname: str | None = None,
    ) -> bool: ...


@dataclass
class PopenProcess:
    """`SpawnedProcess` backed by a real subprocess. stdout+stderr are redirected to a log file
    under the process's own scratch directory rather than captured through a pipe-reading thread
    — simpler, and `started_check`'s `log_marker` only ever needs to re-read the file's current
    contents, not race a live stream."""

    popen: subprocess.Popen
    log_path: Path

    def is_running(self) -> bool:
        return self.popen.poll() is None

    def captured_output(self) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def terminate(self) -> None:
        self.popen.terminate()


def _default_log_chown(path: Path, uid: int, gid: int) -> None:
    os.chown(path, uid, gid)


def default_process_runner(
    argv: Sequence[str], *, cwd: Path, env: dict[str, str], log_path: Path,
    user: int | None = None, group: int | None = None,
    chown: Callable[[Path, int, int], None] = _default_log_chown,
) -> PopenProcess:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("wb")
    if user is not None:
        # F1, p5-round1-review: the log file is created by the harness (svc-control) before the
        # child's privilege drop — the child can still WRITE through the inherited fd regardless
        # of on-disk mode (permission checks happen at open(), not at write()), but leaving it
        # svc-control-owned means nothing running as the child's own user could ever open it
        # fresh afterward. Chowned to match, not just handed over unchecked. `chown` is injectable
        # like every other chown call in this module (`os.chown` to a foreign uid needs root/
        # CAP_CHOWN — the same privilege `user=`/`group=` below already requires, so a real
        # hosted deployment has both or neither; a dev-box test fakes it structurally).
        chown(log_path, user, group if group is not None else -1)
    popen = subprocess.Popen(
        list(argv), cwd=cwd, env=env, stdout=log_file, stderr=subprocess.STDOUT,
        user=user, group=group,
    )
    return PopenProcess(popen=popen, log_path=log_path)


@dataclass
class SpawnedWorldProcess:
    process_name: str
    handle: SpawnedProcess
    port: int
    world_index: int | None  # None for a job-shared managed engine


# --- managed-engine launch commands ---------------------------------------------------------
#
# §2b fixes the catalog (engine/version/role/db-name/strategies) and the port formula; it does
# not fix an exact launch invocation for any of the three engines — that is provisioner-internal,
# same status as `LocalComposeRuntimeProvider`'s own Compose mechanics. The commands below are a
# defensible V1 default, kept swappable through `ProcessRunner`/`sync_run` injection; they are
# never executed in this module's own test lane (§0: assumed on PATH, never required in tests).


def postgres_bootstrap_argv(
    *, data_dir: Path, credentials: EngineCredentials, pwfile: Path
) -> list[str]:
    return [
        "initdb", "-D", str(data_dir), "-U", credentials.username,
        "--pwfile", str(pwfile), "-A", "scram-sha-256",
    ]


def postgres_daemon_argv(*, data_dir: Path, port: int) -> list[str]:
    return [
        "postgres", "-D", str(data_dir), "-p", str(port), "-k", str(data_dir), "-h", "localhost",
    ]


def redis_daemon_argv(*, data_dir: Path, port: int) -> list[str]:
    return [
        "redis-server", "--port", str(port), "--dir", str(data_dir),
        "--daemonize", "no", "--save", "",
    ]


def rabbitmq_daemon_argv() -> list[str]:
    return ["rabbitmq-server"]


def rabbitmq_daemon_env(
    *, data_dir: Path, port: int, credentials: EngineCredentials
) -> dict[str, str]:
    return {
        "RABBITMQ_NODE_PORT": str(port),
        "RABBITMQ_MNESIA_BASE": str(data_dir),
        "RABBITMQ_LOG_BASE": str(data_dir),
        "RABBITMQ_DEFAULT_USER": credentials.username,
        "RABBITMQ_DEFAULT_PASS": credentials.password,
        "RABBITMQ_NODENAME": f"harness-{port}@localhost",
    }


def spawn_managed_process(
    process: ManagedProcess,
    *,
    port: int,
    data_dir: Path,
    credentials: EngineCredentials | None,
    runner: ProcessRunner = default_process_runner,
    sync_run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    user_resolver: Callable[[str], "pwd.struct_passwd | None"] = default_user_resolver,
    require_declared_user: bool = False,
    chown: Callable[[Path, int, int], None] = _default_chown,
) -> SpawnedWorldProcess:
    """Spawns one managed-engine daemon. Postgres alone needs a one-time synchronous bootstrap
    (`initdb`) before the daemon can start at all — run through `sync_run`, not `runner`, since
    it must complete before the daemon exec happens, not run alongside it. Redis/RabbitMQ
    initialize their own data directory on first boot and need no separate step.

    `data_dir` is chowned to the engine's declared `user` (`svc-data`, per §2b) before anything
    runs in it (F1, p5-round1-review) — otherwise a daemon spawned under that user could not even
    write its own data directory, which the harness (running as `svc-control`) created.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    resolved_user = _resolve_process_user(
        process.user, resolver=user_resolver, require=require_declared_user,
        process_name=process.name, stage="spawn",
    )
    if resolved_user is not None:
        chown(data_dir, resolved_user.pw_uid, resolved_user.pw_gid)
    spawn_uid = resolved_user.pw_uid if resolved_user is not None else None
    spawn_gid = resolved_user.pw_gid if resolved_user is not None else None

    env = _allowlisted_ambient_env(os.environ)
    if process.engine is ManagedEngine.POSTGRES:
        if credentials is None:
            raise ProcessRuntimeError(
                "spawn", "spawn_failed", "postgres requires generated credentials",
                process=process.name,
            )
        if not (data_dir / "PG_VERSION").exists():
            pwfile = data_dir.parent / f".{process.name}.pwfile"
            # F7, p5-round1-review: created at 0600 ATOMICALLY. `write_text` then `chmod` creates
            # the file at `0666 & ~umask` (typically 0644) and only narrows it afterward — a
            # classic create-then-chmod TOCTOU that leaves the generated superuser password
            # world-readable for the window in between. `O_EXCL` additionally refuses to follow a
            # pre-planted symlink/file already sitting at this path.
            fd = os.open(pwfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(credentials.password + "\n")
                if resolved_user is not None:
                    # `initdb` itself runs as `resolved_user` below (via `sync_run`'s `user=`) —
                    # it must be able to READ its own 0600 file, so ownership follows the same
                    # user, never the mode widening to let anyone else read it too.
                    chown(pwfile, resolved_user.pw_uid, resolved_user.pw_gid)
                bootstrap_argv = postgres_bootstrap_argv(
                    data_dir=data_dir, credentials=credentials, pwfile=pwfile
                )
                result = sync_run(
                    bootstrap_argv, capture_output=True, text=True,
                    user=spawn_uid, group=spawn_gid,
                )
                if result.returncode != 0:
                    stderr = (result.stderr or "").strip()[:2000]
                    raise ProcessRuntimeError(
                        "spawn", "spawn_failed",
                        f"initdb exited {result.returncode}: {stderr}",
                        process=process.name,
                    )
            finally:
                pwfile.unlink(missing_ok=True)
        argv = postgres_daemon_argv(data_dir=data_dir, port=port)
    elif process.engine is ManagedEngine.REDIS:
        argv = redis_daemon_argv(data_dir=data_dir, port=port)
    elif process.engine is ManagedEngine.RABBITMQ:
        if credentials is None:
            raise ProcessRuntimeError(
                "spawn", "spawn_failed", "rabbitmq requires generated credentials",
                process=process.name,
            )
        env.update(rabbitmq_daemon_env(data_dir=data_dir, port=port, credentials=credentials))
        argv = rabbitmq_daemon_argv()
    else:  # pragma: no cover - ManagedEngine is closed; unreachable past the model layer.
        raise ProcessRuntimeError(
            "spawn", "spawn_failed", f"unknown engine {process.engine!r}", process=process.name
        )
    try:
        handle = runner(
            argv, cwd=data_dir, env=env, log_path=data_dir / "process.log",
            user=spawn_uid, group=spawn_gid,
        )
    except FileNotFoundError as exc:
        raise ProcessRuntimeError("spawn", "spawn_failed", str(exc), process=process.name) from exc
    return SpawnedWorldProcess(
        process_name=process.name, handle=handle, port=port, world_index=None
    )


def spawn_source_process(
    process: SourceProcess,
    *,
    build_dir: Path,
    world_dir: Path,
    world_index: int,
    port_plan: PortPlan,
    configuration_addresses: dict[str, str],
    secret_values: dict[str, str],
    secret_purposes: dict[str, str],
    runner: ProcessRunner = default_process_runner,
    user_resolver: Callable[[str], "pwd.struct_passwd | None"] = default_user_resolver,
    require_declared_user: bool = False,
    chown: Callable[[Path, int, int], None] = _default_chown,
) -> SpawnedWorldProcess:
    """§2b: `run_command` "exec'd once per world with cwd `/work/build/<name>/`" — the build tree
    is read-only by convention at run time; `world_dir` (`{{WORLD_DIR}}`) is this process's own
    per-world writable scratch, created here since nothing upstream of spawn needs it to exist.
    Chowned to the process's declared `user` before spawn (F1, p5-round1-review) — otherwise a
    process running as anyone but the harness's own uid could not write into its own `{{WORLD_
    DIR}}` at all, since the harness (as `svc-control`) is the one that just created it.
    """
    world_dir.mkdir(parents=True, exist_ok=True)
    resolved_user = _resolve_process_user(
        process.user, resolver=user_resolver, require=require_declared_user,
        process_name=process.name, stage="spawn",
    )
    if resolved_user is not None:
        chown(world_dir, resolved_user.pw_uid, resolved_user.pw_gid)
    rendered = render_environment(
        process,
        world_index=world_index,
        world_dir=world_dir,
        port_plan=port_plan,
        configuration_addresses=configuration_addresses,
    )
    injected = select_process_secrets(
        process, secret_values=secret_values, secret_purposes=secret_purposes
    )
    env = _base_process_env(
        build_dir, {**(process.build_environment or {}), **rendered, **injected}
    )
    try:
        handle = runner(
            list(process.run_command), cwd=build_dir, env=env, log_path=world_dir / "process.log",
            user=resolved_user.pw_uid if resolved_user is not None else None,
            group=resolved_user.pw_gid if resolved_user is not None else None,
        )
    except FileNotFoundError as exc:
        raise ProcessRuntimeError("spawn", "spawn_failed", str(exc), process=process.name) from exc
    port = port_plan.port_for(process.name, world_index)
    return SpawnedWorldProcess(
        process_name=process.name, handle=handle, port=port, world_index=world_index
    )


# --- filesystem layout (§0's guaranteed paths, plus one this module must invent) --------------


def build_tree_dir(work_directory: Path, process_name: str) -> Path:
    return _ensure_within(
        work_directory / "build" / process_name, work_directory,
        process_name=process_name, stage="build",
    )


def world_scratch_dir(work_directory: Path, world_index: int, process_name: str) -> Path:
    return _ensure_within(
        work_directory / "worlds" / f"w{world_index}" / process_name, work_directory,
        process_name=process_name, stage="spawn",
    )


def managed_engine_data_dir(
    work_directory: Path, process_name: str, *, world_index: int | None
) -> Path:
    """§0 fixes `/work/build/<proc>/` and `/work/worlds/w<N>/<proc>/` but names no path for a
    managed engine's own data directory. Per-world engines reuse the per-world scratch shape
    (`world_index` given) so `reset`'s `datadir_copy` case (§4.2: "restore its data directory")
    has an unambiguous per-world location. Job-shared engines (`world_index=None`) get a
    job-level directory outside any single world's tree, since nothing about them is per-world —
    `/work/managed/<proc>/`, which §0 v1.8 added to the layout block (JC4, p5-round1-review).
    """
    if world_index is None:
        path = work_directory / "managed" / process_name
    else:
        return world_scratch_dir(work_directory, world_index, process_name)
    return _ensure_within(path, work_directory, process_name=process_name, stage="spawn")


# --- §2b depends_on wait -------------------------------------------------------------------------


def _readiness_probes_for_process(
    manifest: EnvironmentBundleV2, process_name: str
) -> list[ReadinessProbeV2]:
    """F8, p5-round1-review: returns EVERY declared probe backing this process, not just the
    first-listed one — matching `healthy()`'s own definition of ready (§4 point 3: "declared
    `readiness` probes," plural, unqualified). A process backing two capabilities used to be
    treated as ready by `depends_on` the moment the first-listed probe passed, then immediately
    reported unhealthy the instant `healthy()` ran, because the two functions disagreed about
    what "ready" meant."""
    capability_slugs = {
        slug
        for slug, capability in manifest.capabilities.items()
        if capability.service == process_name
    }
    return [probe for probe in manifest.readiness if probe.capability in capability_slugs]


def _tcp_probe(host: str, port: int, *, timeout: float = 0.75) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _probe_http(host: str, port: int, path: str | None, *, timeout: float = 1.0) -> bool:
    url = f"http://{host}:{port}/{(path or '').lstrip('/')}"
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=timeout) as response:
            response.read(256)
            return 200 <= response.status < 400
    except (OSError, urllib.error.URLError, urllib.error.HTTPError):
        return False


def _probe_postgres(
    host: str, port: int, *, user: str | None = None, password: str | None = None,
    dbname: str | None = None, timeout: float = 1.0,
) -> bool:
    """F9, p5-round1-review: previously connected as `user="postgres", dbname="postgres"` —
    neither exists, since the catalog's `initdb -U harness` creates only the `harness` role — and
    treated almost any resulting `OperationalError` as "answered, therefore ready," including
    `FATAL: the database system is starting up`, postgres's OWN response while still in recovery
    (it binds its listen socket early). With real generated credentials threaded through, this
    runs an actual `SELECT 1`, which a starting-up server cannot pass. Without credentials (no
    generated role for this call site, or `psycopg` absent) it falls back to a bare TCP probe —
    strictly weaker, but no longer claims false readiness from a substring match either.
    """
    try:
        import psycopg  # type: ignore[import-not-found]
    except ImportError:
        return _tcp_probe(host, port, timeout=timeout)
    if user is None or dbname is None:
        return _tcp_probe(host, port, timeout=timeout)
    try:
        connection = psycopg.connect(
            host=host, port=port, user=user, password=password, dbname=dbname,
            connect_timeout=timeout,
        )
        try:
            connection.execute("SELECT 1")
        finally:
            connection.close()
        return True
    except psycopg.OperationalError:
        return False
    except Exception:
        return False


def default_capability_prober(
    *, protocol: CapabilityProtocol, host: str, port: int, path: str | None,
    user: str | None = None, password: str | None = None, dbname: str | None = None,
) -> bool:
    if protocol is CapabilityProtocol.POSTGRES:
        return _probe_postgres(host, port, user=user, password=password, dbname=dbname)
    if protocol is CapabilityProtocol.HTTP:
        return _probe_http(host, port, path)
    return _tcp_probe(host, port)


def _poll_until(
    condition: Callable[[], bool],
    *,
    timeout: float,
    interval: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    timeout_error: Callable[[], Exception],
) -> None:
    deadline = clock() + timeout
    while True:
        if condition():
            return
        if clock() >= deadline:
            raise timeout_error()
        sleep(interval)


def _postgres_probe_credentials(
    capability: CapabilityV2,
    *,
    world_index: int,
    credentials: dict[str, EngineCredentials] | None,
) -> tuple[str | None, str | None, str | None]:
    if capability.protocol is not CapabilityProtocol.POSTGRES:
        return None, None, None
    creds = (credentials or {}).get(capability.service)
    if creds is None:
        return None, None, None
    return creds.username, creds.password, f"w{world_index}"


def wait_for_dependency(
    manifest: EnvironmentBundleV2,
    dependency_name: str,
    *,
    world_index: int,
    port_plan: PortPlan,
    spawned: SpawnedWorldProcess,
    credentials: dict[str, EngineCredentials] | None = None,
    prober: CapabilityProber = default_capability_prober,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """§2b: "the dependent starts only after the dependency's capability `readiness` probe passes
    (or its `started_check`, or immediately after spawn if it has neither)." Priority order per
    the contract's own parenthetical: a declared capability readiness probe first; `started_check`
    only when the dependency backs no such capability (and only `SourceProcess` ever carries one —
    `ManagedProcess` has no `started_check` field); otherwise return immediately, since spawn
    already happened before this is ever called.

    A dependency backing more than one capability must pass EVERY declared probe (F8), matching
    `healthy()`'s own definition — the combined timeout is the longest of them (no probe is cut
    off early), the combined poll interval is the tightest of them (no probe is polled less often
    than it declared). `credentials` (F9) lets a postgres probe run a real `SELECT 1` instead of
    a bare TCP connect; omitted, it degrades to the same TCP-only check as before.
    """
    probes = _readiness_probes_for_process(manifest, dependency_name)
    if probes:
        port = port_plan.port_for(dependency_name, world_index)

        def probe_ready() -> bool:
            for probe in probes:
                capability = manifest.capabilities[probe.capability]
                user, password, dbname = _postgres_probe_credentials(
                    capability, world_index=world_index, credentials=credentials
                )
                if not prober(
                    protocol=capability.protocol, host="localhost", port=port, path=probe.path,
                    user=user, password=password, dbname=dbname,
                ):
                    return False
            return True

        combined_timeout = max(probe.timeout_seconds for probe in probes)
        _poll_until(
            probe_ready,
            timeout=combined_timeout,
            interval=min(probe.interval_seconds for probe in probes),
            clock=clock,
            sleep=sleep,
            timeout_error=lambda: ProcessRuntimeError(
                "depends_on",
                "depends_on_timeout",
                f"{dependency_name}: readiness probe did not pass within {combined_timeout}s",
                process=dependency_name,
            ),
        )
        return

    processes_by_name = {process.name: process for process in manifest.processes}
    dependency = processes_by_name[dependency_name]
    started_check = dependency.started_check if isinstance(dependency, SourceProcess) else None
    if started_check is None:
        return  # neither a readiness probe nor a started_check — ready immediately after spawn.

    if started_check.port:
        # §2b (v1.8): the field only SELECTS the port-probe variant — the dialed port is always
        # the dependency's own allocated one (`port_plan.port_for`, honoring `fixed_port`), never
        # a literal read from the manifest (F4, p5-round1-review). A literal cannot be correct for
        # more than one world, since the formula port differs by `world_index`.
        port = port_plan.port_for(dependency_name, world_index)

        def condition() -> bool:
            return _tcp_probe("localhost", port)
    else:
        marker = started_check.log_marker

        def condition() -> bool:
            return marker in spawned.handle.captured_output()

    _poll_until(
        condition,
        timeout=started_check.timeout_seconds,
        interval=0.25,
        clock=clock,
        sleep=sleep,
        timeout_error=lambda: ProcessRuntimeError(
            "depends_on",
            "depends_on_timeout",
            f"{dependency_name}: started_check did not pass within "
            f"{started_check.timeout_seconds}s",
            process=dependency_name,
        ),
    )


def _topological_order(manifest: EnvironmentBundleV2) -> list[str]:
    """Dependencies before dependents. `preflight_bundle` (`_verify_depends_on`) already rejects
    cycles and unknown names before this ever runs, so a DAG is assumed here, not re-verified."""
    graph = {process.name: list(process.depends_on) for process in manifest.processes}
    order: list[str] = []
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        for dependency_name in graph[name]:
            visit(dependency_name)
        order.append(name)

    for name in graph:
        visit(name)
    return order


# --- per-world spawn orchestration --------------------------------------------------------------


@dataclass(frozen=True)
class SpawnContext:
    """The caller-supplied dependencies `spawn_world` needs, grouped so the per-call signature
    stays to (manifest, world_index, shared_handles) — Phase 6's `provision()` builds one of
    these per job and calls `spawn_world` once per world index inside its own reconciliation
    loop; that loop, idempotency across retries, and `EnvironmentRuntime` state assembly across
    all `instances` worlds are its job, not this function's.
    """

    work_directory: Path
    port_plan: PortPlan
    credentials: dict[str, EngineCredentials]
    secret_values: dict[str, str]
    secret_purposes: dict[str, str]
    runner: ProcessRunner = default_process_runner
    # Shared by `build_process_trees`' `build_commands` steps and `spawn_managed_process`'s
    # postgres `initdb` bootstrap — both are "run one synchronous step, check its exit code."
    sync_run: Callable[..., subprocess.CompletedProcess] = subprocess.run
    prober: CapabilityProber = default_capability_prober
    copy: Callable[[Path, Path], None] | None = None
    # F1, p5-round1-review: threaded to every build/spawn call this context drives.
    # `require_declared_user=False` is the local-lane default (no `svc-*` accounts on a dev box);
    # a hosted caller sets it `True` so a snapshot that somehow lacks a declared user fails typed
    # instead of silently running unprivileged.
    user_resolver: Callable[[str], "pwd.struct_passwd | None"] = default_user_resolver
    require_declared_user: bool = False
    chown: Callable[[Path, int, int], None] = _default_chown
    build_step_timeout_seconds: float = _DEFAULT_BUILD_STEP_TIMEOUT_SECONDS


@dataclass
class WorldSpawnResult:
    handles: dict[str, SpawnedWorldProcess]
    endpoints: dict[str, RuntimeEndpoint]


def spawn_world(
    manifest: EnvironmentBundleV2,
    *,
    world_index: int,
    context: SpawnContext,
    shared_handles: dict[str, SpawnedWorldProcess] | None = None,
) -> WorldSpawnResult:
    """Spawns every process for ONE world, in `depends_on` dependency order, waiting on each
    dependency's readiness before starting its dependent (§2b). `shared_handles` carries
    already-running job-shared managed engines (from an earlier world's call) so they are reused,
    never respawned, for `world_index > 0` — the caller is expected to thread the same dict
    through every `spawn_world` call for one job. Source process build trees must already exist
    (via `build_process_tree`, called once per process before any world is spawned) — this
    function only creates per-world scratch directories, never a build tree.
    """
    endpoints = build_endpoints(
        manifest,
        world_index=world_index,
        port_plan=context.port_plan,
        credentials=context.credentials,
    )
    configuration_addresses = configuration_addresses_from_endpoints(endpoints)
    processes_by_name = {process.name: process for process in manifest.processes}
    handles: dict[str, SpawnedWorldProcess] = dict(shared_handles or {})

    for name in _topological_order(manifest):
        if name in handles:
            continue  # a job-shared managed engine already running from an earlier world.
        process = processes_by_name[name]
        for dependency_name in process.depends_on:
            wait_for_dependency(
                manifest,
                dependency_name,
                world_index=world_index,
                port_plan=context.port_plan,
                spawned=handles[dependency_name],
                credentials=context.credentials,
                prober=context.prober,
            )
        if isinstance(process, ManagedProcess):
            data_dir = managed_engine_data_dir(
                context.work_directory,
                name,
                world_index=None if context.port_plan.is_job_shared(name) else world_index,
            )
            handle = spawn_managed_process(
                process,
                port=context.port_plan.port_for(name, world_index),
                data_dir=data_dir,
                credentials=context.credentials.get(name),
                runner=context.runner,
                sync_run=context.sync_run,
                user_resolver=context.user_resolver,
                require_declared_user=context.require_declared_user,
                chown=context.chown,
            )
        else:
            handle = spawn_source_process(
                process,
                build_dir=build_tree_dir(context.work_directory, name),
                world_dir=world_scratch_dir(context.work_directory, world_index, name),
                world_index=world_index,
                port_plan=context.port_plan,
                configuration_addresses=configuration_addresses,
                secret_values=context.secret_values,
                secret_purposes=context.secret_purposes,
                runner=context.runner,
                user_resolver=context.user_resolver,
                require_declared_user=context.require_declared_user,
                chown=context.chown,
            )
        handles[name] = handle
    return WorldSpawnResult(handles=handles, endpoints=endpoints)


def build_process_trees(
    manifest: EnvironmentBundleV2, *, source_root: Path, context: SpawnContext
) -> dict[str, Path]:
    """Builds every `source` process's tree once — the caller invokes this exactly once per job,
    before the first `spawn_world` call for any world (§2b: `build_commands` run "once per
    job")."""
    return {
        process.name: build_process_tree(
            process,
            source_root=source_root,
            build_root=context.work_directory / "build",
            run=context.sync_run,
            copy=context.copy,
            user_resolver=context.user_resolver,
            require_declared_user=context.require_declared_user,
            chown=context.chown,
            build_step_timeout_seconds=context.build_step_timeout_seconds,
        )
        for process in manifest.processes
        if isinstance(process, SourceProcess)
    }


# --- §4.3 healthy() --------------------------------------------------------------------------


def _split_host_port(address: str) -> tuple[str, int]:
    parsed = urlsplit(address)
    return parsed.hostname or "localhost", parsed.port or 0


def _postgres_credentials_from_address(address: str) -> tuple[str | None, str | None, str | None]:
    """F9: the rendered `endpoint.address` for a postgres capability already carries the
    generated role/password/database (`postgresql://harness:<pw>@localhost:<port>/w<N>`) — parsed
    back out rather than threading `EngineCredentials` separately into `probe_runtime_health`,
    which only ever sees `EnvironmentRuntime`, not the job's credential map."""
    parsed = urlsplit(address)
    return parsed.username, parsed.password, (parsed.path.lstrip("/") or None)


def probe_runtime_health(
    manifest: EnvironmentBundleV2,
    runtime: EnvironmentRuntime,
    *,
    prober: CapabilityProber = default_capability_prober,
) -> bool:
    """§4 point 3: "`healthy` = declared `readiness` probes, not 'process is running.'" Every
    declared `readiness` entry for a capability this runtime actually exposes must pass; a
    capability with no readiness entry declared carries no health obligation (§2b: "Health/
    readiness is otherwise declared ONLY in the capability-level `readiness` section")."""
    for probe in manifest.readiness:
        endpoint = runtime.endpoints.get(probe.capability)
        capability = manifest.capabilities.get(probe.capability)
        if endpoint is None or capability is None:
            return False
        host, port = _split_host_port(endpoint.address)
        user = password = dbname = None
        if capability.protocol is CapabilityProtocol.POSTGRES:
            user, password, dbname = _postgres_credentials_from_address(endpoint.address)
        if not prober(
            protocol=capability.protocol, host=host, port=port, path=probe.path,
            user=user, password=password, dbname=dbname,
        ):
            return False
    return True


async def healthy(
    manifest: EnvironmentBundleV2,
    runtime: EnvironmentRuntime,
    *,
    prober: CapabilityProber = default_capability_prober,
) -> bool:
    """Async wrapper matching §4's `RuntimeProvider.healthy` shape (`runtime.py`'s own
    `LocalComposeRuntimeProvider.healthy` uses the same `asyncio.to_thread` idiom).

    §3's `state` transitions are fixed: `preparing->ready`, `ready->unhealthy`, and
    `unhealthy->ready` ONLY via re-provision reconcile (§4 point 1: "a sick world mid-job is
    recovered by calling `provision` again"). `healthy()` is not a reconcile (F6, p5-round1-
    review) — it may only ever DEMOTE `runtime.state`, never promote it. `ready` stays `ready`
    while still healthy; `unhealthy`/`stopped` are cleared only by `provision()`/`close()`, never
    by a passing probe alone (a world whose sentinel was never re-proved after coming back up must
    not silently re-enter the pool).
    """
    import asyncio

    is_healthy = await asyncio.to_thread(probe_runtime_health, manifest, runtime, prober=prober)
    if not is_healthy:
        runtime.state = RuntimeState.UNHEALTHY
    elif runtime.state is RuntimeState.PREPARING:
        runtime.state = RuntimeState.READY
    return is_healthy


__all__ = [
    "CapabilityProber",
    "EngineCredentials",
    "EnvironmentRuntime",
    "PopenProcess",
    "PortPlan",
    "ProcessRunner",
    "ProcessRuntimeError",
    "RuntimeEndpoint",
    "RuntimeState",
    "SpawnContext",
    "SpawnedProcess",
    "SpawnedWorldProcess",
    "WorldSpawnResult",
    "build_endpoints",
    "build_process_tree",
    "build_process_trees",
    "build_tree_dir",
    "configuration_addresses_from_endpoints",
    "default_capability_prober",
    "default_process_runner",
    "default_user_resolver",
    "generate_engine_credentials",
    "healthy",
    "managed_engine_data_dir",
    "new_runtime_id",
    "plan_ports",
    "probe_runtime_health",
    "rabbitmq_daemon_argv",
    "rabbitmq_daemon_env",
    "redis_daemon_argv",
    "render_capability_address",
    "render_environment",
    "render_template",
    "postgres_bootstrap_argv",
    "postgres_daemon_argv",
    "select_process_secrets",
    "spawn_managed_process",
    "spawn_source_process",
    "spawn_world",
    "wait_for_dependency",
    "world_scratch_dir",
]
