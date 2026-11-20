"""Validate generated setup against the actual hosted runtime before accepting authoring.

This is setup proof, not a claim that a reference tool trajectory executed. Actual
agent tool execution remains evidence collected during the calls.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import random
import re
import shutil
import tempfile
import time
from collections import Counter
from collections.abc import Collection, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any

import fi.alk

from . import (
    bundle_v2,
    call_runner,
    failure_record,
    hosted_entrypoint,
    judge,
    live_proof,
    outbound,
    process_runtime,
    tool_routing,
)
from .contract import AgentContract
from .prove import JUDGED_CODES
from .world.snapshot import world_state_digest
from .catalogue import load_catalogue
from .certification import CheckStatus
from .generated_runtime import installed_frameworks
from .privilege import isolation_mode

if TYPE_CHECKING:
    from .job import FailureCause
    from .source_model import SourceModel
    from .world_ir import WorldIR


def _artifact_digest(path: Path) -> str:
    """Hash one authoring artifact without depending on its absolute checkout path."""

    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.read_bytes())
    elif path.is_dir():
        for item in sorted(
            candidate for candidate in path.rglob("*") if candidate.is_file()
        ):
            relative = item.relative_to(path).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            digest.update(item.read_bytes())
    return "sha256:" + digest.hexdigest()


def _runtime_snapshot(job) -> str | None:
    metadata = getattr(job, "metadata", None)
    if isinstance(metadata, dict):
        for key in ("daytona_snapshot", "sandbox_snapshot", "snapshot"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return os.environ.get("ALK_DAYTONA_SNAPSHOT") or None


def _representative_chat_scenario(scenarios, bundle: Path):
    """Prefer the generated workflow that exercises the broadest real tool surface."""

    from .chat_call_runner import _scenario_document

    candidates = [
        scenario
        for scenario in scenarios
        if getattr(scenario, "requires_tool_evidence", False)
    ]
    if not candidates:
        return scenarios[0]

    def workflow_strength(scenario):
        source_key = (
            getattr(scenario, "source_scenario_key", None) or scenario.scenario_key
        )
        solution = _scenario_document(bundle, source_key).get("solution", [])
        tools = [
            step.get("tool")
            for step in solution
            if isinstance(step, dict) and isinstance(step.get("tool"), str)
        ]
        return len(set(tools)), len(tools)

    return max(candidates, key=workflow_strength)


def _world_isolation_status(build_output: dict):
    """Classify the isolation proof at the runtime's enforced parallelism."""

    from .certification import CheckStatus

    if build_output.get("conformance") is True:
        return CheckStatus.PASSED
    if (
        build_output.get("degrade_reason") == "fixed_port"
        and build_output.get("effective_parallelism") == 1
    ):
        return CheckStatus.NOT_APPLICABLE
    return CheckStatus.NOT_RUN


# The processes that make up the world's door: the tool proxy in front of a tools service, or the in-process door.
_DOOR_PROCESSES = ("tool-proxy", "world-door")


def _door_user(manifest) -> str | None:
    """The OS user the bundle's door processes are declared to run as; None when the bundle has no door or its door
    processes do not agree on one user."""
    users = {process.user.value for process in getattr(manifest, "processes", ()) if process.name in _DOOR_PROCESSES}
    return users.pop() if len(users) == 1 else None


def _write_runtime_evidence(
    *,
    job,
    authoring: Path,
    manifest,
    count: int,
    external_provider: bool,
    tool_report,
    action_report,
    reset_equivalence,
    world_isolation,
    chat_ingress,
    native_observation: bool = False,
) -> None:
    """Seal facts which would otherwise disappear with the validation sandbox."""

    from .certification import (
        CertificationChecks,
        CheckStatus,
        GenericHarnessArtifactStore,
        RuntimeValidationEvidence,
    )

    store = GenericHarnessArtifactStore(authoring / "generic-harness")
    limitations = [
        "runtime-only agent tool effects are verified during calls, not setup certification",
        "chat readiness proves one representative ingress turn, not task success or every conversation path",
    ]
    if chat_ingress is CheckStatus.NOT_APPLICABLE:
        limitations.append(
            "chat ingress is not applicable to voice or external connect-only runtimes"
        )
    if external_provider or native_observation:
        source_schema_hash = manifest.provenance.source_digest
        world_ir_hash = _artifact_digest(Path("__external_provider_world__"))
        compiler_version = "external-provider-black-box"
        schema_and_seed = CheckStatus.NOT_APPLICABLE
        source_invariants = CheckStatus.NOT_APPLICABLE
        if external_provider:
            limitations.append(
                "external provider state and tool implementations were not available for local inspection"
            )
    if native_observation:
        compiler_version = "native-compose-observation-v1"
        limitations.append("Native application state is opaque: schema, seed, source invariants, value-level reset equivalence and state isolation are not certified. Compose lifecycle rebuilds private source copies and volumes; checks may observe only native responses. Source actions are not probed by the harness.")
    if not external_provider and not native_observation:
        source_model = store.read_source_model()
        world_ir = store.read_world_ir()
        source_schema_hash = source_model.fingerprint
        world_ir_hash = world_ir.fingerprint
        from .generic_pipeline import default_compiler_registry

        compiler_version = (
            default_compiler_registry().resolve(source_model.engine).version
        )
        schema_and_seed = CheckStatus.PASSED
        source_invariants = CheckStatus.PASSED
        from .source_data_invariants import ARTIFACT

        invariant_review = authoring / ARTIFACT
        if (
            invariant_review.is_file()
            and json.loads(invariant_review.read_text()).get("status") == "not_applicable"
        ):
            source_invariants = CheckStatus.NOT_APPLICABLE
            limitations.append(
                "No runtime SQL business tables were available for source invariants; "
                "private application state is not certified by this check"
            )
    if world_isolation is CheckStatus.NOT_RUN:
        limitations.append(
            "concurrent world isolation was not applicable or runtime parallelism was safely degraded"
        )
    elif world_isolation is CheckStatus.NOT_APPLICABLE and not external_provider and not native_observation:
        limitations.append(
            "concurrent world isolation is not applicable because a fixed-port runtime is "
            "enforced at single-world parallelism; reset equivalence remains certified"
        )
    if action_report.executable != action_report.total:
        limitations.append(
            f"{action_report.total - action_report.executable} actions require runtime or an external sandbox probe"
        )
    if action_report.certified != action_report.executable:
        limitations.append(
            f"{action_report.executable - action_report.certified} safe action probes failed at the runtime boundary"
        )

    contract = authoring / "contract.json"
    from .contract import AgentContract
    from .proofs import load_proofs
    from .tool_certification import compare_declared_tables

    proofs = load_proofs(authoring)
    try:
        declared = AgentContract.model_validate_json(contract.read_text(encoding="utf-8")) if proofs else None
    except (OSError, ValueError):
        declared = None
    if declared is not None:
        tool_report, skipped = compare_declared_tables(tool_report, declared, proofs)
        if skipped:
            limitations.append(
                "declared tables were not compared for proof runs no one tool wrote in: " + "; ".join(skipped)
            )
    scenarios = authoring / "scenarios"
    if not scenarios.exists():
        scenarios = authoring / "scenario"
    evidence = RuntimeValidationEvidence(
        source_digest=manifest.provenance.source_digest,
        source_schema_hash=source_schema_hash,
        world_ir_hash=world_ir_hash,
        compiler_version=compiler_version,
        bundle_digest=manifest.digest,
        contract_hash=_artifact_digest(contract),
        scenario_set_hash=_artifact_digest(scenarios),
        checks=CertificationChecks(
            static=CheckStatus.PASSED,
            schema_and_seed=schema_and_seed,
            processes=CheckStatus.PASSED,
            chat_ingress=chat_ingress,
            source_invariants=source_invariants,
            scenario_setup_ready=f"{count}/{count}",
            tool_contract=(
                f"{tool_report.certified_or_runtime_only}/{tool_report.total}"
            ),
            action_behavior=f"{action_report.certified}/{action_report.executable}",
            reset_equivalence=reset_equivalence,
            world_isolation=world_isolation,
        ),
        limitations=tuple(limitations),
        process_isolation=isolation_mode().value,
        door_user=_door_user(manifest),
    )
    store.write_runtime_evidence(evidence)
    store.write_tool_certification(tool_report)
    store.write_action_certification(action_report)


def _database_lanes() -> int:
    """One lane per CPU by default: each check starts a worker inside its time budget."""
    from .process_runtime import _cgroup_cpu_quota

    configured = os.environ.get("ALK_VALIDATION_DB_LANES")
    if configured:
        try:
            return max(1, int(configured))
        except ValueError:
            raise ValueError(
                f"ALK_VALIDATION_DB_LANES must be a whole number, not {configured!r}"
            ) from None
    cpus = (
        len(os.sched_getaffinity(0))
        if hasattr(os, "sched_getaffinity")
        else os.cpu_count() or 1
    )
    quota = _cgroup_cpu_quota()
    return max(1, min(cpus, int(quota)) if quota else cpus)


def _freeze_certified_bundle(bundle: Path, authoring: Path) -> None:
    """Persist the exact bundle bytes exercised by runtime validation."""

    certified_bundle = authoring / "generic-harness" / "certified-bundle"
    shutil.rmtree(certified_bundle, ignore_errors=True)
    shutil.copytree(bundle, certified_bundle)


def _setup_check_keys(bundle: Path, invariants: list, baseline_digest: str) -> list[str]:
    """One key per scenario folder, in load order, over everything its setup check depends on."""

    shared = hashlib.sha256()
    shared.update(baseline_digest.encode("utf-8"))
    shared.update(json.dumps(invariants, sort_keys=True, default=str).encode("utf-8"))
    for item in sorted(path for path in bundle.rglob("*") if path.is_file()):
        relative = item.relative_to(bundle)
        if relative.parts[0] in {"scenarios", "manifest.json"}:
            continue
        shared.update(relative.as_posix().encode("utf-8") + b"\0")
        shared.update(item.read_bytes())
    prefix = shared.hexdigest()
    return [
        "sha256:" + hashlib.sha256((prefix + _artifact_digest(folder)).encode()).hexdigest()
        for folder in sorted((bundle / "scenarios").iterdir())
        if folder.is_dir()
    ]


def _make_local_seed_files_readable(bundle: Path) -> None:
    """Allow an unprivileged data service to read public, generated seed inputs.

    The bundle is created in a private controller workspace.  PostgreSQL is deliberately
    launched as svc-data, so its ``psql -f`` needs search permission on the seed directory
    and read permission on the generated files.  Do not relax modes elsewhere in the bundle:
    credential handoffs and submitted source are outside this seed-only boundary.
    """

    seed = bundle / "seed"
    if not seed.is_dir():
        return
    bundle.chmod(bundle.stat().st_mode | 0o055)
    seed.chmod(seed.stat().st_mode | 0o055)
    for path in seed.rglob("*"):
        if path.is_symlink():
            continue
        path.chmod(path.stat().st_mode | (0o055 if path.is_dir() else 0o044))


def _has_judged_sub_goals(authoring: Path) -> bool:
    for root in (authoring, authoring / "environment-bundle"):
        try:
            catalogue = load_catalogue(root)
        except (OSError, ValueError):
            continue
        if any(goal.judged.strip() for goal in catalogue.sub_goals):
            return True
    return False


def _with_judge_check(checks, authoring: Path):
    """``checks`` with whether the judge answered, only when a judged sub-goal needs it."""
    if not _has_judged_sub_goals(authoring):
        return checks
    return checks.model_copy(update={"judge_reachable": CheckStatus(judge.judge_reachable())})


def _harness_version() -> str | None:
    return None if fi.alk.__version__.startswith("0.0.0+") else fi.alk.__version__


