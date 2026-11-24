"""Adaptation inside the hosted sandbox (`environment-releases.md` §7.5, Launch).

The job's command is the control process: it inspects the agent read-only with the target's
provider key (never placed in the process environment, never seen by the model), starts the scratch
PostgreSQL the proofs run on, hands the conversation's builder what it needs in the workspace, and
waits until the builder finishes, exactly as a build waits for its harness. The builder session
does the binding through the workbench; this process turns its `result.json` into the exit code.
With `--auto` (the platform's run mode) there is no builder: this process binds the agent itself
(`auto.py`) and writes the same files.

Workspace layout (`/work/authoring`): `adaptation/inputs.json` and `adaptation/profile.json` from
this process; `adaptation/result.json` and the sealed files from the workbench; the builder's
`.futureagi-build-finished.json` marker when the work ends either way. The scratch PostgreSQL's
superuser DSN sits beside the workspace in `.authoring-credentials/`, which the model's file tools
refuse and the workspace checkpoint never packs. What this process verified (the sealed binding's
digest and its proof's) is recorded there too, in `adaptation-verified.json`: the platform reads
it from that path, not from the workspace, and takes a proof only when it has that digest.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from ..hosted_authoring_entrypoint import (
    _SECRETS_PATH,
    _TARGET_CREDENTIAL_NAMES,
    _TARGET_SECRETS_PATH,
    _load_values,
)
from ..releases import RELEASES_SUITE, BindingMode, environment_directory
from .errors import AdaptationFailure
from .workbench import (
    BINDING,
    BINDING_PROOF,
    INPUTS,
    PROFILE,
    RESULT,
    TRAJECTORIES,
    Workbench,
    failed_result,
    profile_from_document,
    write_json,
)

ADAPTATION_DIRECTORY = "adaptation"
FINISHED_MARKER = ".futureagi-build-finished.json"
_INPUTS_WAIT_SECONDS = 300
_POSTGRES_CREDENTIALS = Path(".authoring-credentials") / "adaptation-postgres.json"
VERIFIED_SCHEMA = "futureagi.adaptation-verified.v1"
_VERIFIED = Path(".authoring-credentials") / "adaptation-verified.json"
_CONTROL = Path(".authoring-credentials") / "adaptation-control"


def _postgres_credentials(workspace: Path) -> Path:
    return workspace.parent / _POSTGRES_CREDENTIALS


def verified_path(workspace: Path) -> Path:
    """Where the control process records what it verified: beside the workspace, in the folder
    the builder session's tools refuse, never inside the output the session can write."""
    return workspace.parent / _VERIFIED


def record_verified(
    workspace: Path, *, binding: dict[str, Any] | None, proof_digest: str | None
) -> None:
    """Write the control process's own statement of what it sealed: the binding's digest and
    mode and, for a routed binding, the digest of the proof this process made. Written from
    values this process holds, never read back from the output directory."""
    path = verified_path(workspace)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    body = {
        "schema_version": VERIFIED_SCHEMA,
        "binding": binding,
        "binding_proof": {"digest": proof_digest} if proof_digest else None,
    }
    # A new file, never one that is already there: a planted link or mode is not written through.
    path.unlink(missing_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(body, stream, sort_keys=True)


def _values(path: Path) -> dict[str, str]:
    try:
        return _load_values(path)
    except (OSError, ValueError):
        return {}


def _target_credentials() -> dict[str, str]:
    """The target's provider key, read once: an adaptation runs no customer process, so nothing
    else in the sandbox needs either file afterwards."""
    values = {**_values(_SECRETS_PATH), **_values(_TARGET_SECRETS_PATH)}
    _SECRETS_PATH.unlink(missing_ok=True)
    _TARGET_SECRETS_PATH.unlink(missing_ok=True)
    return {name: values[name] for name in _TARGET_CREDENTIAL_NAMES if values.get(name)}


def _environments(job: Any, releases: Path) -> tuple[list[Path], Path]:
    adaptation = (job.metadata or {}).get("adaptation")
    if not isinstance(adaptation, dict):
        raise AdaptationFailure(
            "adaptation_job_invalid", "job metadata.adaptation is missing"
        )
    chain = adaptation.get("environment")
    suite = adaptation.get("suite")
    if not isinstance(chain, list) or not chain or not isinstance(suite, dict):
        raise AdaptationFailure(
            "adaptation_job_invalid",
            "metadata.adaptation needs an environment chain and a suite",
        )
    directories = [
        environment_directory(releases, str(ref.get("digest") or "")) for ref in chain
    ]
    suite_root = releases / RELEASES_SUITE
    from ..releases import load_suite_release

    delivered = load_suite_release(suite_root).digest
    if delivered != suite.get("digest"):
        raise AdaptationFailure(
            "release_invalid",
            f"delivered suite {delivered} is not the job's {suite.get('digest')}",
        )
    return directories, suite_root


def failed_before_start(
    output: Path, code: str, message: str, *, hosted: bool = False
) -> int:
    """A failure before the work could start still leaves result.json; in the sandbox it also
    leaves the marker that tells the builder there is nothing left to do."""
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / RESULT, failed_result(code, message))
    if hosted:
        write_json(output.parent / FINISHED_MARKER, {"status": "failed"})
    print(f"adaptation failed: {code}: {message}")
    return 2