def _write_generic_certificate(
    job, authoring: Path, repairs, source: Path | None = None
) -> None:
    from .certification import (
        CertificationAuthoring,
        CertificationCompiler,
        CertificationRuntime,
        CertificationSource,
        CertificationStatus,
        GenericHarnessArtifactStore,
        HarnessCertification,
    )

    store = GenericHarnessArtifactStore(authoring / "generic-harness")
    evidence = store.read_runtime_evidence()
    origin = getattr(job, "source", None)
    certificate = HarnessCertification.create(
        status=CertificationStatus.CERTIFIED,
        source=CertificationSource(
            repository=getattr(origin, "repository", None),
            commit=getattr(origin, "commit_sha", None),
            digest=evidence.source_digest,
            schema_hash=evidence.source_schema_hash,
        ),
        authoring=CertificationAuthoring(
            contract_hash=evidence.contract_hash,
            world_ir_hash=evidence.world_ir_hash,
            scenario_set_hash=evidence.scenario_set_hash,
        ),
        compiler=CertificationCompiler(
            version=evidence.compiler_version,
            bundle_digest=evidence.bundle_digest,
        ),
        runtime=CertificationRuntime(
            snapshot=_runtime_snapshot(job),
            validation_attempts=len(repairs.decisions),
            harness_version=_harness_version(),
            frameworks=installed_frameworks(source),
            process_isolation=evidence.process_isolation,
            door_user=evidence.door_user,
        ),
        checks=_with_judge_check(evidence.checks, authoring),
        repairs=repairs,
        limitations=evidence.limitations,
    )
    store.write_certification(certificate)
    (authoring / "runtime-validation.json").write_text(
        certificate.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )


def _generic_candidate_hash(source: Path, authoring: Path) -> str:
    from .provision import source_fingerprint

    digest = hashlib.sha256()
    digest.update(source_fingerprint(source).encode("ascii"))
    for path in sorted(item for item in authoring.rglob("*") if item.is_file()):
        relative = path.relative_to(authoring).as_posix()
        if relative == "runtime-validation.json":
            continue
        if relative.startswith("generic-harness/") and relative not in {
            "generic-harness/source-model.json",
            "generic-harness/world-ir.json",
        }:
            continue
        digest.update(f"authoring/{relative}\n".encode("utf-8"))
        digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()


def _stored_in_world(
    authoring: Path, world: WorldIR, source: SourceModel
) -> WorldIR:
    """Keep a repaired World IR in world.sqlite, the world the environment is known by.

    Returns the world as it imports back, so the IR stays exactly what world.sqlite
    holds. A repair SQLite cannot hold as it seeds is rejected and nothing is written.
    """
    import sqlite3
    from contextlib import closing

    from .repair_patch import RepairPatchRejected
    from .world_import.sqlite import (
        import_sqlite_world,
        world_holds,
        write_sqlite_world,
    )

    path = authoring / "world.sqlite"
    if not path.is_file():
        return world
    with closing(sqlite3.connect(path)) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            wrote = write_sqlite_world(connection, world, source)
            stored = import_sqlite_world(connection, source, validate=False).world
            if not world_holds(stored, world, source):
                raise ValueError("world_not_held")
        except (sqlite3.Error, ValueError, OverflowError) as exc:
            connection.rollback()
            raise RepairPatchRejected("repair_patch_not_storable") from exc
        if wrote:
            connection.commit()
        else:
            connection.rollback()
    return stored


def _invariants_violated(detail: str) -> tuple:
    """Certify-only's typed rejection for seeded data that breaks a saved invariant."""
    from .diagnostics import HarnessDiagnostic
    from .job import HarnessStage

    return (
        HarnessDiagnostic.create(
            stage=HarnessStage.VALIDATING_ENVIRONMENT,
            component="source_data_invariants",
            code="source_data_invariant_violated",
            message=detail,
        ),
    )


def _fallback_diagnostic(error, *, authoring: Path | None = None):
    from .diagnostics import HarnessDiagnostic
    from .job import HarnessStage

    detail = str(error)
    normalized = detail.casefold()
    provider_lifecycle_failure = "provider_lifecycle/" in normalized
    if (
        provider_lifecycle_failure
        and "http error 408" not in normalized
        and "http error 429" not in normalized
        and re.search(r"http error 4\d\d\b", normalized)
    ):
        # A lifecycle command is submitted source. A definitive external 4xx means
        # that source/config/credential was rejected; changing generated world data
        # cannot make it valid. Keep 408 and 429 in the transient bucket below.
        code = "external_service_request_rejected"
        stage = HarnessStage.CONNECTING_AGENT
    elif provider_lifecycle_failure and (
        re.search(r"http error 5\d\d\b", normalized)
        or "http error 408" in normalized
        or "http error 429" in normalized
        or any(
            marker in normalized
            for marker in (
                "clientconnectordnserror",
                "name resolution",
                "temporary failure in name resolution",
                "connection reset",
                "connection refused",
                "timed out",
                "timeout",
            )
        )
    ):
        code = "external_service_unavailable"
        stage = HarnessStage.CONNECTING_AGENT
    elif error.phase == "scenarios":
        code = "ready_condition_invalid"
        stage = HarnessStage.VALIDATING_SCENARIOS
    elif error.phase == "infrastructure":
        code = "process_dependency_timeout"
        stage = HarnessStage.BUILDING_ENVIRONMENT
    elif (
        detail.removeprefix("BundleAuthorError: ")
        == "generic_pipeline_source_schema_required: no source-owned PostgreSQL schema found"
        and authoring is not None
        and (authoring / "world.sqlite").is_file()
        and json.loads((authoring / "contract.json").read_text(encoding="utf-8")).get(
            "data_store"
        )
        is None
    ):
        code = "generated_storage_declaration_missing"
        stage = HarnessStage.BUILDING_ENVIRONMENT
    elif error.phase == "runtime" or _looks_like_runtime_plan_failure(normalized):
        code = "generated_runtime_plan_invalid"
        stage = HarnessStage.BUILDING_ENVIRONMENT
    else:
        code = "generated_setup_invalid"
        stage = HarnessStage.VALIDATING_ENVIRONMENT
    return HarnessDiagnostic.create(
        stage=stage,
        component="runtime_validation",
        code=code,
        message=str(error),
    )


def _looks_like_runtime_plan_failure(detail: str) -> bool:
    """Recognize failures in the generated execution plan, not agent behavior/data.

    The concrete exception types are deliberately allowed to vary across Docker, Compose,
    process and HTTP runtimes.  These stable boundary messages are produced before a scenario
    can exercise the agent and are therefore safe to route to repository/runtime inspection.
    """

    return any(
        marker in detail
        for marker in (
            "generated_runtime_",
            "bundle_secret_material_detected",
            "no runnable shipped entrypoint",
            "dependency manifest",
            "failed to build",
            "docker build",
            "process exited",
            "process failed",
            "readiness probe",
            "health check",
            "healthcheck",
            "entrypoint",
            "module not found",
            "modulenotfounderror",
            "cannot find module",
            "command not found",
        )
    )


def _provider_owns_tool_execution(job) -> bool:
    from .authoring_entrypoint import PROVIDER_MANAGED_CONNECTORS
    from .job import ProviderExecutionMode

    agent = getattr(job, "agent", None)
    return (
        str(getattr(agent, "connector", "")).lower()
        in PROVIDER_MANAGED_CONNECTORS
        and getattr(agent, "mode", None)
        in {
            ProviderExecutionMode.CONNECT_ONLY,
            ProviderExecutionMode.ENVIRONMENT_BACKED,
            ProviderExecutionMode.PROVIDER_IMPORT,
        }
    )


def _repair_guidance(diagnostics) -> str:
    """Render the complete secret-safe evidence needed for one constrained repair."""

    rendered = []
    for item in sorted(
        diagnostics, key=lambda diagnostic: (diagnostic.code, diagnostic.component)
    ):
        location = (
            item.location.model_dump(mode="json", exclude_none=True)
            if item.location is not None
            else None
        )
        parts = [f"{item.code}@{item.component}"]
        if item.repair_strategy:
            parts.append(f"strategy={item.repair_strategy}")
        if location:
            parts.append(
                "location="
                + json.dumps(location, sort_keys=True, separators=(",", ":"))
            )
        # HarnessDiagnostic.create already redacts this value. Keep the model input
        # bounded as a second line of defence against oversized provider traces.
        parts.append(f"evidence={item.redacted_message[:4000]}")
        rendered.append(" | ".join(parts))
    return "\n".join(rendered)[:12000]


def _mask_diagnostics(diagnostics, masks):
    """The typed diagnostics as the repair step may see them: message and references masked
    against the source's listed files (before any cut). With no masks (the walk failed), the codes
    only."""
    from .process_diagnostics import mask_source_secrets

    if masks is None:
        return tuple(
            item.model_copy(update={"redacted_message": "withheld", "evidence_refs": ()})
            for item in diagnostics
        )
    return tuple(
        item.model_copy(
            update={
                "redacted_message": mask_source_secrets(item.redacted_message, masks),
                "evidence_refs": tuple(
                    mask_source_secrets(reference, masks) for reference in item.evidence_refs
                ),
            }
        )
        for item in diagnostics
    )


def _needs_runtime_plan_repair(diagnostics) -> bool:
    """Route execution construction failures away from world-data authoring."""

    from .repair_controller import is_chat_setup_investigation

    if is_chat_setup_investigation(diagnostics):
        return True
    runtime_codes = {
        "generated_runtime_plan_invalid",
        "chat_ingress_configuration_invalid",
        "process_dependency_timeout",
        "tool_endpoint_unreachable",
    }
    return bool(diagnostics) and all(
        diagnostic.code in runtime_codes for diagnostic in diagnostics
    )


class RuntimeValidationError(RuntimeError):
    def __init__(
        self,
        phase: str,
        detail: str,
        *,
        diagnostics=(),
        cause: FailureCause | None = None,
    ):
        self.phase = phase
        self.diagnostics = tuple(diagnostics)
        # The closed reason a reader without access to guest text gets; None when unknown.
        self.cause = cause
        self.process_evidence = ""
        self.target_process_evidence = ""
        super().__init__(detail)


class _GeneratedWorldActionAdapter:
    """Expose the generated world through the generic action-probe boundary."""

    def __init__(self, world) -> None:
        self.world = world
        self.baseline = world.checkpoint()

    def invoke(self, action, arguments):
        from .action_certification import ActionInvocation
        from .world.handle import WorldUnavailable

        try:
            call = self.world.call(action.name, arguments)
        except WorldUnavailable as exc:
            # A source-owned callable that is reachable only through the running agent is not
            # an executable action at this validation boundary.  Reporting NOT_RUN keeps the
            # evidence honest; treating the absent direct adapter as a broken customer action
            # made every repository-hosted tool fail before scenarios could exercise it.
            return ActionInvocation(not_run=True, error=str(exc))
        return ActionInvocation(
            result=call.result,
            refused=bool(call.refused),
            error=(call.error or "action execution failed")
            if not call.ok and not call.refused
            else None,
        )

    def checkpoint(self) -> str:
        return world_state_digest(self.world)

    def reset(self) -> None:
        self.world.revert(self.baseline)


async def _probe_runtime_readiness(
    *, job, source, authoring, manifest, contract, provider, runtime, factory,
    work, values, redactions, compare_baseline=True,
):
    from .chat_call_runner import _tools, _chat_target_timeout_seconds, agent_chat_timeout_seconds
    from .chat_readiness import probe_chat_ingress
    from .process_runtime import ProcessRuntimeError

    interface = contract.runtime.interface if contract.runtime else None
    endpoint = runtime.endpoints.get("target_http")
    if interface is None or interface.kind not in {"http", "callable", "command"} or endpoint is None:
        raise RuntimeValidationError(
            "runtime", "generated_runtime_plan_invalid: unsupported chat ingress"
        )
    baseline = await factory.create(runtime, rng=random.Random(job.seed or 0))
    baseline_digest = world_state_digest(baseline) if compare_baseline else None
    receipt = {
        "status": "failed",
        "candidate_hash": _generic_candidate_hash(source, authoring),
        "bundle_digest": manifest.digest,
        "reset": "failed",
    }
    try:
        try:
            await probe_chat_ingress(
                interface=interface,
                endpoint=endpoint.address,
                opening_message="Hello",
                authoring=authoring,
                candidate_hash=receipt["candidate_hash"],
                bundle_digest=manifest.digest,
                secret_values=values,
                redaction_values=redactions,
                local_auth_values=getattr(runtime, "_local_auth_values", None),
                tools=_tools(contract) if interface.include_tools else (),
                timeout=agent_chat_timeout_seconds(job) or _chat_target_timeout_seconds(),
            )
        finally:
            try:
                await provider.reset(runtime, work_directory=work)
                if compare_baseline:
                    restored = await factory.create(runtime, rng=random.Random(job.seed or 0))
                    if world_state_digest(restored) != baseline_digest:
                        raise RuntimeValidationError(
                            "environment", "Readiness reset did not restore the baseline"
                        )
            except RuntimeValidationError:
                raise
            except Exception as exc:
                raise RuntimeValidationError(
                    "environment",
                    f"Readiness reset failed: {exc}",
                    diagnostics=tuple(getattr(exc, "diagnostics", ())),
                    cause=exc.cause if isinstance(exc, ProcessRuntimeError) else None,
                ) from exc
            receipt["reset"] = "equivalent" if compare_baseline else "restarted"
        receipt["status"] = "passed"
    finally:
        path = authoring / "generic-harness" / "runtime-readiness.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")