def _unproved(output: Path, result: dict[str, Any], message: str) -> dict[str, Any]:
    failed = failed_result("binding_unproved", message)
    failed["environment"] = result.get("environment") or []
    write_json(output / RESULT, failed)
    return failed


def _sealed_as_ours(output: Path, control: Workbench) -> Any:
    """The sealed binding, once this process's own workbench makes the same one: the mode the
    profile gives, the reviewed pairing and, for a routed binding, the derived trajectories
    (kept in `control.draft`). Raises `ValueError` for any other."""
    from ..releases import load_agent_binding

    binding = load_agent_binding(output / BINDING)
    if binding.mode is not control.mode:
        raise ValueError(
            f"the sealed binding is {binding.mode.value}, the agent's is "
            f"{control.mode.value}"
        )
    if control.mode is BindingMode.TOOL_ROUTED:
        trajectories = json.loads((output / TRAJECTORIES).read_text(encoding="utf-8"))
        accepted, said = control.submit_binding(
            {
                "binding": json.loads((output / BINDING).read_text(encoding="utf-8")),
                "trajectories": trajectories,
            }
        )
        if not accepted:
            raise ValueError(said)
    if binding.digest != control.binding.digest:
        raise ValueError(
            "the sealed binding is not the one this process makes from the same mapping"
        )
    return binding