def _prepare_runtime_work(job, work: Path, secrets_path: Path, *, local_runtime: bool) -> Path:
    """Lay out a disposable runtime workspace: the job's sizes and a private copy of the secrets."""
    runtime = getattr(job, "runtime", None)
    (work / "job.json").write_text(
        json.dumps(
            {
                "job_id": getattr(job, "job_id", ""),
                "runtime": {
                    "cpu_units": getattr(runtime, "cpu_units", 1),
                    "memory_mb": getattr(runtime, "memory_mb", 512),
                }
            }
        )
    )
    if local_runtime:
        # Child processes drop to svc-* identities. They need search permission on the
        # disposable workspace parent in order to reach their individually chowned trees;
        # secrets below retain mode 0600 and are injected by the controller.
        work.chmod(0o755)
    secrets = work / "secrets.json"
    secrets.write_bytes(secrets_path.read_bytes())
    secrets.chmod(0o600)
    return secrets


def _runtime_provider(job, secrets: Path, authoring: Path, *, local_runtime: bool, generic: bool):
    """The process runtime provider validation starts the bundle with."""
    capabilities = None if local_runtime else outbound.load_capabilities(unlink=False)
    transport = outbound.RequestsTransport()
    return process_runtime.ProcessRuntimeProvider(
        secrets_path=secrets,
        secret_purpose_map=hosted_entrypoint.job_secret_purposes(job),
        # A local parity run shares the host/container process namespace and must resolve
        # the same unprivileged identities installed in its image. A hosted sandbox runs
        # each declared user only when the platform granted per-user isolation.
        **(
            {"user_resolver": process_runtime.default_user_resolver, "require_declared_user": False}
            if local_runtime and os.environ.get("ALK_FIXED_SANDBOX_USER") != "1"
            else process_runtime.hosted_user_resolution()
        ),
        public_url_resolver=(
            None
            if capabilities is None
            else lambda port, ttl: hosted_entrypoint.resolve_hosted_public_url(
                capabilities, transport, port=port, expires_in_seconds=ttl
            )
        ),
        provider_attempt_id=(capabilities.attempt_id if capabilities is not None else None),
        provider_expires_at=(capabilities.expires_at if capabilities is not None else None),
        generic_artifact_root=(authoring / "generic-harness") if generic else None,
    )