def prove_again(
    output: Path,
    environment_root: Path,
    suite_root: Path,
    postgres_dsn: str,
    control: Workbench,
) -> dict[str, Any]:
    """The result of a builder's adaptation, with its proof made by this process.

    The builder's session can write everything under `output`, its saved state included, so the
    proof it left is not what is sealed. The control process takes only the sealed binding and
    the trajectories, makes the same binding in its own workbench (`control`, which holds the
    inspected agent and the reviewed pairing), proves it against the released suite itself, and
    writes the proof, its digest and the unscorable list from that. A binding it would not make,
    or cannot prove, fails the adaptation, whatever the proof the builder left said."""
    from ..binding_proof import proof_digest
    from ..releases import ReleaseError
    from .proofs import binding_verdicts, prove_binding_on

    result: dict[str, Any] = {}
    try:
        left = json.loads((output / RESULT).read_text(encoding="utf-8"))
        if not isinstance(left, dict):
            raise TypeError(f"{RESULT} is not an object")
        result = left
        if result.get("status") != "completed":
            return result
        binding = _sealed_as_ours(output, control)
        # What is reported is the sealed binding as this process read and checked it, not what
        # the result file says of it.
        result["binding"] = {"digest": binding.digest, "mode": binding.mode.value}
        if binding.mode is not BindingMode.TOOL_ROUTED:
            result["binding_proof"] = None
            write_json(output / RESULT, result)
            return result
        proof = prove_binding_on(
            suite_root,
            binding,
            control.draft.trajectories,
            environment_root=environment_root,
            postgres_dsn=postgres_dsn,
        )
    except (OSError, ValueError, TypeError, AttributeError, ReleaseError) as exc:
        detail = (
            f"{exc.code}: {exc.detail}" if isinstance(exc, ReleaseError) else str(exc)
        )
        return _unproved(
            output,
            result,
            f"the sealed binding could not be proved again: {detail}",
        )
    verdicts = binding_verdicts(proof)
    if not proof.passed or not any(one.status == "proved" for one in verdicts.values()):
        return _unproved(
            output,
            result,
            "the sealed binding is not proved:\n"
            + "\n".join(
                f"{key}: {one.status}: {one.reason}"
                for key, one in sorted(verdicts.items())
                if one.status != "proved"
            ),
        )
    write_json(output / BINDING_PROOF, proof.model_dump(mode="json"))
    write_json(
        output / TRAJECTORIES,
        {
            key: calls
            for key, calls in control.draft.trajectories.items()
            if getattr(verdicts.get(key), "status", None) == "proved"
        },
    )
    result["binding_proof"] = {"digest": proof_digest(proof)}
    result["alignment"] = {
        **(result.get("alignment") or {}),
        "unscorable": [
            {"scenario_key": key, "reason": one.reason}
            for key, one in sorted(verdicts.items())
            if one.status == "unscorable"
        ],
    }
    write_json(output / RESULT, result)
    return result


def _await_result(
    output: Path,
    environment_root: Path,
    suite_root: Path,
    postgres_dsn: str,
    control: Workbench,
) -> int:
    """Wait while the conversation's builder binds the agent in this sandbox, then prove what it
    sealed again here (`prove_again`)."""
    marker = output.parent / FINISHED_MARKER
    deadline = time.monotonic() + int(
        os.environ.get("ALK_HOSTED_AUTHORING_MAX_DURATION_SECONDS") or 3300
    )
    while time.monotonic() < deadline:
        if marker.is_file() and (output / RESULT).is_file():
            result = prove_again(
                output, environment_root, suite_root, postgres_dsn, control
            )
            if result.get("status") == "completed":
                # From what this process just proved and holds, not from the files it wrote.
                record_verified(
                    output.parent,
                    binding=result.get("binding"),
                    proof_digest=(result.get("binding_proof") or {}).get("digest"),
                )
            print(json.dumps({"status": result.get("status")}))
            return 0 if result.get("status") == "completed" else 2
        time.sleep(5)
    return failed_before_start(
        output,
        "adaptation_unfinished",
        "the builder did not finish the adaptation in time",
        hosted=True,
    )


def run_hosted(args: argparse.Namespace) -> int:
    from ..job import HarnessJob
    from ..releases import ReleaseError
    from .profile import profile_from_job

    for name in _TARGET_CREDENTIAL_NAMES:
        os.environ.pop(name, None)
    credentials = _target_credentials()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    try:
        job = HarnessJob.model_validate_json(Path(args.job).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AdaptationFailure("adaptation_job_invalid", str(exc)) from exc
    try:
        environments, suite_root = _environments(job, Path(args.releases))
    except ReleaseError as exc:
        raise AdaptationFailure("release_invalid", f"{exc.code}: {exc.detail}") from exc
    profile = profile_from_job(job, credentials=credentials)
    with contextlib.ExitStack() as stack:
        if args.postgres_dsn:
            dsn = args.postgres_dsn
        else:
            from .scratch_postgres import scratch_cluster

            try:
                dsn = stack.enter_context(scratch_cluster())
            except (OSError, RuntimeError, KeyError) as exc:
                raise AdaptationFailure("postgres_unavailable", str(exc)) from exc
        credentials_path = _postgres_credentials(output.parent)
        credentials_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(
            credentials_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"postgres_dsn": dsn}, stream)
        write_json(output / PROFILE, profile.document())
        # What an earlier round verified is not this one's.
        verified_path(output.parent).unlink(missing_ok=True)
        if getattr(args, "auto", False):
            return _run_auto(output, environments, suite_root, dsn, profile, job)
        # This process's own workbench, where the builder's tools cannot write: what the builder
        # seals is held to it. A pairing it cannot take fails here, before anyone waits.
        pairing = ((job.metadata or {}).get("adaptation") or {}).get("pairing")
        control_output = output.parent.parent / _CONTROL
        shutil.rmtree(control_output, ignore_errors=True)
        control = Workbench(
            environments=environments,
            suite_root=suite_root,
            output=control_output,
            postgres_dsn=dsn,
            profile=profile,
            pairing=pairing,
        )
        # Written last: its presence tells the builder everything else is in place.
        write_json(
            output / INPUTS,
            {
                "environments": [str(one) for one in environments],
                "suite": str(suite_root),
                # The pairing the person reviewed, which the builder's bindings are held to.
                "pairing": pairing,
            },
        )
        return _await_result(output, environments[-1], suite_root, dsn, control)


def _run_auto(
    output: Path,
    environments: list[Path],
    suite_root: Path,
    dsn: str,
    profile: Any,
    job: Any,
) -> int:
    """Run mode: no builder session. This process binds the agent itself (`auto.py`) and leaves
    the same result.json and marker a builder would."""
    from .. import spend
    from .auto import adapt

    # The platform reads this ledger before the sandbox is deleted and bills it as authoring.
    spend.journal_to(output.parent / "cost.json")
    workbench = Workbench(
        environments=environments,
        suite_root=suite_root,
        output=output,
        postgres_dsn=dsn,
        profile=profile,
        finished_marker=output.parent / FINISHED_MARKER,
        pairing=(job.metadata or {})["adaptation"].get("pairing"),
    )
    try:
        asyncio.run(adapt(workbench))
    except AdaptationFailure as exc:
        workbench.fail(exc.code, exc.message)
    result = json.loads((output / RESULT).read_text(encoding="utf-8"))
    if result.get("status") == "completed":
        # Run mode has no session: this process made the proof, and says so from its own
        # workbench, not from the files.
        record_verified(
            output.parent,
            binding={"digest": workbench.binding.digest, "mode": workbench.mode.value},
            proof_digest=(workbench.proof_ref() or {}).get("digest"),
        )
    print(json.dumps({"status": result.get("status")}))
    return 0 if result.get("status") == "completed" else 2


async def load_workbench(workspace: Path) -> Workbench:
    """The builder's side: the workbench over what the control process prepared."""
    output = workspace / ADAPTATION_DIRECTORY
    deadline = time.monotonic() + _INPUTS_WAIT_SECONDS
    while not (output / INPUTS).is_file():
        if (output / RESULT).is_file():
            result = json.loads((output / RESULT).read_text(encoding="utf-8"))
            failure = result.get("failure") or {}
            raise AdaptationFailure(
                str(failure.get("code") or "adaptation_failed"),
                str(
                    failure.get("message") or "the adaptation failed before it started"
                ),
            )
        if time.monotonic() > deadline:
            raise AdaptationFailure(
                "adaptation_inputs_missing", "the adaptation's inputs never arrived"
            )
        await asyncio.sleep(2)
    inputs = json.loads((output / INPUTS).read_text(encoding="utf-8"))
    profile = profile_from_document(
        json.loads((output / PROFILE).read_text(encoding="utf-8"))
    )
    return await asyncio.to_thread(
        Workbench,
        environments=[Path(one) for one in inputs["environments"]],
        suite_root=Path(inputs["suite"]),
        output=output,
        postgres_dsn=str(
            json.loads(_postgres_credentials(workspace).read_text(encoding="utf-8"))[
                "postgres_dsn"
            ]
        ),
        profile=profile,
        finished_marker=workspace / FINISHED_MARKER,
        pairing=inputs.get("pairing"),
    )