async def validate_once(
    job,
    source: Path,
    authoring: Path,
    *,
    secrets_path=Path("/run/futureagi/secrets.json"),
    local_runtime: bool = False,
    reuse_invariants_only: bool = False,
    review=None,
    readiness_only: bool = False,
) -> int:
    from .bundle_author_v2 import author_bundle_v2
    from .compose_integration import enabled as compose_enabled
    from .hosted_compose_bootstrap import initialize_hosted_compose
    from .contract import AgentContract
    from .hosted_entrypoint import ProcessWorldFactory, job_secret_purposes
    from .hosted_scheduler import _classify_ready, _run_phase
    from .job import ProviderExecutionMode, SourceKind
    from .process_preflight import preflight_bundle
    from .process_runtime import ProcessRuntimeError
    from .scenario_source import does_nothing, load_scenarios
    from .source_data_invariants import author_invariants, check_invariants
    from .tool_certification import ToolAvailability, certify_tool_inventory

    broker_socket = None
    if not local_runtime:
        broker_socket = await asyncio.to_thread(initialize_hosted_compose, job, source)
        if broker_socket is not None:
            os.environ["ALK_COMPOSE_BROKER_SOCKET"] = str(broker_socket)

    if getattr(job, "require_tool_workflow", False):
        from .contract import validate_tool_workflow

        try:
            validate_tool_workflow(
                AgentContract.model_validate_json(
                    (authoring / "contract.json").read_text(encoding="utf-8")
                )
            )
        except (ValueError, OSError) as exc:
            raise RuntimeValidationError("environment", str(exc)) from exc

    external_provider = (
        getattr(getattr(job, "source", None), "kind", None) is SourceKind.PROVIDER
        and getattr(getattr(job, "agent", None), "mode", None)
        is ProviderExecutionMode.CONNECT_ONLY
    )
    provider_owned_tools = _provider_owns_tool_execution(job)
    generic = bool(
        isinstance(getattr(job, "metadata", None), dict)
        and job.metadata.get("generic_harness_v1") is True
    )
    if readiness_only and not generic:
        raise RuntimeValidationError("runtime", "Early readiness requires the generic runtime")
    if readiness_only:
        (authoring / "generic-harness" / "runtime-readiness.json").unlink(missing_ok=True)
    if generic:
        from .certification import CheckStatus, GenericHarnessArtifactStore

        artifact_root = authoring / "generic-harness"
        # A prior certified bundle is an output of validation, never an input to a fresh
        # validation attempt.  Remove it before compiling so repairs cannot accidentally
        # validate yesterday's environment.
        shutil.rmtree(artifact_root / "certified-bundle", ignore_errors=True)
        (artifact_root / GenericHarnessArtifactStore.RUNTIME_EVIDENCE).unlink(
            missing_ok=True
        )
        (artifact_root / GenericHarnessArtifactStore.CERTIFICATION).unlink(
            missing_ok=True
        )
        (authoring / "runtime-validation.json").unlink(missing_ok=True)

    # The real execution consumes its credential file. Validation gets a private copy,
    # with the same purpose map, so it cannot destroy the execution handoff.
    temporary_parent = authoring.parent
    if local_runtime:
        # The local sandbox itself runs in Docker while ProcessRuntimeProvider may ask the
        # host Docker daemon to build/start submitted Compose services.  A path in the sandbox's
        # private artifact volume is not visible to that daemon.  The upload root is deliberately
        # bind-mounted at the same absolute path on both sides, so use it for disposable runtime
        # candidates when present.  A plain local CLI has no such variable and keeps the historic
        # authoring-adjacent temporary directory.
        shared_root = os.environ.get("ALK_SANDBOX_UPLOAD_ROOT", "").strip()
        if shared_root:
            temporary_parent = Path(shared_root).expanduser().resolve()
            temporary_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="runtime-validation-", dir=temporary_parent
    ) as root:
        work = Path(root)
        secrets = _prepare_runtime_work(job, work, secrets_path, local_runtime=local_runtime)
        redaction_values = json.loads(secrets.read_text())
        process_logs = authoring / "generic-harness" / "process-logs"
        logs_before = set(process_logs.glob("*.log"))
        validation_error = None
        manifest = None
        from .hosted_entrypoint import peek_target_provider_secret_values

        target_secret_values = peek_target_provider_secret_values(
            secrets, job_secret_purposes(job)
        )
        secret_values = tuple(
            str(value) for value in json.loads(secrets.read_text()).values()
        )
        provider = _runtime_provider(
            job, secrets, authoring, local_runtime=local_runtime, generic=generic
        )
        # Parallel runtime instances inside the one sandbox, each with its own ports and databases.
        # One setting for a build's validation and a Validate's proof (`live_proof.prove_concurrency`).
        requested_lanes = live_proof.prove_concurrency()
        # Setup checks touch only the world's database, so a single-database world checks each
        # scenario on its own clone of the sealed baseline instead of a whole agent stack.
        database_lanes = _database_lanes()
        executor = ThreadPoolExecutor(
            max_workers=max(requested_lanes, database_lanes),
            thread_name_prefix="runtime-validation",
        )
        started = time.monotonic()
        phase = "runtime"
        try:
            bundle = work / "bundle"
            if not readiness_only:
                # A pre-run session written from an actor record that has since changed is written again first.
                from .pre_run import refresh_stale

                await refresh_stale(authoring)
            manifest = await asyncio.to_thread(
                author_bundle_v2,
                source=source,
                job=job,
                authoring=authoring,
                output=bundle,
                include_scenarios=not readiness_only,
            )
            if local_runtime:
                _make_local_seed_files_readable(bundle)
            validation_parallelism = (
                1
                if external_provider or review is not None or readiness_only
                else max(requested_lanes, 2 if generic else 1)
            )
            preflight_bundle(
                bundle,
                manifest,
                parallelism=validation_parallelism,
                secret_refs=job_secret_purposes(job),
                allow_compose=(local_runtime and compose_enabled()) or broker_socket is not None,
            )
            from .bundle_v2 import RuntimeKindV2
            if manifest.runtime.kind is RuntimeKindV2.COMPOSE:
                from .compose_integration import validate_native
                return await validate_native(job=job, source=source, authoring=authoring,
                    bundle=bundle, manifest=manifest, work=work, values=target_secret_values,
                    redactions=redaction_values, executor=executor, readiness_only=readiness_only)

            runtimes = await provider.provision(
                manifest,
                source=source,
                bundle_dir=bundle,
                work_directory=work,
                instances=validation_parallelism,
            )
            if review is not None:
                # Only the source data review, against this world, while scenarios are written.
                await provider.reset(runtimes[0], work_directory=work)
                world = await ProcessWorldFactory(work).create(
                    runtimes[0], rng=random.Random(job.seed or 0)
                )
                await review(world.read_only(), runtimes[0].endpoints)
                return 0
            # From here onward the generated build/start plan has succeeded. Failures belong to
            # the generated world, tool boundary, or scenarios rather than runtime discovery.
            phase = "environment"
            tool_report = None
            action_report = None
            world_isolation = None
            if generic:
                from .action_certification import certify_actions
                from .bundle_author_v2 import _runtime_component
                from .source_folder import contract_folder

                contract = AgentContract.model_validate_json(
                    (bundle / "contract.json").read_text(encoding="utf-8")
                )
                folder = contract_folder(contract)
                tool_report = certify_tool_inventory(
                    contract,
                    bundle,
                    external_provider=provider_owned_tools,
                    # What the agent's install can import: its folder, or the project above
                    # it, shared code included. A tool outside that is not this agent's.
                    source=_runtime_component(source.resolve(), folder) if folder else source,
                )
                rejected = {
                    item.tool: item.reason
                    for item in tool_report.tools
                    if item.availability is ToolAvailability.REJECTED
                }
                if rejected:
                    from .diagnostics import DiagnosticLocation, HarnessDiagnostic
                    from .job import HarnessStage

                    diagnostics = tuple(
                        HarnessDiagnostic.create(
                            stage=HarnessStage.VALIDATING_ENVIRONMENT,
                            component="tool_contract",
                            code="tool_schema_mismatch",
                            message=f"{name}: {rejected[name]}",
                            location=DiagnosticLocation(tool=name),
                        )
                        for name in sorted(rejected)
                    )
                    # Each tool with its reason, in the message the platform keeps: the diagnostics alone are not stored.
                    raise RuntimeValidationError(
                        "environment",
                        "Tool contract certification rejected: "
                        + "; ".join(f"{name} ({rejected[name][:200]})" for name in sorted(rejected))[:2000],
                        diagnostics=diagnostics,
                    )
                source_model = GenericHarnessArtifactStore(
                    authoring / "generic-harness"
                ).read_source_model()
                action_report = certify_actions(source_model.actions, adapter=None)
                build_output = json.loads(
                    (work / "artifacts" / "build.json").read_text(encoding="utf-8")
                )
                conformance = build_output.get("conformance")
                if conformance is False:
                    raise RuntimeValidationError(
                        "environment",
                        "Concurrent world isolation conformance failed: "
                        + str(
                            build_output.get("conformance_reason") or "unknown reason"
                        ),
                    )
                # A source process that genuinely hard-codes its listen port cannot coexist with
                # a second copy in the same network namespace. The process provider enforces that
                # physical constraint by returning exactly one world. There is no concurrent
                # boundary to certify at that supported parallelism; clean lifecycle reuse is
                # still proved independently by reset_equivalence.
                world_isolation = _world_isolation_status(build_output)
            factory = ProcessWorldFactory(work, bundle)
            runtime = runtimes[0]
            if readiness_only:
                await _probe_runtime_readiness(
                    job=job, source=source, authoring=authoring, manifest=manifest,
                    contract=contract, provider=provider, runtime=runtime, factory=factory,
                    work=work, values=target_secret_values, redactions=redaction_values,
                )
                return 0
            lanes = len(runtimes) or 1
            phase = "scenarios"
            scenarios = await asyncio.to_thread(load_scenarios, bundle)
            if len(scenarios) != job.scenario_count:
                raise RuntimeValidationError(
                    phase, "Runtime scenario count differs from the requested count"
                )
            targets: list = list(runtimes)
            if (
                generic
                and not external_provider
                and await provider.clone_world_database(runtime, "lane0") is not None
            ):
                targets = [
                    f"lane{index}"
                    for index in range(max(1, min(database_lanes, len(scenarios))))
                ]
                lanes = len(targets)

            baseline_state_digest: str | None = None
            cleared: list = []

            # A failure under an in-place reset is re-checked once against a full restart.
            in_place = os.environ.get("ALK_VALIDATION_RESET_IN_PLACE", "1") != "0"

            async def check_setups(invariants, suite=None):
                suite = scenarios if suite is None else suite
                failures: list[str] = []
                diagnostics: list = []

                async def lane(own, against):
                    for scenario in own:
                        try:
                            await check_setup(scenario, invariants, against)
                        except RuntimeValidationError as exc:
                            failures.append(str(exc))
                            diagnostics.extend(exc.diagnostics)
                        else:
                            cleared.append(scenario)

                await asyncio.gather(
                    *(
                        lane(suite[index::lanes], targets[index])
                        for index in range(lanes)
                        if suite[index::lanes]
                    )
                )
                if failures:
                    raise RuntimeValidationError(
                        phase, "\n".join(sorted(failures)), diagnostics=diagnostics
                    )

            async def check_setup(scenario, invariants, against=None):
                try:
                    await check_setup_once(scenario, invariants, against, in_place)
                except Exception:
                    if not in_place:
                        raise
                    await check_setup_once(scenario, invariants, against, False)

            from .local_certified_runtime import LocalCertifiedSink

            phase_sink = LocalCertifiedSink(
                authoring / "generic-harness" / "phase-observations"
            )

            async def check_setup_once(scenario, invariants, against, keep_processes):
                nonlocal baseline_state_digest
                if isinstance(against, str):
                    against = await provider.clone_world_database(runtime, against)
                else:
                    against = runtime if against is None else against
                    await provider.reset(
                        against, work_directory=work, keep_processes=keep_processes
                    )
                world = await factory.create(against, rng=random.Random(job.seed or 0))
                if generic and not external_provider:
                    current_digest = world_state_digest(world)
                    if baseline_state_digest is None:
                        baseline_state_digest = current_digest
                    elif current_digest != baseline_state_digest:
                        raise RuntimeValidationError(
                            phase,
                            f"{scenario.scenario_key}: reset did not restore the certified baseline",
                        )
                for name, fn, target, timeout in (
                    ("setup", scenario.setup, world, 30.0),
                    ("ready", scenario.ready, world.read_only(), 15.0),
                ):
                    result = await _run_phase(
                        fn, target, timeout=timeout, phase=name, executor=executor,
                        audit_log=phase_sink.log,
                        audit_context={"scenario_key": scenario.scenario_key, "world_index": against.world_index, "stage": "validation"},
                    )
                    if result.failure:
                        raise RuntimeValidationError(
                            phase, f"{scenario.scenario_key}: {name}: {result.failure}"
                        )
                    if name == "setup":
                        try:
                            await check_invariants(
                                world.read_only(),
                                invariants,
                                scenario_key=scenario.scenario_key,
                            )
                        except Exception as exc:
                            detail = f"{scenario.scenario_key}: source data: {exc}"
                            raise RuntimeValidationError(
                                phase,
                                detail,
                                diagnostics=_invariants_violated(detail)
                                if reuse_invariants_only and isinstance(exc, ValueError)
                                else (),
                            ) from exc
                    if name == "ready":
                        verdict = _classify_ready(result.value)
                        if verdict.broken or not verdict.held:
                            raise RuntimeValidationError(
                                phase,
                                f"{scenario.scenario_key}: ready precondition did not hold",
                            )

            print(
                f"runtime validation: {len(scenarios)} scenarios across {lanes} lane"
                f"{'s' if lanes != 1 else ''}, environment ready in "
                f"{time.monotonic() - started:.0f}s",
                flush=True,
            )
            invariants: list = []
            if external_provider:
                # A connect-only provider owns its state and executes its tools outside
                # this sandbox. There is no harness-owned source database to probe or
                # seed, so source-data invariant review would invent a local environment.
                print(
                    "runtime validation: external provider black-box mode; "
                    "skipping local source-data invariant review",
                    flush=True,
                )
                active = [
                    scenario
                    for scenario in scenarios
                    if not (
                        does_nothing(scenario.setup) and does_nothing(scenario.ready)
                    )
                ]
                print(
                    f"runtime validation: {len(scenarios) - len(active)} of {len(scenarios)} "
                    "scenarios set up nothing and hold by construction; walking the rest",
                    flush=True,
                )
                await check_setups(invariants, active)
                if generic:
                    _write_runtime_evidence(
                        job=job,
                        authoring=authoring,
                        manifest=manifest,
                        count=len(scenarios),
                        external_provider=True,
                        tool_report=tool_report,
                        action_report=action_report,
                        reset_equivalence=CheckStatus.NOT_APPLICABLE,
                        world_isolation=CheckStatus.NOT_APPLICABLE,
                        chat_ingress=CheckStatus.NOT_APPLICABLE,
                    )
                    _freeze_certified_bundle(bundle, authoring)
                return len(scenarios)
            if generic:
                from .chat_call_runner import (
                    _scenario_document,
                    _with_scenario_edits,
                    _tools,
                    _chat_target_timeout_seconds,
                    agent_chat_timeout_seconds,
                )
                from .chat_readiness import probe_chat_ingress, probe_chat_workflow

                chat_ingress = CheckStatus.NOT_APPLICABLE
                interface = contract.runtime.interface if contract.runtime else None
                if contract.modality != "voice":
                    if interface is None or interface.kind not in {"http", "callable", "command"}:
                        raise RuntimeValidationError(
                            "runtime",
                            "generated_runtime_plan_invalid: unsupported chat ingress",
                        )
                    if not scenarios:
                        raise RuntimeValidationError(
                            "environment",
                            "Chat readiness requires a representative scenario",
                        )
                    representative = _representative_chat_scenario(scenarios, bundle)
                    await check_setup(representative, [])
                    endpoint = runtime.endpoints.get("target_http")
                    if endpoint is None:
                        raise RuntimeValidationError(
                            "runtime",
                            "generated_runtime_plan_invalid: target_http is absent",
                        )
                    source_key = (
                        getattr(representative, "source_scenario_key", None)
                        or representative.scenario_key
                    )
                    document = _with_scenario_edits(
                        _scenario_document(bundle, source_key), job.metadata, source_key
                    )
                    if getattr(representative, "requires_tool_evidence", False):
                        await probe_chat_workflow(
                            job=job, source=source, bundle=bundle, authoring=authoring,
                            work_directory=work, runtime=runtime, scenario=representative,
                            candidate_hash=_generic_candidate_hash(source, authoring),
                            bundle_digest=manifest.digest,
                            secret_values=target_secret_values,
                            redaction_values=redaction_values,
                            evidence_seam=manifest.runtime.evidence_seam,
                        )
                    else:
                        since = time.time()
                        await probe_chat_ingress(
                            interface=interface,
                            endpoint=endpoint.address,
                            document=document,
                            scenario_key=representative.scenario_key,
                            authoring=authoring,
                            candidate_hash=_generic_candidate_hash(source, authoring),
                            bundle_digest=manifest.digest,
                            secret_values=target_secret_values,
                            redaction_values=redaction_values,
                            local_auth_values=getattr(runtime, "_local_auth_values", None),
                            tools=_tools(contract) if interface.include_tools else (),
                            scenario_id=representative.scenario_id,
                            timeout=agent_chat_timeout_seconds(job) or _chat_target_timeout_seconds(),
                        )
                        # A test message can call tools too; then ask for the rest.
                        try:
                            called = call_runner.collect_file_tool_calls(runtime)
                        except Exception as unread:  # noqa: BLE001 - no trace, nothing observed
                            logger.warning("agent's tool trace not read: %s", type(unread).__name__)
                            logger.debug("the failure in full", exc_info=True)
                            called = ()
                        tool_routing.record_tool_routing(
                            authoring,
                            representative.scenario_key,
                            contract,
                            runtime,
                            received=tool_routing.world_call_log(runtime, since),
                            reported=called,
                            bundle=bundle,
                        )
                    # Whichever conversation qualified the agent, each tool it did not show is asked once.
                    await tool_routing.request_uncovered_tools(
                        interface=interface,
                        endpoint=endpoint.address,
                        runtime=runtime,
                        contract=contract,
                        authoring=authoring,
                        bundle=bundle,
                        source=source,
                        secret_values=target_secret_values,
                        candidate_hash=_generic_candidate_hash(source, authoring),
                        local_auth_values=getattr(runtime, "_local_auth_values", None),
                    )
                    chat_ingress = CheckStatus.PASSED
            phase = "environment"
            await provider.reset(runtime, work_directory=work)
            baseline = await factory.create(runtime, rng=random.Random(job.seed or 0))
            print("runtime validation: reviewing source data invariants", flush=True)
            invariants[:] = await author_invariants(
                source,
                authoring,
                baseline.read_only(),
                endpoints=runtime.endpoints,
                reuse_only=reuse_invariants_only,
            )
            # Review probes may have effects; none belongs in the test baseline.
            await provider.reset(runtime, work_directory=work)
            baseline = await factory.create(runtime, rng=random.Random(job.seed or 0))
            try:
                await check_invariants(baseline.read_only(), invariants)
            except ValueError as exc:
                if not reuse_invariants_only:
                    raise
                raise RuntimeValidationError(
                    phase, str(exc), diagnostics=_invariants_violated(str(exc))
                ) from exc
            if generic:
                from .action_certification import certify_actions

                source_model = GenericHarnessArtifactStore(
                    authoring / "generic-harness"
                ).read_source_model()
                action_report = certify_actions(
                    source_model.actions,
                    adapter=_GeneratedWorldActionAdapter(baseline),
                )
                GenericHarnessArtifactStore(
                    authoring / "generic-harness"
                ).write_action_certification(action_report)
                from .action_certification import ActionProbeStatus
                from .diagnostics import DiagnosticLocation, HarnessDiagnostic
                from .job import HarnessStage

                failed_actions = [
                    item
                    for item in action_report.actions
                    if item.status is ActionProbeStatus.FAILED
                ]
                if failed_actions:
                    raise RuntimeValidationError(
                        "environment",
                        "Action behavior certification failed: "
                        + ", ".join(item.action for item in failed_actions),
                        diagnostics=tuple(
                            HarnessDiagnostic.create(
                                stage=HarnessStage.VALIDATING_ENVIRONMENT,
                                component="action_behavior",
                                code="action_probe_failed",
                                message=f"{item.action}: {item.reason}",
                                location=DiagnosticLocation(tool=item.action),
                            )
                            for item in failed_actions
                        ),
                    )
                # Probes may mutate the disposable world. Restore before scenario checks.
                await provider.reset(runtime, work_directory=work)
                baseline = await factory.create(
                    runtime, rng=random.Random(job.seed or 0)
                )
            phase = "scenarios"
            walk = time.monotonic()
            pending = scenarios
            if generic:
                # A scenario that already passed against this exact baseline, bundle and
                # invariants passes again, so a repair re-checks only what failed or changed.
                baseline_state_digest = world_state_digest(baseline)
                passed_path = authoring / "generic-harness" / "setup-checks-passed.json"
                passed = (
                    set(json.loads(passed_path.read_text(encoding="utf-8")))
                    if passed_path.is_file()
                    else set()
                )
                key_of = dict(
                    zip(
                        map(id, scenarios),
                        _setup_check_keys(bundle, invariants, baseline_state_digest),
                    )
                )
                pending = [item for item in scenarios if key_of[id(item)] not in passed]
            try:
                await check_setups(invariants, pending)
            finally:
                if generic:
                    passed.update(key_of[id(item)] for item in cleared)
                    passed_path.write_text(
                        json.dumps(sorted(passed)) + "\n", encoding="utf-8"
                    )
            print(
                f"runtime validation: setup pass {time.monotonic() - walk:.0f}s for "
                f"{len(pending)} of {len(scenarios)} scenarios with {len(invariants)} "
                f"invariants, complete in {time.monotonic() - started:.0f}s",
                flush=True,
            )
            if not readiness_only:
                # While the stack is up, play each case's reference and controls through the
                # service-backed and MCP Toolbox tools for real; never fails validation.
                try:
                    await play_this_attempt(
                        runtimes=runtimes,
                        authoring=authoring,
                        contract=AgentContract.model_validate_json(
                            (bundle / "contract.json").read_text(encoding="utf-8")
                        ),
                        runtime=runtime,
                        provider=provider,
                        factory=factory,
                        work=work,
                        seed=job.seed or 0,
                    )
                except Exception as exc:  # noqa: BLE001 - live proofs only add evidence
                    print(f"runtime validation: live proofs not run: {outbound.redact_outbound_text(f'{type(exc).__name__}: {exc}')}", flush=True)
            if generic:
                await provider.reset(runtime, work_directory=work)
                reset_world = await factory.create(
                    runtime, rng=random.Random(job.seed or 0)
                )
                if (
                    baseline_state_digest is None
                    or world_state_digest(reset_world) != baseline_state_digest
                ):
                    raise RuntimeValidationError(
                        "environment",
                        "Final reset did not restore the certified baseline",
                    )
                _write_runtime_evidence(
                    job=job,
                    authoring=authoring,
                    manifest=manifest,
                    count=len(scenarios),
                    external_provider=False,
                    tool_report=tool_report,
                    action_report=action_report,
                    reset_equivalence=CheckStatus.PASSED,
                    world_isolation=world_isolation,
                    chat_ingress=chat_ingress,
                )
                # Freeze the exact bytes that passed the real runtime checks.  Recompiling from
                # the authoring archive in the execution sandbox can otherwise drift on inputs
                # that are deliberately outside the source fingerprint (generated build/cache
                # artifacts are one example).  The consumer re-verifies the certificate and all
                # authoring fingerprints before accepting this directory.
                _freeze_certified_bundle(bundle, authoring)
            return len(scenarios)
        except RuntimeValidationError as exc:
            validation_error = RuntimeValidationError(
                exc.phase,
                outbound.redact_outbound_text(
                    str(exc), extra_secret_values=secret_values
                ),
                diagnostics=exc.diagnostics,
                cause=exc.cause,
            )
            raise validation_error from None
        except Exception as exc:
            if "CERTIFICATE_VERIFY_FAILED" in str(exc):
                # Generated data cannot repair the infrastructure trust store.
                phase = "infrastructure"
            validation_error = RuntimeValidationError(
                phase,
                outbound.redact_outbound_text(
                    f"{type(exc).__name__}: {exc}", extra_secret_values=secret_values
                ),
                diagnostics=tuple(getattr(exc, "diagnostics", ())),
                cause=exc.cause if isinstance(exc, ProcessRuntimeError) else None,
            )
            raise validation_error from None
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
            await provider.close(work_directory=work)
            if validation_error is not None:
                from .process_diagnostics import retained_process_evidence

                try:
                    # The listed files in the agent's source, walked once a run (and only when
                    # it fails), masked out of every log the repair model reads. If the walk
                    # itself fails, no log goes to the model.
                    source_masks = await _source_masks(source)
                    validation_error.process_evidence = retained_process_evidence(
                        process_logs, logs_before, redaction_values, masks=source_masks
                    )
                    control_service = (
                        getattr(manifest.runtime, "control_service", None)
                        if manifest is not None else None
                    )
                    if control_service:
                        validation_error.target_process_evidence = retained_process_evidence(
                            process_logs, logs_before, redaction_values,
                            process_name=control_service, masks=source_masks,
                        )
                except _SourceMasksUnavailable:
                    pass  # logged where the walk failed
                except OSError as exc:
                    logger.warning(
                        "process evidence withheld from the repair (%s)",
                        type(exc).__name__,
                        exc_info=True,
                    )


logger = logging.getLogger(__name__)


class _SourceMasksUnavailable(OSError):
    """The source's listed files could not be walked for masks; logged once, where it failed."""


# One walk per validate_and_repair run, failure included: the repair never changes the source.
_SOURCE_MASKS: ContextVar[dict[str, frozenset[str] | None] | None] = ContextVar(
    "alk_source_masks", default=None
)


async def _source_masks(source: Path) -> frozenset[str]:
    """The masks of the source's listed files. Raises _SourceMasksUnavailable if the walk fails,
    and the callers then withhold the evidence."""
    from . import process_diagnostics

    cache = _SOURCE_MASKS.get()
    key = str(Path(source).resolve())
    if cache is not None and key in cache:
        found = cache[key]
        if found is None:
            raise _SourceMasksUnavailable("source secret masks unavailable")
        return found
    try:
        masks = await asyncio.to_thread(process_diagnostics.source_secret_masks, source)
    except Exception as exc:
        logger.warning(
            "the source's listed files could not be masked (%s); the repair gets no "
            "diagnostic evidence",
            type(exc).__name__,
            exc_info=True,
        )
        if cache is not None:
            cache[key] = None
        raise _SourceMasksUnavailable("source secret masks unavailable") from None
    if cache is not None:
        cache[key] = masks
    return masks


async def prove_on_sealed_runtime(
    job,
    source: Path,
    authoring: Path,
    *,
    keys: set[str] | None = None,
    until: float | None = None,
    secrets_path: Path = Path("/run/futureagi/secrets.json"),
    local_runtime: bool = False,
) -> dict[str, live_proof.LiveResult]:
    """Start the pair's sealed bundle and play its cases on it, as certification does, without
    certifying again: no compile, no setup pass, no repair. `{}` when the bundle serves no tool a
    case could be played on; the cases then keep their offline proofs."""
    bundle = authoring / "environment-bundle"
    manifest = bundle_v2.load_bundle_v2(bundle)
    if manifest.runtime.kind is bundle_v2.RuntimeKindV2.COMPOSE or not {
        live_proof.TOOL_PROXY,
        live_proof.MCP_TOOLBOX,
    } & set(manifest.capabilities):
        return {}
    contract = AgentContract.model_validate_json((bundle / "contract.json").read_text(encoding="utf-8"))
    generic = bool(isinstance(getattr(job, "metadata", None), dict) and job.metadata.get("generic_harness_v1") is True)
    with tempfile.TemporaryDirectory(prefix="live-proof-", dir=authoring.parent) as root:
        work = Path(root)
        secrets = _prepare_runtime_work(job, work, secrets_path, local_runtime=local_runtime)
        provider = _runtime_provider(job, secrets, authoring, local_runtime=local_runtime, generic=generic)
        try:
            # Up to `prove_concurrency()` instances in this one sandbox; provisioning admits only what its own CPU and
            # memory limits fit (`admit_parallelism`), and one when the agent holds a fixed port.
            from .scenario_tools import load_scenarios
            from .proofs import proof_key

            cases = sum(1 for one in load_scenarios(authoring) if keys is None or proof_key(one) in keys)
            runtimes = await provider.provision(
                manifest,
                source=source,
                bundle_dir=bundle,
                work_directory=work,
                # Never more instances than cases: each one costs its start-up.
                instances=max(1, min(live_proof.prove_concurrency(), cases)),
            )
            return await live_proof.prove_live(
                authoring=authoring,
                contract=contract,
                runtime=runtimes[0],
                runtimes=runtimes,
                provider=provider,
                factory=hosted_entrypoint.ProcessWorldFactory(work, bundle),
                work=work,
                seed=job.seed or 0,
                keys=keys,
                until=until,
            )
        finally:
            await provider.close(work_directory=work)


# Held after the live proof for a reason the scenario writer can fix (a check, a control, a reference), never a limit of
# the lane it was proved on. After a passing validation these cases go back to the writer this many times, each round
# replayed live, before the build keeps them held.
WRITER_FIXABLE = frozenset(
    {
        "broken_check",
        "not_solvable",
        "vacuous",
        "weak_check",
        "credits_failed_attempt",
        "too_strict",
        "unchecked",
        "no_negative_control",
        "negative_control_passed",
        "control_misses_case_goal",
        # Kept as written when it stands, but handed back first: a model's judgment the writer can answer.
        "policy_conflict",
    }
)
# The held-case rounds' own clock, so their time budget can be read apart from the event loop's, and the wall clock
# a build's deadline is read against.
_held_clock = time.monotonic
_wall_clock = time.time


# Holds that say the case cannot be passed as it is grounded (a record the world cannot serve, a tool the agent lacks):
# only these may be replaced by a new case in the same plan cell. Every other hold is repaired, never swapped.
REPLACEABLE = frozenset({"not_solvable", "not_ready", "tool_not_on_agent"})


def hands_back(entry: Mapping[str, Any]) -> bool:
    """Whether a repair round hands this case back to its writer: held for a reason the writer can fix, and not kept as
    written, except where a model's judgment alone keeps it (a possible rule conflict), which the writer answers
    first."""
    reason = entry.get("blocked_reason")
    judged = reason in {str(code) for code in JUDGED_CODES}
    return reason in WRITER_FIXABLE | REPLACEABLE and (entry.get("kept_as_written") is not True or judged)



def read_held(authoring: Path, *, budget: float = 300) -> tuple[dict[str, str], dict[str, Any] | None]:
    """Prove the suite with this attempt's live plays, as a build judges it (`judge_case_goals`), and return the cases
    held for a reason its writer can fix, in the proof's own words, with the tally: how many cases are runnable
    (proved, or kept as written, `proof_counts`), how many there are, and how many are held for each reason. The tally
    is None when the suite could not be proved."""
    from .prove_suite import prove_suite
    from .proofs import proof_counts

    try:
        with tempfile.TemporaryDirectory(prefix="held-cases-") as root:
            proofs = prove_suite(authoring, Path(root) / "proofs.json", budget=budget, judge_case_goals=True)
    except Exception as error:  # noqa: BLE001 - only adds a chance to repair; the build's proofs decide the rest
        print(f"runtime validation: held cases not read ({type(error).__name__})", flush=True)
        return {}, None
    counts = proof_counts(proofs, list(proofs))
    reasons = Counter(
        str(entry.get("blocked_reason") or "not_proved")
        for entry in proofs.values()
        if entry.get("proved") is not True and entry.get("kept_as_written") is not True
    )
    handed = {key: str(entry.get("blocked_reason")) for key, entry in proofs.items() if hands_back(entry)}
    held = {key: str(proofs[key].get("why") or code) for key, code in handed.items()}
    tally = {
        "runnable": counts["proved"] + counts["kept_as_written"],
        "cases": len(proofs),
        "held": dict(reasons),
        # Each handed-back case's hold, which decides whether it may be replaced (`REPLACEABLE`).
        "held_cases": handed,
    }
    return held, tally


def held_for_the_writer(authoring: Path, *, budget: float = 300) -> dict[str, str]:
    """The cases `read_held` hands back to the writer."""
    return read_held(authoring, budget=budget)[0]


# What each hold means, in plain words, for the build's yield line. Read from the proof's code, never from case text.
HELD_WORDS = {
    "negative_control_passed": "no check fails the wrong behaviour its control plays",
    "control_misses_case_goal": "its wrong behaviour never breaks the goal the case is about",
    "no_negative_control": "it has no wrong behaviour to fail",
    "not_solvable": "its reference solution does not pass on the world",
    "broken_check": "a check fails to run",
    "vacuous": "its checks pass before the agent does anything",
    "weak_check": "a check only tests that a call was made",
    "too_strict": "a check fails a right answer written another way",
    "unchecked": "no check reads what it tests",
    "credits_failed_attempt": "a check counts a failed attempt as done",
    "not_ready": "the world does not hold what it needs",
    "validation_incomplete": "the validation ran out of time before reaching it",
    "tool_not_on_agent": "it calls a tool this agent does not have",
}
YIELD_FILE = "yield.json"


def yield_line(asked: int, tally: dict[str, Any]) -> str:
    """`X of N runnable; held: 3 because ..., 1 because ...`, N being what the build asked for (else the cases)."""
    wanted = asked or int(tally.get("cases") or 0)
    held = sorted((tally.get("held") or {}).items(), key=lambda pair: (-pair[1], pair[0]))
    reasons = ", ".join(f"{count} because {HELD_WORDS.get(code, code.replace('_', ' '))}" for code, count in held)
    return f"{tally.get('runnable', 0)} of {wanted} runnable" + (f"; held: {reasons}" if reasons else "")


def write_yield(authoring: Path, asked: int, tally: dict[str, Any] | None, rounds: list[dict[str, Any]]) -> None:
    """The build's yield, kept beside its repair history: what was asked, what is runnable, what is held and why, and
    each round that handed held cases back."""
    if tally is None:
        return
    body = {"asked": asked, **tally, "line": yield_line(asked, tally), "rounds": rounds}
    path = authoring / "generic-harness" / YIELD_FILE
    from .json_file import write_json_atomic

    # What the plan supports was recorded while the suite was written, and outlives every validation round.
    if supports := _read_yield(authoring).get("supports"):
        body["supports"] = supports
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, body)


def _read_yield(authoring: Path) -> dict[str, Any]:
    try:
        found = json.loads((Path(authoring) / "generic-harness" / YIELD_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def record_capacity(authoring: Path, supports: int, written: int, asked: int, why: str = "") -> None:
    """Record in the yield that the plan holds about `supports` distinct cases of the `asked`, with the plain line
    the build reports and `why` the plan could not be widened; `supports` 0 clears it, once a wider plan holds what
    was asked."""
    from .json_file import write_json_atomic
    from .scenario_tools import capacity_line

    body = _read_yield(authoring)
    if not supports and "supports" not in body:
        return
    if supports:
        body["supports"] = {
            "cases": supports,
            "asked": asked,
            "written": written,
            "line": capacity_line(supports, written, asked),
            "why": why,
        }
    else:
        body.pop("supports", None)
    write_json_atomic(Path(authoring) / "generic-harness" / YIELD_FILE, body)


def supported_count(authoring: Path, asked: int) -> int:
    """What the build has to finish with: what it asked for, or fewer when its plan supports only that many."""
    supports = int((_read_yield(authoring).get("supports") or {}).get("cases") or 0)
    return min(asked, supports) if asked and supports else asked


def held_case_guidance(held: dict[str, str], replaceable: Collection[str] = ()) -> str:
    """What the writer is told about the cases the proof held: each to repair, and those `replaceable` (held for how
    they are grounded) that it may instead replace."""
    replaceable = set(replaceable)
    lines = [
        f"- {key}: {why}" + (" [may be replaced]" if key in replaceable else "") for key, why in sorted(held.items())
    ]
    return (
        "The live proof held these cases, each for the reason given. Rewrite each so it proves: fix its checks, its "
        "negative controls or its reference solution as the reason says, and add or amend a sub-goal with "
        "add_sub_goal where it says so. Keep its name and intent, and never drop a sub-goal it names. Only a case "
        "marked [may be replaced] is held for how it is grounded (a record the world cannot serve, a tool the "
        "agent lacks); you may replace it instead: write a new case for the same plan cell, with the same "
        "coverage and every sub-goal it names, and remove the held one with drop_scenario. Never replace any other "
        "case. Every other case stays as it is, and the number of cases stays the same.\n" + "\n".join(lines)
    )


# Which cases the next live pass re-proves after a held-case round, decided from file digests (`mark_replay`).
REPLAY_FILE = "generic-harness/live-replay.json"
# Files that record what a run did or derive from the cases, never an input a case is proved on.
_NOT_AN_INPUT = frozenset(
    {
        "proofs.json",
        "scenario-proofs.json",
        "runtime-validation.json",
        "coverage.json",
        "written.jsonl",
        "scenarios.json",
        "sub_goals.json",
    }
)
# The harness's own run records under generic-harness/: what a validation wrote, never what a case is proved on. Any
# other file there counts as an input the cases share, so a file nobody listed here makes every case proved again.
_RUN_RECORDS = (
    "process-logs/",
    "phase-observations/",
    "compose-logs/",
    "chat-readiness/",
    "certified-bundle/",
    "runtime-readiness.json",
    "readiness-repair-history.json",
    "repair-history.json",
    "runtime-evidence.json",
    "certification.json",
    "tool-certification.json",
    "action-certification.json",
    "tool-requests.json",
    "tool-routing-observations.json",
    "setup-checks-passed.json",
    "live-proof-lanes.json",
    "live-replay.json",
    YIELD_FILE,
    "source-data-review-",
    "runtime-plan-repair-",
)


def _run_record(relative: str) -> bool:
    """Whether a file under the authoring tree is one of the harness's run records under generic-harness/."""
    inside = relative.removeprefix("generic-harness/")
    return inside != relative and inside.startswith(_RUN_RECORDS)


def _digest_files(paths: list[Path], root: Path) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\n")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def proof_inputs(authoring: Path) -> dict[str, Any]:
    """What a live proof is made from, as digests: each case's own folder by its key, each sub-goal by name, the
    sub-goals each case names, and one digest of everything the cases share (contract, world and seed files,
    attached files, actors, the source model). The bundle is sealed from these files, and run records are left out."""
    from .proofs import proof_key
    from .scenario_tools import load_scenarios

    authoring = Path(authoring)
    cases: dict[str, str] = {}
    uses: dict[str, list[str]] = {}
    for one in load_scenarios(authoring):
        folder = authoring / "scenarios" / one.name
        files = sorted(path for path in folder.rglob("*") if path.is_file()) if folder.is_dir() else []
        cases[proof_key(one)] = _digest_files(files, authoring)
        uses[proof_key(one)] = sorted(str(name) for name in one.sub_goals or ())
    try:
        listed = json.loads((authoring / "sub_goals.json").read_text(encoding="utf-8")).get("sub_goals") or []
    except (OSError, ValueError, AttributeError):
        listed = []
    goals = {
        str(goal.get("name")): hashlib.sha256(json.dumps(goal, sort_keys=True).encode("utf-8")).hexdigest()
        for goal in listed
        if isinstance(goal, dict) and goal.get("name")
    }
    shared = [
        path
        for path in sorted(authoring.rglob("*"))
        if path.is_file()
        and (relative := path.relative_to(authoring).as_posix()) not in _NOT_AN_INPUT
        and not relative.startswith(("scenarios/", "environment-bundle/"))
        and not path.name.startswith(".futureagi-")
        and not _run_record(relative)
    ]
    return {"cases": cases, "uses": uses, "goals": goals, "shared": _digest_files(shared, authoring)}


def cases_to_replay(before: Mapping[str, Any], after: Mapping[str, Any]) -> set[str] | None:
    """The cases a held-case round changed, added or replaced, which alone need proving again; None when an input the
    cases share changed (a shared file, or a sub-goal a case the round left alone names), so every case does."""
    if before.get("shared") != after.get("shared"):
        return None
    goals_before, goals_after = before.get("goals") or {}, after.get("goals") or {}
    moved = {name for name in {*goals_before, *goals_after} if goals_before.get(name) != goals_after.get(name)}
    cases_before, cases_after = before.get("cases") or {}, after.get("cases") or {}
    changed = {key for key, digest in cases_after.items() if cases_before.get(key) != digest}
    uses = after.get("uses") or {}
    if any(set(uses.get(key) or ()) & moved for key in cases_after if key not in changed):
        return None
    return changed


def mark_replay(authoring: Path, before: Mapping[str, Any]) -> None:
    """After a held-case round, say which cases the next live pass proves again (`REPLAY_FILE`); with nothing to
    narrow it to, every case is proved, as before."""
    after = proof_inputs(authoring)
    keys = cases_to_replay(before, after)
    path = Path(authoring) / REPLAY_FILE
    if keys is None:
        path.unlink(missing_ok=True)
        return
    removed = sorted(set(before.get("cases") or {}) - set(after["cases"]))
    from .json_file import write_json_atomic

    write_json_atomic(path, {"keys": sorted(keys), "removed": removed, "shared": after["shared"]})


def _replay_keys(authoring: Path) -> tuple[set[str] | None, set[str]]:
    """The cases this live pass proves again and the ones it drops, from the round before it: (None, ∅) to prove every
    case, as when no round narrowed it or an input the cases share has changed since."""
    path = Path(authoring) / REPLAY_FILE
    try:
        marked = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, set()
    finally:
        path.unlink(missing_ok=True)
    if not isinstance(marked, dict) or marked.get("shared") != proof_inputs(authoring)["shared"]:
        return None, set()
    return {str(key) for key in marked.get("keys") or ()}, {str(key) for key in marked.get("removed") or ()}


async def play_this_attempt(
    *,
    runtimes: list | None = None,
    authoring: Path,
    contract: AgentContract,
    runtime,
    provider,
    factory,
    work: Path,
    seed: int,
) -> dict[str, live_proof.LiveResult]:
    """Play this validation attempt's cases on its live runtime. Only this attempt's plays count: an earlier attempt's
    results are dropped first, since a repair between attempts may have changed the cases they were played on. After a
    held-case round that changed only some cases and nothing they share (`mark_replay`), only those are played again
    and the rest keep their results."""
    keys, removed = _replay_keys(authoring)
    live_proof.forget_live(authoring, None if keys is None else keys | removed)
    if keys is not None:
        print(f"runtime validation: proving again only the {len(keys)} cases the round changed", flush=True)
    live = await live_proof.prove_live(
        authoring=authoring,
        contract=contract,
        runtime=runtime,
        runtimes=runtimes,
        provider=provider,
        factory=factory,
        work=work,
        seed=seed,
        keys=keys,
    )
    if live:
        print(f"runtime validation: {live_proof.live_summary(live)}", flush=True)
    return live


def _invariants_the_ir_still_breaks(world, authoring: Path) -> list[str]:
    """Declared invariants the patched rows still violate, judged without building anything."""
    from .source_data_invariants import ARTIFACT, violations_in_ir

    document = authoring / ARTIFACT
    if not document.is_file():
        return []
    try:
        checks = json.loads(document.read_text(encoding="utf-8")).get("checks") or []
        return violations_in_ir(world, checks)
    except Exception:  # noqa: BLE001 - a check we cannot run is the real environment's to judge
        return []


def _promote_data_store(contract, authoring: Path, candidate_dir: Path) -> int:
    """Copy only ``data_store`` from a candidate reading onto the saved contract, and save it.

    Returns 0 when it was promoted, 1 when the candidate has none to give.
    """
    from .contract import AgentContract, write_contract

    candidate_path = candidate_dir / "contract.json"
    if not candidate_path.is_file():
        return 1
    candidate = AgentContract.model_validate_json(
        candidate_path.read_text(encoding="utf-8")
    )
    if candidate.data_store is None or not candidate.data_store.kind.strip():
        return 1
    payload = contract.model_dump()
    payload["data_store"] = candidate.data_store.model_dump()
    write_contract(AgentContract.model_validate(payload), authoring)
    return 0


async def _rewrite_held_cases(
    held: dict[str, str],
    authoring: Path,
    *,
    repair,
    guidance: str,
    expected_count: int,
    seconds: float = 300,
    replaceable: Collection[str] = (),
) -> bool:
    """Hand the held cases back to the writer once. True when it changed something and kept every case, its name and
    its sub-goals; otherwise the suite is left as it was and the cases stay held, never failing the build."""
    from .scenario_repair import certify_scenario_repair
    from .scenario_tools import load_scenarios, write_scenarios

    print(f"runtime validation: {len(held)} cases held after the live proof, back to the writer", flush=True)
    before = load_scenarios(authoring)
    catalogue_before = load_catalogue(authoring)
    before_tree = _authoring_digest(authoring)
    passing_files = _passing_case_files(held, before, authoring)
    suite_files = _suite_files(authoring)
    if seconds <= 0:
        return False
    try:
        status = await asyncio.wait_for(
            repair("scenarios", held_case_guidance(held, replaceable) + "\n\n" + guidance), timeout=seconds
        )
    except Exception as error:  # noqa: BLE001 - a writer that cannot repair, or runs out of time, leaves them held
        print(f"runtime validation: held cases not rewritten ({type(error).__name__})", flush=True)
        # Whatever a writer stopped part-way left is undone, file for file.
        _put_back_suite(authoring, suite_files)
        return False
    if status:
        return False
    if restored := _keep_shared_checks(held, catalogue_before, authoring):
        print(f"runtime validation: checks other cases rely on put back: {', '.join(restored)}", flush=True)
    if restored := _put_back_files(passing_files):
        print(f"runtime validation: cases that were not held put back: {', '.join(restored)}", flush=True)
    if _authoring_digest(authoring) == before_tree:
        return False
    try:
        certify_scenario_repair(
            before,
            load_scenarios(authoring),
            expected_count=expected_count,
            diagnostic_codes=set(),
            replaceable=set(replaceable),
        )
    except ValueError as rejected:
        # A rewrite of the catalogue alone (a check added to a shared sub-goal) changes no scenario file.
        if not str(rejected).startswith("scenario_repair_no_material_change"):
            write_scenarios(before, authoring)
            print(f"runtime validation: held-case rewrite undone: {rejected}", flush=True)
            return False
    return True


def _keep_shared_checks(held: dict[str, str], catalogue_before, authoring: Path) -> list[str]:
    """Put back each sub-goal a case that was not held names whose check the rewrite changed or removed: rewriting a
    held case may add sub-goals or change those only held cases name, never loosen a check a passing case relies on."""
    from .catalogue import save_catalogue
    from .proofs import proof_key
    from .scenario_tools import load_scenarios, write_scenarios

    scenarios = load_scenarios(authoring)
    catalogue = load_catalogue(authoring)
    shared = {goal for one in scenarios if proof_key(one) not in held for goal in one.sub_goals}
    now = {goal.name: goal for goal in catalogue.sub_goals}
    restored = sorted(
        goal.name
        for goal in catalogue_before.sub_goals
        if goal.name in shared and (goal.name not in now or now[goal.name].check != goal.check)
    )
    if restored:
        previous = {goal.name: goal for goal in catalogue_before.sub_goals if goal.name in restored}
        catalogue.sub_goals = [previous.get(one.name, one) for one in catalogue.sub_goals] + [
            goal for name, goal in previous.items() if name not in now
        ]
        save_catalogue(catalogue, authoring)
        # A scenario folder carries the checks it names: rewrite them from the catalogue as put back.
        write_scenarios(scenarios, authoring, catalogue)
    return restored


def _passing_case_files(held: dict[str, str], scenarios, authoring: Path) -> dict[Path, dict[Path, bytes]]:
    """Every file of each case that was not held, as it is now, by its folder."""
    from .folder import folder_for
    from .proofs import proof_key

    folders = [folder_for(authoring, one.name) for one in scenarios if proof_key(one) not in held]
    return {folder: {path: path.read_bytes() for path in folder.rglob("*") if path.is_file()} for folder in folders}


def _put_back_files(files: dict[Path, dict[Path, bytes]]) -> list[str]:
    """Each folder given back exactly as it was (its reference, fixed values, expected outcome and checks): a rewrite
    of the held cases never changes a case that was not held. The folders it touched, by name."""
    touched = []
    for folder, kept in files.items():
        now = {path for path in folder.rglob("*") if path.is_file()} if folder.is_dir() else set()
        changed = [path for path in kept if path not in now or path.read_bytes() != kept[path]] + sorted(now - set(kept))
        if not changed:
            continue
        for path in now - set(kept):
            path.unlink()
        for path, body in kept.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        touched.append(folder.name)
    return touched


def _suite_paths(authoring: Path) -> set[Path]:
    """The suite as written: every scenario folder's files, the index and the sub-goal catalogue."""
    from .folder import INDEX

    root = Path(authoring)
    paths = {path for path in (root / "scenarios").rglob("*") if path.is_file()} if (root / "scenarios").is_dir() else set()
    return paths | {path for path in (root / INDEX, root / "sub_goals.json") if path.is_file()}


def _suite_files(authoring: Path) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in _suite_paths(authoring)}


def _put_back_suite(authoring: Path, files: dict[Path, bytes]) -> None:
    """The suite exactly as `files` holds it: a file written since is removed, every other one written back."""
    for path in _suite_paths(authoring) - set(files):
        path.unlink()
    for path, body in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)


def _authoring_digest(authoring: Path) -> str:
    """The scenarios and the sub-goal catalogue as written, so a rewrite that changed neither is seen as none."""
    digest = hashlib.sha256()
    for path in sorted([*Path(authoring).glob("scenarios/**/*"), Path(authoring) / "sub_goals.json"]):
        if path.is_file():
            digest.update(str(path.relative_to(authoring)).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


async def validate_and_repair(
    job,
    source: Path,
    authoring: Path,
    *,
    validate=None,
    repair=None,
    controller=None,
    sleeper=asyncio.sleep,
    readiness_only: bool = False,
    deadline_at: float | None = None,
) -> None:
    """Two repairs per phase; environment repairs cannot exhaust setup's budget. `deadline_at` (epoch seconds) is when
    the build is stopped, when known: handing held cases back never runs into it."""
    token = _SOURCE_MASKS.set({})
    try:
        await _validate_and_repair(
            job,
            source,
            authoring,
            validate=validate,
            repair=repair,
            controller=controller,
            sleeper=sleeper,
            readiness_only=readiness_only,
            deadline_at=deadline_at,
        )
    finally:
        _SOURCE_MASKS.reset(token)


async def _validate_and_repair(
    job,
    source: Path,
    authoring: Path,
    *,
    validate,
    repair,
    controller,
    sleeper,
    readiness_only: bool,
    deadline_at: float | None = None,
) -> None:
    from .job import HarnessJob

    base_guidance = (
        "\n".join(job.authoring_guidance()) if isinstance(job, HarnessJob) else ""
    )
    available_secret_aliases = (
        job.available_target_environment_names()
        if isinstance(job, HarnessJob)
        else frozenset()
    )
    if validate is None:
        if readiness_only:

            async def validate(job, source, authoring):
                return await validate_once(job, source, authoring, readiness_only=True)
        else:
            validate = validate_once
    generic = bool(
        job is not None
        and isinstance(getattr(job, "metadata", None), dict)
        and job.metadata.get("generic_harness_v1") is True
    )
    if readiness_only:
        if not generic:
            raise RuntimeValidationError(
                "runtime", "Early readiness requires the generic runtime"
            )
        (authoring / "generic-harness" / "runtime-readiness.json").unlink(missing_ok=True)
    if repair is None:

        async def repair(phase, guidance):
            from .cli import _build, _scenarios

            if phase == "environment":
                artifact_root = authoring / "generic-harness"
                if generic and {item.code for item in _active_repair_diagnostics} == {
                    "generated_storage_declaration_missing"
                }:
                    from .cli import _understand
                    from .contract import AgentContract

                    contract_path = authoring / "contract.json"
                    contract = AgentContract.model_validate_json(
                        contract_path.read_text(encoding="utf-8")
                    )
                    if contract.data_store is not None:
                        return 1
                    # The contract can quote the source, so it is masked like the guidance, and
                    # left out if the masks can't be built.
                    contract_masks = await source_masks_or_none()
                    contract_text = (
                        "(withheld)"
                        if contract_masks is None
                        else mask_source_secrets(
                            contract.model_dump_json(indent=2), contract_masks
                        )
                    )
                    with tempfile.TemporaryDirectory(
                        prefix="alk-storage-repair-"
                    ) as candidate_dir:
                        status = await _understand(
                            argparse.Namespace(
                                kind="repo",
                                name=source.name,
                                path=str(source),
                                out=candidate_dir,
                                interactive=False,
                                job=job,
                                guidance=[
                                    guidance,
                                    (
                                        "Inspect source to recover the missing data_store declaration. "
                                        "Record explicit filesystem, in_memory, none, or database storage "
                                        "only when source supports it. Do not invent a schema or change "
                                        "source behavior. Only data_store will be promoted. Current contract:\n"
                                        + contract_text
                                    ),
                                ],
                            )
                        )
                        if status != 0:
                            return 1
                        return _promote_data_store(
                            contract, authoring, Path(candidate_dir)
                        )
                if generic and _needs_runtime_plan_repair(_active_repair_diagnostics):
                    from .contract import AgentContract
                    from .runtime_repair import request_runtime_plan_patch

                    contract = AgentContract.model_validate_json(
                        (authoring / "contract.json").read_text(encoding="utf-8")
                    )
                    return await request_runtime_plan_patch(
                        source,
                        contract,
                        _active_repair_diagnostics,
                        guidance=guidance,
                        available_secret_aliases=available_secret_aliases,
                        allow_ingress_repair=readiness_only,
                    )
                if generic and all(
                    (artifact_root / name).is_file()
                    for name in ("source-model.json", "world-ir.json")
                ):
                    from .certification import GenericHarnessArtifactStore
                    from .repair_authoring import (
                        RepairPatchNotSubmittedError,
                        request_world_ir_patch,
                    )

                    store = GenericHarnessArtifactStore(artifact_root)
                    # Diagnostics are already included in guidance, but the constrained author
                    # consumes their typed form. The closure is called only by the generic loop,
                    # which replaces this placeholder through `_active_repair_diagnostics`.
                    try:
                        return await request_world_ir_patch(
                            source,
                            store.read_source_model(),
                            store.read_world_ir(),
                            _active_repair_diagnostics,
                            guidance=guidance,
                        )
                    except RepairPatchNotSubmittedError:
                        return 1
                return await _build(
                    argparse.Namespace(
                        name=source.name,
                        path=str(source),
                        out=str(authoring),
                        interactive=False,
                        guidance=[guidance],
                        skip_source_provision=True,
                        external_runtime=(
                            job.source.kind.value == "provider"
                            and getattr(job.agent.mode, "value", None) == "connect_only"
                        ),
                    )
                )
            return await _scenarios(
                argparse.Namespace(
                    name=source.name,
                    out=str(authoring),
                    count=job.scenario_count,
                    interactive=False,
                    guidance=[guidance],
                    job=job,
                )
            )

    # Every text the repair step gets can echo process output (logs and diagnostics alike), so
    # all of it is masked against the contents of the source's listed files on the way in. If the
    # masks can't be built, the repair gets the standing instructions without the evidence.
    from .process_diagnostics import mask_source_secrets

    _unmasked_repair = repair

    async def source_masks_or_none():
        try:
            return await _source_masks(source)
        except _SourceMasksUnavailable:
            return None

    async def repair(phase, guidance, *more, **options):
        masks = await source_masks_or_none()
        if masks is None:
            guidance = "The diagnostic evidence was withheld.\n\n" + base_guidance
        else:
            guidance = mask_source_secrets(guidance, masks)
        return await _unmasked_repair(phase, guidance, *more, **options)

    _active_repair_diagnostics = ()
    if generic:
        from .certification import GenericHarnessArtifactStore
        from .repair_controller import (
            CandidateObservation,
            RepairAction,
            RepairBudgets,
            RepairController,
            RepairOutcome,
            RepairPhase,
            held_case_repair_seconds,
        )

        repair_controller = controller or RepairController(
            RepairBudgets(held_case_seconds=held_case_repair_seconds(int(getattr(job, "scenario_count", 0) or 0)))
        )
        artifacts = GenericHarnessArtifactStore(authoring / "generic-harness")

        def write_history():
            if readiness_only:
                path = authoring / "generic-harness" / "readiness-repair-history.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(repair_controller.history.model_dump_json(indent=2) + "\n")
            else:
                artifacts.write_repair_history(repair_controller.history)

        budgets = repair_controller.budgets
        candidate_repairs = (
            budgets.compiler_candidates
            + budgets.runtime_patches
            + budgets.environment_patches
            + budgets.scenario_patches
        )
        # Every materially changed candidate may first consume its transient retry allowance.
        # Derive the guard from the controller's real budgets so increasing autonomous repair
        # depth cannot make this outer loop terminate before the final candidate is validated.
        decision_bound = (
            1
            + candidate_repairs
            + (candidate_repairs + 1) * budgets.infrastructure_retries_per_candidate
        )
        candidate_hash = _generic_candidate_hash(source, authoring)
        held_rounds = 0
        yield_rounds: list[dict[str, Any]] = []
        asked = int(getattr(job, "scenario_count", 0) or 0)
        held_since: float | None = None
        last_validation = 0.0
        for _attempt in range(decision_bound + budgets.held_case_rounds):
            # A record left by an earlier attempt must never speak for this one.
            failure_record.clear()
            try:
                validation_started = _held_clock()
                count = await validate(job, source, authoring)
                # How long one more validation takes, so a held-case round leaves room for it before the deadline.
                last_validation = _held_clock() - validation_started
            except RuntimeValidationError as exc:
                if readiness_only and exc.phase == "scenarios":
                    raise
                diagnostics = exc.diagnostics or (
                    _fallback_diagnostic(exc, authoring=authoring),
                )
                # The repair history deliberately fingerprints diagnostics without persisting
                # their potentially high-cardinality text. Keep the redacted evidence visible in
                # the guest log as well, otherwise a bounded repair can appear to hang while the
                # actionable cause is available only inside the model session.
                print(
                    f"runtime validation: {exc.phase}: {exc}",
                    flush=True,
                )
                failure_record.record(exc.phase, exc)
                phase = (
                    RepairPhase.SCENARIOS
                    if exc.phase == "scenarios"
                    else RepairPhase.RUNTIME
                    if exc.phase == "runtime"
                    else RepairPhase.ENVIRONMENT
                )
                decision = repair_controller.decide(
                    CandidateObservation(
                        candidate_hash=candidate_hash,
                        phase=phase,
                        diagnostics=diagnostics,
                    )
                )
                write_history()
                if decision.action is RepairAction.REJECT:
                    raise
                if decision.action is RepairAction.RETRY_INFRASTRUCTURE:
                    await sleeper(decision.retry_after_seconds or 0)
                    repair_controller.record_result(
                        decision.sequence,
                        after_candidate_hash=candidate_hash,
                        outcome=RepairOutcome.APPLIED,
                    )
                    write_history()
                    continue
                if decision.action is RepairAction.RECOMPILE:
                    # Compilation is deterministic. Re-evaluate once; an unchanged fingerprint
                    # is rejected by the controller rather than handed to a model as data repair.
                    repair_controller.record_result(
                        decision.sequence,
                        after_candidate_hash=candidate_hash,
                        outcome=RepairOutcome.NO_MATERIAL_CHANGE,
                    )
                    write_history()
                    continue
                if readiness_only and decision.action is RepairAction.PATCH_SCENARIOS:
                    raise
                repair_phase = (
                    "scenarios"
                    if decision.action is RepairAction.PATCH_SCENARIOS
                    else "environment"
                )
                # Masked before anything is cut or handed on: the guidance text and the typed
                # diagnostics both reach the model.
                repair_diagnostics = _mask_diagnostics(
                    diagnostics, await source_masks_or_none()
                )
                guidance = (
                    "Apply one constrained repair using submitted source evidence only. "
                    "Do not modify source, disable constraints, weaken checks, drop scenarios, "
                    "or invent services/tools. Resolve this complete, redacted diagnostic set:\n"
                    + _repair_guidance(repair_diagnostics)
                    + "\n\n"
                    + base_guidance
                )
                if exc.process_evidence:
                    guidance += (
                        "\n<untrusted_current_attempt_process_logs>\n"
                        + exc.process_evidence
                        + "\n</untrusted_current_attempt_process_logs>"
                    )
                if exc.target_process_evidence:
                    guidance += (
                        "\n<untrusted_current_target_process_logs>\n"
                        + exc.target_process_evidence
                        + "\n</untrusted_current_target_process_logs>"
                    )
                _active_repair_diagnostics = repair_diagnostics
                scenarios_before = None
                if repair_phase == "scenarios":
                    from .scenario_tools import load_scenarios

                    scenarios_before = load_scenarios(authoring)
                before_repair_hash = _generic_candidate_hash(source, authoring)
                try:
                    repair_result = await repair(repair_phase, guidance)
                except Exception as repair_error:
                    print(
                        f"runtime validation: repair failed ({type(repair_error).__name__})",
                        flush=True,
                    )
                    repair_controller.record_result(
                        decision.sequence,
                        after_candidate_hash=candidate_hash,
                        outcome=RepairOutcome.FAILED,
                    )
                    write_history()
                    raise exc from None
                if repair_phase == "environment":
                    from .repair_patch import (
                        RepairPatchRejected,
                        WorldIRRepairPatch,
                        apply_world_ir_repair_patch,
                    )
                    from .runtime_repair import (
                        RuntimePlanPatch,
                        apply_runtime_plan_patch,
                    )

                    from .repair_controller import is_chat_setup_investigation

                    if is_chat_setup_investigation(diagnostics) and not isinstance(
                        repair_result, RuntimePlanPatch
                    ):
                        status = 1
                    elif isinstance(repair_result, WorldIRRepairPatch):
                        source_model = artifacts.read_source_model()
                        world_ir = artifacts.read_world_ir()
                        artifacts.write_repair_patch(
                            repair_result, sequence=decision.sequence
                        )
                        try:
                            repaired_world = _stored_in_world(
                                authoring,
                                apply_world_ir_repair_patch(
                                    world_ir,
                                    source_model,
                                    repair_result,
                                    allowed_reason_codes={
                                        item.code for item in diagnostics
                                    },
                                ),
                                source_model,
                            )
                        except RepairPatchRejected as rejected:
                            print(
                                f"runtime validation: repair rejected: {rejected}",
                                flush=True,
                            )
                            status = 1
                        else:
                            artifacts.write_world_ir(repaired_world)
                            still_broken = _invariants_the_ir_still_breaks(
                                repaired_world, authoring
                            )
                            if still_broken:
                                print(
                                    "runtime validation: patch did not close the "
                                    "invariants, repairing again without rebuilding: "
                                    + "; ".join(still_broken)[:400],
                                    flush=True,
                                )
                            status = 0
                    elif isinstance(repair_result, RuntimePlanPatch):
                        apply_runtime_plan_patch(
                            source,
                            authoring / "contract.json",
                            repair_result,
                            allowed_diagnostic_codes={
                                item.code for item in diagnostics
                            },
                            available_secret_aliases=available_secret_aliases,
                            allow_ingress_repair=readiness_only,
                        )
                        patch_path = (
                            authoring
                            / "generic-harness"
                            / f"runtime-plan-repair-{decision.sequence}.json"
                        )
                        patch_path.write_text(
                            repair_result.model_dump_json(indent=2) + "\n",
                            encoding="utf-8",
                        )
                        status = 0
                    else:
                        status = repair_result
                else:
                    from .scenario_repair import certify_scenario_repair
                    from .scenario_tools import load_scenarios, write_scenarios

                    status = repair_result
                    for correction in range(budgets.scenario_patches + 1):
                        if status:
                            break
                        assert scenarios_before is not None
                        scenarios_after = load_scenarios(authoring)
                        try:
                            receipt = certify_scenario_repair(
                                scenarios_before,
                                scenarios_after,
                                expected_count=job.scenario_count,
                                diagnostic_codes={item.code for item in diagnostics},
                            )
                        except Exception as rejected:
                            # Restore the last certified candidate; a rejected patch never becomes
                            # the input to another repair attempt.
                            write_scenarios(scenarios_before, authoring)
                            if correction == budgets.scenario_patches:
                                raise
                            print(
                                f"runtime validation: scenario repair rejected, repairing again: {rejected}",
                                flush=True,
                            )
                            status = await repair(
                                repair_phase,
                                guidance
                                + "\nThe previous repair was rejected and undone: "
                                + f"{rejected}. Repair again within these rules.",
                            )
                            continue
                        artifacts.write_scenario_repair(
                            receipt, sequence=decision.sequence
                        )
                        break
                after_hash = _generic_candidate_hash(source, authoring)
                if status or after_hash == before_repair_hash:
                    after_hash = candidate_hash
                repair_controller.record_result(
                    decision.sequence,
                    after_candidate_hash=after_hash,
                    outcome=(RepairOutcome.FAILED if status else RepairOutcome.APPLIED),
                )
                write_history()
                if status:
                    raise
                candidate_hash = after_hash
            else:
                # A validation that may not repair scenarios (certify-only) never reads or hands back held cases, and
                # every round, with the validation it causes, fits in one time budget from the first round.
                round_started = _held_clock()
                held_since = round_started if held_since is None else held_since
                left = budgets.held_case_seconds - (round_started - held_since)
                if deadline_at is not None:
                    from .prove_suite import DEADLINE_MARGIN_SECONDS

                    # Never past the build's own deadline: one more validation and the sealing after it still fit.
                    left = min(left, deadline_at - _wall_clock() - last_validation - DEADLINE_MARGIN_SECONDS)
                held, tally = (
                    read_held(authoring, budget=left)
                    if not readiness_only
                    and budgets.scenario_patches
                    and held_rounds < budgets.held_case_rounds
                    and left > 0
                    else ({}, None)
                )
                if tally is not None:
                    write_yield(authoring, asked, tally, yield_rounds)
                    print(f"runtime validation: {yield_line(asked, tally)}", flush=True)
                    # The build asked for this many runnable cases and has them: nothing more to hand back but what a
                    # model's judgment alone keeps as written, which its writer answers first.
                    if asked and tally["runnable"] >= asked:
                        judged = {str(code) for code in JUDGED_CODES}
                        held = {
                            key: why
                            for key, why in held.items()
                            if (tally.get("held_cases") or {}).get(key) in judged
                        }
                from .scenario_tools import load_scenarios

                keys_before = {one.scenario_key for one in load_scenarios(authoring)} if held else set()
                codes = (tally or {}).get("held_cases") or {}
                replaceable = {key for key in held if codes.get(key) in REPLACEABLE}
                inputs_before = proof_inputs(authoring) if held else None
                rewritten = bool(held) and await _rewrite_held_cases(
                    held,
                    authoring,
                    repair=repair,
                    guidance=base_guidance,
                    expected_count=job.scenario_count,
                    seconds=left - (_held_clock() - round_started),
                    replaceable=replaceable,
                )
                if held:
                    print(
                        f"runtime validation: held-case round {held_rounds + 1} took "
                        f"{_held_clock() - round_started:.0f}s; {_held_clock() - held_since:.0f}s of "
                        f"{budgets.held_case_seconds:.0f}s spent",
                        flush=True,
                    )
                if held:
                    kept_keys = {one.scenario_key for one in load_scenarios(authoring)}
                    yield_rounds.append(
                        {
                            "round": held_rounds + 1,
                            "held": len(held),
                            "rewritten": rewritten,
                            "replaced": [
                                {"removed": key, "reason": codes.get(key)} for key in sorted(keys_before - kept_keys)
                            ]
                            if rewritten
                            else [],
                            "added": sorted(kept_keys - keys_before) if rewritten else [],
                            "seconds": round(_held_clock() - round_started, 1),
                        }
                    )
                    write_yield(authoring, asked, tally, yield_rounds)
                if rewritten and inputs_before is not None:
                    mark_replay(authoring, inputs_before)
                if rewritten:
                    held_rounds += 1
                    candidate_hash = _generic_candidate_hash(source, authoring)
                    # Validated again: the live proof replays every case, the rewritten ones included.
                    continue
                decision = repair_controller.decide(
                    CandidateObservation(
                        candidate_hash=candidate_hash,
                        phase=RepairPhase.ENVIRONMENT,
                    )
                )
                if decision.action is not RepairAction.CERTIFY:
                    raise RuntimeValidationError(
                        "environment", "generic pipeline refused passing candidate"
                    )
                write_history()
                if readiness_only:
                    return
                evidence_path = (
                    authoring
                    / "generic-harness"
                    / GenericHarnessArtifactStore.RUNTIME_EVIDENCE
                )
                if evidence_path.is_file():
                    _write_generic_certificate(
                        job, authoring, repair_controller.history, source
                    )
                else:
                    # Injected validators in unit/integration consumers may not implement the
                    # runtime-evidence seam. Keep their legacy proof shape without allowing the
                    # production validator to emit a false full certificate.
                    (authoring / "runtime-validation.json").write_text(
                        json.dumps(
                            {
                                "status": "passed",
                                "attempts": len(repair_controller.history.decisions),
                                "setup_ready_scenarios": count,
                                "reference_tools_proven": False,
                                "generic_harness": "v1",
                            },
                            indent=2,
                        )
                        + "\n"
                    )
                return
        raise RuntimeValidationError(
            "environment", "generic repair controller decision bound exhausted"
        )

    repairs = {"environment": 0, "scenarios": 0}
    for attempt in range(5):
        print(f"runtime validation: attempt {attempt + 1}/5", flush=True)
        failure_record.clear()
        try:
            count = await validate(job, source, authoring)
        except RuntimeValidationError as exc:
            print(f"runtime validation: {exc.phase}: {exc}", flush=True)
            failure_record.record(exc.phase, exc)
            if exc.phase not in repairs or repairs[exc.phase] >= 2:
                raise
            repairs[exc.phase] += 1
            masks = await source_masks_or_none()
            detail = (
                "withheld" if masks is None else mask_source_secrets(str(exc), masks)[:4000]
            )
            guidance = (
                "Actual hosted runtime validation failed. Repair the generated environment/data "
                "or scenario setup using the submitted source as authority. Do not modify source, "
                "disable database constraints, weaken checks or ready conditions, drop scenarios, "
                "or replace tools with invented implementations. Preserve scenario count and intent. "
                f"Diagnostic: {detail}" + "\n\n" + base_guidance
            )
            if await repair(exc.phase, guidance):
                raise
        else:
            (authoring / "runtime-validation.json").write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "attempts": attempt + 1,
                        "setup_ready_scenarios": count,
                        "reference_tools_proven": False,
                    },
                    indent=2,
                )
                + "\n"
            )
            return
