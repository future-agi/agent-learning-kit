from __future__ import annotations

import asyncio
import copy
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from xml.sax.saxutils import escape


AGENT_LEARNING_SUITE_KIND = "agent-learning.suite.v1"
AGENT_LEARNING_SUITE_OPTIMIZATION_KIND = "agent-learning.suite-optimization.v1"

_CHILD_COMMANDS = {
    "baseline",
    "compare",
    "promote_to_regression",
    "replay",
    "report",
    "run",
    "suite",
    "eval",
    "eval_artifact",
    "eval_task",
    "redteam",
    "optimize",
    "optimize_eval",
    "optimize_suite",
}


class SuiteError(ValueError):
    """Raised when an Agent Learning suite manifest cannot run."""


@dataclass(frozen=True)
class SuiteRunOptions:
    name: Optional[str] = None
    threshold: Optional[float] = None
    max_candidates: Optional[int] = None
    dry_run: bool = False
    fail_fast: bool = False


@dataclass(frozen=True)
class SuiteOptimizationOptions:
    name: Optional[str] = None
    threshold: Optional[float] = None
    max_candidates: Optional[int] = None
    dry_run: bool = False


def load_suite_file(path: str | Path) -> dict[str, Any]:
    suite_path = Path(path).expanduser().resolve()
    if not suite_path.exists():
        raise SuiteError(f"suite manifest not found: {suite_path}")
    suite = _load_json_or_yaml(suite_path)
    if not isinstance(suite, Mapping):
        raise SuiteError("suite manifest root must be an object")
    return _prepare_suite(dict(suite), base_dir=suite_path.parent)


load_suite = load_suite_file


def required_suite_env(
    suite: Mapping[str, Any],
    *,
    suite_path: str | Path = ".",
) -> list[str]:
    base_dir = _suite_base_dir(suite_path)
    required = set(_as_string_list(suite.get("required_env")))
    for job in _suite_jobs(suite):
        try:
            child = _load_child_source(job, base_dir=base_dir)
        except Exception:
            continue
        if _normalize_command(job.get("command") or job.get("type")) == "suite":
            required.update(
                required_suite_env(
                    child,
                    suite_path=_job_path(job, base_dir=base_dir),
                )
            )
            continue
        required.update(_as_string_list(child.get("required_env")))
    return sorted(required)


def missing_suite_env(
    suite: Mapping[str, Any],
    *,
    suite_path: str | Path = ".",
) -> list[str]:
    return [
        key
        for key in required_suite_env(suite, suite_path=suite_path)
        if not os.environ.get(key)
    ]


def validate_suite_env(
    suite: Mapping[str, Any],
    *,
    suite_path: str | Path = ".",
) -> None:
    missing = missing_suite_env(suite, suite_path=suite_path)
    if missing:
        raise SuiteError(
            "missing required environment variable(s): "
            f"{', '.join(sorted(missing))}"
        )


def build_suite_manifest(
    *,
    name: str,
    jobs: Sequence[Mapping[str, Any]],
    required_env: Sequence[str] = (),
    required_capabilities: Optional[Mapping[str, Sequence[str]]] = None,
    outputs: Optional[Mapping[str, Any]] = None,
    metadata: Optional[Mapping[str, Any]] = None,
    threshold: Optional[float] = None,
    fail_fast: Optional[bool] = None,
) -> dict[str, Any]:
    """Build an Agent Learning suite manifest from SDK data.

    This is the SDK counterpart to writing ``agent-learning.suite.v1`` JSON by
    hand: users can compose run/eval/red-team/optimization jobs in Python and
    execute them through ``run_suite`` or ``run_suite_file``.
    """

    if not name:
        raise ValueError("name is required")
    if not jobs:
        raise ValueError("jobs must contain at least one suite job")
    manifest: dict[str, Any] = {
        "version": AGENT_LEARNING_SUITE_KIND,
        "name": str(name),
        "required_env": _unique_strings(required_env),
        "jobs": [
            _normalize_suite_job(job, index)
            for index, job in enumerate(jobs, start=1)
        ],
    }
    if required_capabilities:
        manifest["required_capabilities"] = {
            str(key): _unique_strings(value)
            for key, value in dict(required_capabilities).items()
            if _unique_strings(value)
        }
    if outputs:
        manifest["outputs"] = copy.deepcopy(dict(outputs))
    if metadata:
        manifest["metadata"] = copy.deepcopy(dict(metadata))
    if threshold is not None:
        manifest["threshold"] = float(threshold)
    if fail_fast is not None:
        manifest["fail_fast"] = bool(fail_fast)
    return manifest


def build_trinity_suite_manifest(
    *,
    name: str,
    run_path: str | Path,
    eval_path: str | Path,
    artifact_eval_path: str | Path,
    artifact_report_path: str | Path,
    redteam_path: str | Path,
    eval_optimization_path: str | Path,
    optimization_path: str | Path,
    artifact_optimization_path: str | Path | None = None,
    artifact_eval_config_path: str | Path | None = None,
    required_env: Sequence[str] = (),
    max_candidates: Optional[int] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Build a run/eval/artifact/red-team/optimization suite.

    The manifest mirrors the promptfoo-style trinity workflow: simulation,
    text eval, saved-artifact eval, direct artifact-report eval, optional
    artifact-evidence optimization, red-team, eval-suite optimization, and
    full manifest optimization in one capability-gated suite.
    """

    suite_name = str(name)
    jobs: list[dict[str, Any]] = [
        {
            "id": "local-simulation",
            "command": "run",
            "path": _suite_path_text(run_path),
            "name": f"{suite_name}-run",
        },
        {
            "id": "promptfoo-style-eval",
            "command": "eval",
            "path": _suite_path_text(eval_path),
            "name": f"{suite_name}-eval",
        },
        {
            "id": "artifact-task-eval",
            "command": "eval",
            "path": _suite_path_text(artifact_eval_path),
            "name": f"{suite_name}-artifact-eval",
        },
        {
            "id": "direct-artifact-report-eval",
            "command": "eval-artifact",
            "path": _suite_path_text(artifact_report_path),
            "name": f"{suite_name}-direct-artifact",
        },
    ]
    if artifact_optimization_path is not None:
        jobs.append(
            {
                "id": "artifact-evidence-optimizer",
                "command": "optimize-eval",
                "path": _suite_path_text(artifact_optimization_path),
                "name": f"{suite_name}-artifact-optimizer",
            }
        )
    jobs.extend(
        [
            {
                "id": "agent-red-team",
                "command": "redteam",
                "path": _suite_path_text(redteam_path),
                "name": f"{suite_name}-redteam",
            },
            {
                "id": "eval-suite-optimizer",
                "command": "optimize-eval",
                "path": _suite_path_text(eval_optimization_path),
                "name": f"{suite_name}-eval-optimizer",
            },
            {
                "id": "agent-optimizer",
                "command": "optimize",
                "path": _suite_path_text(optimization_path),
                "name": f"{suite_name}-optimizer",
            },
        ]
    )
    if artifact_eval_config_path is not None:
        jobs[3]["config"] = _suite_path_text(artifact_eval_config_path)
    if max_candidates is not None:
        for job in jobs:
            if job["command"] in {"optimize", "optimize-eval"}:
                job["max_candidates"] = int(max_candidates)
    return build_suite_manifest(
        name=suite_name,
        required_env=required_env,
        jobs=jobs,
        required_capabilities={
            "commands": [
                "run",
                "eval",
                "eval_artifact",
                "redteam",
                "optimize_eval",
                "optimize",
            ],
            "result_kinds": [
                "agent-learning.run.v1",
                "agent-learning.eval.v1",
                "agent-learning.artifact-evaluation.v1",
                "agent-learning.redteam.v1",
                "agent-learning.eval-optimization.v1",
                "agent-learning.optimization.v1",
            ],
            "metrics": [
                "eval_assertions",
            ],
        },
        metadata={
            "source": "agent_learning.suite.build_trinity_suite_manifest",
            **copy.deepcopy(dict(metadata or {})),
        },
    )


def build_regression_artifact_suite_manifest(
    *,
    name: str,
    baseline_path: str | Path,
    current_path: str | Path,
    finding_path: str | Path,
    replay_manifest_paths: Sequence[str | Path],
    required_env: Sequence[str] = (),
    min_score_delta: float = 0.0,
    max_new_findings: int = 0,
    max_new_error_findings: int = 0,
    min_level: str = "warning",
    max_findings: int = 1,
    metadata: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Build the artifact-regression lifecycle suite from SDK paths.

    This composes the lifecycle users usually script around CI artifacts:
    create a compact baseline, compare current vs baseline, render a report,
    promote a red-team finding into a regression manifest, and replay one or
    more regression manifests.
    """

    replay_paths = [_suite_path_text(path) for path in replay_manifest_paths]
    if not replay_paths:
        raise ValueError("replay_manifest_paths must contain at least one manifest")

    suite_name = str(name)
    jobs = [
        {
            "id": "baseline-current-run",
            "command": "baseline",
            "path": _suite_path_text(current_path),
            "name": f"{suite_name}-baseline",
        },
        {
            "id": "compare-baseline-to-current",
            "command": "compare",
            "path": _suite_path_text(current_path),
            "baseline": _suite_path_text(baseline_path),
            "current": _suite_path_text(current_path),
            "name": f"{suite_name}-compare",
            "min_score_delta": float(min_score_delta),
            "max_new_findings": int(max_new_findings),
            "max_new_error_findings": int(max_new_error_findings),
        },
        {
            "id": "report-current-run",
            "command": "report",
            "path": _suite_path_text(current_path),
            "name": f"{suite_name}-report",
        },
        {
            "id": "promote-redteam-finding",
            "command": "promote_to_regression",
            "path": _suite_path_text(finding_path),
            "name": f"{suite_name}-promoted-regression",
            "min_level": str(min_level),
            "max_findings": int(max_findings),
        },
        {
            "id": "replay-regression-manifest",
            "command": "replay",
            "path": replay_paths[0],
            "manifests": replay_paths,
            "name": f"{suite_name}-replay",
        },
    ]
    return build_suite_manifest(
        name=suite_name,
        required_env=required_env,
        jobs=jobs,
        required_capabilities={
            "commands": [
                "baseline",
                "compare",
                "report",
                "promote_to_regression",
                "replay",
            ],
            "result_kinds": [
                "agent_simulate.baseline.v1",
                "agent_simulate.compare.v1",
                "agent_simulate.report.v1",
                "agent_simulate.regression_promotion.v1",
                "agent_simulate.replay.v1",
            ],
            "metrics": [
                "compare_score_delta",
                "replay_pass_rate",
            ],
        },
        metadata={
            "source": "agent_learning.suite.build_regression_artifact_suite_manifest",
            "task_kind": "regression_artifact_lifecycle",
            **copy.deepcopy(dict(metadata or {})),
        },
    )


def write_suite_file(manifest: Mapping[str, Any], path: str | Path) -> Path:
    """Write a suite manifest as formatted JSON and return the resolved path."""

    suite_path = Path(path).expanduser().resolve()
    suite_path.parent.mkdir(parents=True, exist_ok=True)
    suite_path.write_text(
        json.dumps(dict(manifest), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return suite_path


def run_suite_file(
    path: str | Path,
    *,
    options: Optional[SuiteRunOptions] = None,
    name: Optional[str] = None,
    threshold: Optional[float] = None,
    max_candidates: Optional[int] = None,
    dry_run: Optional[bool] = None,
    fail_fast: Optional[bool] = None,
) -> dict[str, Any]:
    suite_path = Path(path).expanduser().resolve()
    suite = load_suite_file(suite_path)
    return run_suite(
        suite,
        suite_path=suite_path,
        options=_merge_options(
            options,
            name=name,
            threshold=threshold,
            max_candidates=max_candidates,
            dry_run=dry_run,
            fail_fast=fail_fast,
        ),
    )


def run_suite(
    suite: Mapping[str, Any],
    *,
    suite_path: str | Path = ".",
    options: Optional[SuiteRunOptions] = None,
    name: Optional[str] = None,
    threshold: Optional[float] = None,
    max_candidates: Optional[int] = None,
    dry_run: Optional[bool] = None,
    fail_fast: Optional[bool] = None,
) -> dict[str, Any]:
    started = time.time()
    opts = _merge_options(
        options,
        name=name,
        threshold=threshold,
        max_candidates=max_candidates,
        dry_run=dry_run,
        fail_fast=fail_fast,
    )
    suite_path = Path(suite_path).expanduser().resolve()
    base_dir = _suite_base_dir(suite_path)
    runtime_suite = _prepare_suite(copy.deepcopy(dict(suite)), base_dir=base_dir)
    validate_suite_env(runtime_suite, suite_path=suite_path)

    children: list[dict[str, Any]] = []
    for index, job in enumerate(_suite_jobs(runtime_suite), start=1):
        child = _execute_job(
            job,
            index=index,
            base_dir=base_dir,
            suite_options=opts,
        )
        children.append(child)
        if int(child.get("exit_code", 1)) != 0 and opts.fail_fast:
            break

    return _suite_result(
        suite=runtime_suite,
        suite_path=suite_path,
        children=children,
        name=opts.name,
        dry_run=opts.dry_run,
        fail_fast=opts.fail_fast,
        duration_seconds=round(time.time() - started, 4),
    )


def optimize_suite_file(
    path: str | Path,
    *,
    options: Optional[SuiteOptimizationOptions] = None,
    name: Optional[str] = None,
    threshold: Optional[float] = None,
    max_candidates: Optional[int] = None,
    dry_run: Optional[bool] = None,
) -> dict[str, Any]:
    """Load and optimize a full Agent Learning suite."""

    suite_path = Path(path).expanduser().resolve()
    suite = load_suite_file(suite_path)
    return optimize_suite(
        suite,
        suite_path=suite_path,
        options=_merge_optimization_options(
            options,
            name=name,
            threshold=threshold,
            max_candidates=max_candidates,
            dry_run=dry_run,
        ),
    )


def optimize_suite(
    suite: Mapping[str, Any],
    *,
    suite_path: str | Path = ".",
    options: Optional[SuiteOptimizationOptions] = None,
    name: Optional[str] = None,
    threshold: Optional[float] = None,
    max_candidates: Optional[int] = None,
    dry_run: Optional[bool] = None,
) -> dict[str, Any]:
    """Optimize a mixed Agent Learning suite and return a unified artifact."""

    started = time.time()
    opts = _merge_optimization_options(
        options,
        name=name,
        threshold=threshold,
        max_candidates=max_candidates,
        dry_run=dry_run,
    )
    suite_path = Path(suite_path).expanduser().resolve()
    base_dir = _suite_base_dir(suite_path)
    runtime_suite = copy.deepcopy(dict(suite))
    if opts.name:
        runtime_suite["name"] = opts.name
    if opts.threshold is not None:
        runtime_suite.setdefault("optimization", {})["threshold"] = opts.threshold
    if opts.max_candidates is not None:
        runtime_suite.setdefault("optimization", {}).setdefault(
            "optimizer", {}
        )["max_candidates"] = opts.max_candidates

    prepared = _prepare_suite(runtime_suite, base_dir=base_dir)
    validate_suite_env(prepared, suite_path=suite_path)
    cli = _optimization_cli()
    optimization = cli._optimization_config(prepared)
    target_config = cli._target_config(optimization)
    optimizer_config = cli._optimizer_config(optimization)
    if opts.dry_run:
        return {
            "schema_version": "agent-simulate.cli.v1",
            "kind": AGENT_LEARNING_SUITE_OPTIMIZATION_KIND,
            "name": str(prepared.get("name") or suite_path.stem),
            "status": "passed",
            "exit_code": 0,
            "dry_run": True,
            "summary": {
                "job_count": len(_suite_jobs(prepared)),
                "required_env": required_suite_env(prepared, suite_path=suite_path),
                "search_path_count": len(target_config.get("search_space", {})),
                "max_candidates": optimizer_config.get("max_candidates"),
            },
            "duration_seconds": round(time.time() - started, 4),
        }

    try:
        from fi.opt import problem_from_agent_learning_suite
    except Exception as exc:  # pragma: no cover - optional dependency clarity
        raise SuiteError("agent-opt is required for suite optimization.") from exc

    problem = problem_from_agent_learning_suite(
        prepared,
        suite_path=suite_path,
        name=str(prepared.get("name") or suite_path.stem),
    )
    optimization_result = problem.optimize()
    payload = cli._optimization_result(
        manifest=prepared,
        optimization_result=optimization_result,
        threshold=float(optimization.get("threshold", 1.0)),
        duration_seconds=round(time.time() - started, 4),
    )
    payload["kind"] = AGENT_LEARNING_SUITE_OPTIMIZATION_KIND
    payload["suite"] = _suite_descriptor(prepared)
    payload["optimization"]["source"] = "agent_learning_suite"
    if "manifest_optimization" in payload["optimization"]:
        artifact = copy.deepcopy(payload["optimization"]["manifest_optimization"])
        artifact["kind"] = "agent_learning_suite_optimization"
        artifact["source"] = "agent_learning_suite"
        payload["optimization"]["suite_optimization"] = artifact
    payload["summary"]["job_count"] = len(_suite_jobs(prepared))
    payload["summary"]["child_command_count"] = _suite_job_command_counts(prepared)
    return payload


def render_junit(result: Mapping[str, Any]) -> str:
    name = escape(str(result.get("name") or "agent-learning-suite"))
    children = list(result.get("children") or result.get("jobs") or [])
    finding_failures = [
        finding
        for finding in list(result.get("findings") or [])
        if str(_as_mapping(finding).get("type")) == "suite_required_capability_missing"
    ]
    failures = (
        sum(1 for child in children if int(child.get("exit_code", 1)) != 0)
        + len(finding_failures)
    )
    lines = [
        (
            f'<testsuite name="{name}" tests="{len(children) + len(finding_failures)}" '
            f'failures="{failures}" errors="0">'
        )
    ]
    for child in children:
        child_name = escape(str(child.get("id") or child.get("name") or "job"))
        class_name = escape(str(child.get("command") or "suite"))
        duration = float(child.get("duration_seconds") or 0.0)
        lines.append(
            f'  <testcase classname="{class_name}" name="{child_name}" '
            f'time="{duration:.4f}">'
        )
        if int(child.get("exit_code", 1)) != 0:
            message = escape(str(child.get("error") or child.get("status") or "failed"))
            lines.append(f'    <failure message="{message}">{message}</failure>')
        lines.append("  </testcase>")
    for index, finding in enumerate(finding_failures, start=1):
        item = _as_mapping(finding)
        finding_name = escape(str(item.get("type") or f"suite_finding_{index}"))
        message = escape(str(item.get("reason") or finding_name))
        lines.append(f'  <testcase classname="suite" name="{finding_name}" time="0.0000">')
        lines.append(f'    <failure message="{message}">{message}</failure>')
        lines.append("  </testcase>")
    lines.append("</testsuite>")
    return "\n".join(lines)


def render_sarif(
    result: Mapping[str, Any],
    *,
    manifest_path: str | Path = ".",
) -> str:
    suite_path = Path(manifest_path).expanduser().resolve()
    findings = _suite_sarif_findings(result)
    sarif_results = []
    for finding in findings:
        rule_id = str(finding.get("type") or finding.get("rule_id") or "suite_finding")
        level = str(finding.get("level") or finding.get("severity") or "error").lower()
        if level not in {"none", "note", "warning", "error"}:
            level = "warning"
        location_path = str(finding.get("path") or suite_path)
        sarif_results.append(
            {
                "ruleId": rule_id,
                "level": level,
                "message": {"text": str(finding.get("reason") or rule_id)},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": location_path},
                        }
                    }
                ],
            }
        )
    payload = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "agent-learning-suite",
                        "informationUri": "https://futureagi.com",
                        "rules": [],
                    }
                },
                "results": sarif_results,
            }
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def render_markdown(
    result: Mapping[str, Any],
    *,
    source_path: str | Path = ".",
) -> str:
    summary = dict(result.get("summary") or {})
    lines = [
        f"# {result.get('name') or 'agent-learning-suite'}",
        "",
        f"- Source: `{Path(source_path)}`",
        f"- Status: `{result.get('status')}`",
        f"- Jobs: {summary.get('passed_count', 0)}/{summary.get('job_count', 0)} passed",
        f"- Score: {summary.get('score', 0.0)}",
        "",
        "| Job | Command | Status | Exit |",
        "| --- | --- | --- | --- |",
    ]
    for child in list(result.get("children") or result.get("jobs") or []):
        lines.append(
            "| "
            f"{_md_cell(child.get('id') or child.get('name') or '')} | "
            f"{_md_cell(child.get('command') or '')} | "
            f"{_md_cell(child.get('status') or '')} | "
            f"{int(child.get('exit_code', 1))} |"
        )
    return "\n".join(lines) + "\n"


def _prepare_suite(suite: dict[str, Any], *, base_dir: Path) -> dict[str, Any]:
    jobs = _as_list(suite.get("jobs") or suite.get("runs") or suite.get("steps"))
    if not jobs:
        raise SuiteError("suite manifest requires at least one job")
    prepared_jobs = []
    for index, job in enumerate(jobs, start=1):
        if not isinstance(job, Mapping):
            raise SuiteError(f"suite job[{index}] must be an object")
        prepared = dict(job)
        prepared["command"] = _normalize_command(
            prepared.get("command") or prepared.get("type") or prepared.get("kind")
        )
        prepared.setdefault("id", f"{prepared['command']}-{index}")
        _job_path(prepared, base_dir=base_dir)
        prepared_jobs.append(prepared)
    suite["jobs"] = prepared_jobs
    suite.setdefault("version", AGENT_LEARNING_SUITE_KIND)
    suite.setdefault("name", "agent-learning-suite")
    return suite


def _execute_job(
    job: Mapping[str, Any],
    *,
    index: int,
    base_dir: Path,
    suite_options: SuiteRunOptions,
) -> dict[str, Any]:
    started = time.time()
    command = _normalize_command(job.get("command") or job.get("type"))
    path = _job_path(job, base_dir=base_dir)
    job_id = str(job.get("id") or f"{command}-{index}")
    try:
        payload = _execute_child_payload(
            command,
            path=path,
            base_dir=base_dir,
            job=job,
            suite_options=suite_options,
        )
        payload = copy.deepcopy(dict(payload))
        outputs_written = _write_child_outputs(
            payload,
            command=command,
            job=job,
            path=path,
        )
        payload["outputs_written"] = outputs_written
        return {
            "id": job_id,
            "command": command,
            "path": str(path),
            "kind": payload.get("kind"),
            "name": payload.get("name"),
            "status": str(payload.get("status") or "unknown"),
            "exit_code": int(payload.get("exit_code", 1)),
            "summary": copy.deepcopy(dict(payload.get("summary") or {})),
            "findings": copy.deepcopy(list(payload.get("findings") or [])),
            "outputs_written": outputs_written,
            "duration_seconds": round(time.time() - started, 4),
            "result": payload,
        }
    except Exception as exc:
        return {
            "id": job_id,
            "command": command,
            "path": str(path),
            "kind": None,
            "name": job.get("name"),
            "status": "failed",
            "exit_code": 1,
            "summary": {},
            "findings": [
                {
                    "type": "suite_child_failed",
                    "level": "error",
                    "reason": str(exc),
                    "job": job_id,
                    "command": command,
                    "path": str(path),
                }
            ],
            "outputs_written": [],
            "duration_seconds": round(time.time() - started, 4),
            "error": str(exc),
        }


def _execute_child_payload(
    command: str,
    *,
    path: Path,
    base_dir: Path,
    job: Mapping[str, Any],
    suite_options: SuiteRunOptions,
) -> dict[str, Any]:
    if command == "run":
        from agent_learning import simulate
        from agent_learning.cli import AGENT_LEARNING_RUN_KIND

        payload = _run_async(
            simulate.run_manifest_file(
                path,
                name=_job_name(job),
                threshold=_job_threshold(job, suite_options),
                no_eval=bool(job.get("no_eval", job.get("no-eval", False))),
                dry_run=_job_dry_run(job, suite_options),
            )
        )
        payload["kind"] = AGENT_LEARNING_RUN_KIND
        return payload
    if command == "suite":
        payload = run_suite_file(
            path,
            options=SuiteRunOptions(
                name=_job_name(job),
                threshold=_job_threshold(job, suite_options),
                max_candidates=_job_max_candidates(job, suite_options),
                dry_run=_job_dry_run(job, suite_options),
                fail_fast=bool(
                    suite_options.fail_fast
                    or job.get("fail_fast")
                    or job.get("fail-fast")
                ),
            ),
        )
        payload["kind"] = AGENT_LEARNING_SUITE_KIND
        return payload
    if command == "eval":
        from agent_learning import evals
        from agent_learning.cli import AGENT_LEARNING_EVAL_KIND

        payload = evals.run_eval_suite_file(
            path,
            name=_job_name(job),
            threshold=_job_threshold(job, suite_options),
            dry_run=_job_dry_run(job, suite_options),
        )
        payload["kind"] = AGENT_LEARNING_EVAL_KIND
        return payload
    if command == "eval_artifact":
        from agent_learning import evals
        from agent_learning.cli import AGENT_LEARNING_ARTIFACT_EVAL_KIND

        config_path = _job_optional_path(
            job,
            base_dir=base_dir,
            keys=("config", "eval_config", "agent_report_config"),
        )
        config = evals.load_artifact_file(config_path) if config_path else None
        payload = evals.evaluate_artifact_file(
            path,
            config=config,
            name=_job_name(job),
            threshold=float(_job_threshold(job, suite_options) or 0.7),
        )
        payload["kind"] = AGENT_LEARNING_ARTIFACT_EVAL_KIND
        return payload
    if command == "eval_task":
        from agent_learning import evals
        from agent_learning.cli import AGENT_LEARNING_ARTIFACT_EVAL_KIND

        config_path = _job_optional_path(
            job,
            base_dir=base_dir,
            keys=("config", "eval_config", "agent_report_config"),
        )
        config = evals.load_artifact_file(config_path) if config_path else None
        payload = evals.evaluate_task_evidence_file(
            path,
            config=config,
            name=_job_name(job),
            threshold=float(_job_threshold(job, suite_options) or 0.7),
        )
        payload["kind"] = AGENT_LEARNING_ARTIFACT_EVAL_KIND
        return payload
    if command == "redteam":
        from agent_learning import redteam

        payload = _run_async(
            redteam.redteam_manifest_file(
                path,
                name=_job_name(job),
                threshold=_job_threshold(job, suite_options),
                dry_run=_job_dry_run(job, suite_options),
            )
        )
        return payload
    if command == "optimize":
        from agent_learning import optimize
        from agent_learning.cli import AGENT_LEARNING_OPTIMIZATION_KIND

        payload = optimize.optimize_manifest_file(
            path,
            name=_job_name(job),
            threshold=_job_threshold(job, suite_options),
            max_candidates=_job_max_candidates(job, suite_options),
            dry_run=_job_dry_run(job, suite_options),
        )
        payload["kind"] = AGENT_LEARNING_OPTIMIZATION_KIND
        return payload
    if command == "optimize_eval":
        from agent_learning import optimize
        from agent_learning.cli import AGENT_LEARNING_EVAL_OPTIMIZATION_KIND

        payload = optimize.optimize_eval_suite_file(
            path,
            name=_job_name(job),
            threshold=_job_threshold(job, suite_options),
            max_candidates=_job_max_candidates(job, suite_options),
            dry_run=_job_dry_run(job, suite_options),
        )
        payload["kind"] = AGENT_LEARNING_EVAL_OPTIMIZATION_KIND
        return payload
    if command == "optimize_suite":
        from agent_learning import optimize
        from agent_learning.cli import AGENT_LEARNING_SUITE_OPTIMIZATION_KIND

        payload = optimize.optimize_suite_file(
            path,
            name=_job_name(job),
            threshold=_job_threshold(job, suite_options),
            max_candidates=_job_max_candidates(job, suite_options),
            dry_run=_job_dry_run(job, suite_options),
        )
        payload["kind"] = AGENT_LEARNING_SUITE_OPTIMIZATION_KIND
        return payload
    if command == "baseline":
        from agent_learning import simulate

        return simulate.create_baseline_file(
            path,
            name=_job_name(job),
        )
    if command == "compare":
        from agent_learning import simulate

        return simulate.compare_result_files(
            _job_compare_baseline_path(job, base_dir=base_dir),
            path,
            min_score_delta=_job_float(job, "min_score_delta", "min-score-delta", default=0.0),
            max_new_findings=_job_int(job, "max_new_findings", "max-new-findings", default=0),
            max_new_error_findings=_job_int(
                job,
                "max_new_error_findings",
                "max-new-error-findings",
                default=0,
            ),
            min_metric_delta=_job_optional_float(
                job,
                "min_metric_delta",
                "min-metric-delta",
            ),
            name=_job_name(job),
        )
    if command == "report":
        from agent_learning import simulate

        return simulate.render_report_file(
            path,
            name=_job_name(job),
        )
    if command == "promote_to_regression":
        from agent_learning import simulate

        return simulate.promote_to_regression_file(
            path,
            name=_job_name(job),
            min_level=str(job.get("min_level") or job.get("min-level") or "warning"),
            max_findings=_job_int(job, "max_findings", "max-findings", default=25),
            required_env=_as_string_list(job.get("required_env")),
        )
    if command == "replay":
        from agent_learning import simulate

        return simulate.replay_manifests(
            _job_replay_manifest_paths(job, base_dir=base_dir),
            name=_job_name(job),
            dry_run=_job_dry_run(job, suite_options),
            fail_fast=bool(suite_options.fail_fast or job.get("fail_fast") or job.get("fail-fast")),
        )
    raise SuiteError(f"unsupported suite job command: {command}")


def _write_child_outputs(
    payload: Mapping[str, Any],
    *,
    command: str,
    job: Mapping[str, Any],
    path: Path,
) -> list[str]:
    output_paths = _job_output_paths(job, path.parent)
    if not any(output_paths.values()):
        return []
    render_junit_fn, render_sarif_fn, render_markdown_fn = _child_renderers(command)
    written: list[str] = []
    for output_path in output_paths["json"]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        written.append(str(output_path))
    for output_path in output_paths["junit"]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(render_junit_fn(payload), encoding="utf-8")
        written.append(str(output_path))
    for output_path in output_paths["sarif"]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            render_sarif_fn(payload, manifest_path=path),
            encoding="utf-8",
        )
        written.append(str(output_path))
    for output_path in output_paths["markdown"]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            render_markdown_fn(payload, source_path=path),
            encoding="utf-8",
        )
        written.append(str(output_path))
    return written


def _child_renderers(command: str) -> tuple[Any, Any, Any]:
    if command == "suite":
        return render_junit, render_sarif, render_markdown
    if command == "redteam":
        from agent_learning import redteam

        return redteam.render_junit, redteam.render_sarif, redteam.render_markdown
    from agent_learning import simulate

    return simulate.render_junit, simulate.render_sarif, simulate.render_markdown


def _suite_result(
    *,
    suite: Mapping[str, Any],
    suite_path: Path,
    children: Sequence[Mapping[str, Any]],
    name: Optional[str],
    dry_run: bool,
    fail_fast: bool,
    duration_seconds: float,
) -> dict[str, Any]:
    job_count = len(_suite_jobs(suite))
    passed = [child for child in children if int(child.get("exit_code", 1)) == 0]
    failed = [child for child in children if int(child.get("exit_code", 1)) != 0]
    score = round(len(passed) / job_count, 4) if job_count else 0.0
    command_counts: dict[str, int] = {}
    for child in children:
        command = str(child.get("command") or "unknown")
        command_counts[command] = command_counts.get(command, 0) + 1
    capabilities = _suite_capability_summary(children)
    required_capabilities = _suite_required_capabilities(suite)
    missing_capabilities = _missing_required_capabilities(
        required_capabilities,
        capabilities,
    )
    capability_findings = _suite_capability_findings(missing_capabilities)
    suite_findings = [*capability_findings, *_suite_findings(children)]
    suite_passed = (
        len(failed) == 0
        and len(children) == job_count
        and not capability_findings
    )
    return {
        "kind": AGENT_LEARNING_SUITE_KIND,
        "version": AGENT_LEARNING_SUITE_KIND,
        "name": str(name or suite.get("name") or suite_path.stem),
        "status": "passed" if suite_passed else "failed",
        "exit_code": 0 if suite_passed else 1,
        "dry_run": dry_run,
        "fail_fast": fail_fast,
        "summary": {
            "job_count": job_count,
            "executed_count": len(children),
            "passed_count": len(passed),
            "failed_count": len(failed),
            "skipped_count": max(job_count - len(children), 0),
            "score": score,
            "commands": command_counts,
            "capabilities": capabilities,
            "required_capabilities": required_capabilities,
            "missing_required_capabilities": missing_capabilities,
            "capability_gate_passed": not capability_findings,
        },
        "children": list(children),
        "jobs": list(children),
        "findings": suite_findings,
        "duration_seconds": duration_seconds,
    }


def _suite_descriptor(suite: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "version": suite.get("version") or AGENT_LEARNING_SUITE_KIND,
        "name": suite.get("name"),
        "job_count": len(_suite_jobs(suite)),
        "jobs": [
            {
                "id": job.get("id"),
                "command": job.get("command"),
                "path": job.get("path"),
            }
            for job in _suite_jobs(suite)
        ],
        "required_capabilities": _suite_required_capabilities(suite),
    }


def _suite_job_command_counts(suite: Mapping[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for job in _suite_jobs(suite):
        command = str(job.get("command") or "unknown")
        counts[command] = counts.get(command, 0) + 1
    return counts


def _suite_capability_summary(children: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    caps: dict[str, set[str]] = {
        "channels": set(),
        "child_ids": set(),
        "commands": set(),
        "environment_state_keys": set(),
        "environment_types": set(),
        "frameworks": set(),
        "metrics": set(),
        "modalities": set(),
        "providers": set(),
        "result_kinds": set(),
        "search_paths": set(),
    }
    for child in children:
        _add_capability(caps, "child_ids", child.get("id"))
        _add_capability(caps, "commands", child.get("command"))
        _add_capability(caps, "result_kinds", child.get("kind"))
        result = _as_mapping(child.get("result"))
        _collect_result_capabilities(result, caps)
    return {key: sorted(values) for key, values in caps.items()}


def _suite_required_capabilities(suite: Mapping[str, Any]) -> dict[str, list[str]]:
    raw = (
        suite.get("required_capabilities")
        or suite.get("capability_requirements")
        or suite.get("capabilities_required")
        or {}
    )
    if not isinstance(raw, Mapping):
        return {}
    requirements: dict[str, list[str]] = {}
    for key, values in raw.items():
        normalized_key = _suite_key(key)
        if not normalized_key:
            continue
        normalized_values = sorted(
            {
                _suite_key(value)
                for value in _as_list(values)
                if _suite_key(value)
            }
        )
        if normalized_values:
            requirements[normalized_key] = normalized_values
    return requirements


def _missing_required_capabilities(
    required: Mapping[str, Sequence[str]],
    observed: Mapping[str, Sequence[str]],
) -> dict[str, list[str]]:
    missing: dict[str, list[str]] = {}
    for key, required_values in required.items():
        observed_values = {_suite_key(value) for value in _as_list(observed.get(key))}
        missing_values = sorted(
            {
                _suite_key(value)
                for value in _as_list(required_values)
                if _suite_key(value) and _suite_key(value) not in observed_values
            }
        )
        if missing_values:
            missing[key] = missing_values
    return missing


def _suite_capability_findings(
    missing_capabilities: Mapping[str, Sequence[str]],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for capability, missing_values in sorted(missing_capabilities.items()):
        values = sorted(_suite_key(value) for value in missing_values if _suite_key(value))
        if not values:
            continue
        findings.append(
            {
                "type": "suite_required_capability_missing",
                "level": "error",
                "reason": (
                    f"Missing required suite capability `{capability}`: "
                    f"{', '.join(values)}."
                ),
                "capability": capability,
                "missing": values,
            }
        )
    return findings


def _collect_result_capabilities(payload: Mapping[str, Any], caps: dict[str, set[str]]) -> None:
    for child in _as_list(payload.get("children") or payload.get("jobs")):
        child_item = _as_mapping(child)
        if not child_item:
            continue
        _add_capability(caps, "child_ids", child_item.get("id"))
        _add_capability(caps, "commands", child_item.get("command"))
        _add_capability(caps, "result_kinds", child_item.get("kind"))
        _collect_result_capabilities(_as_mapping(child_item.get("result")), caps)
    _collect_summary_capabilities(_as_mapping(payload.get("summary")), caps)
    optimization = _as_mapping(payload.get("optimization"))
    best_config = _as_mapping(optimization.get("best_config"))
    simulation = _as_mapping(best_config.get("simulation"))
    for environment in _as_list(simulation.get("environments")):
        env = _as_mapping(environment)
        _add_capability(caps, "environment_types", env.get("type"))
    for history in _as_list(optimization.get("history")):
        item = _as_mapping(history)
        _add_capabilities(caps, "metrics", _as_mapping(item.get("metrics")).keys())
        _collect_report_capabilities(_as_mapping(item.get("report")), caps)
    _collect_report_capabilities(_as_mapping(payload.get("report")), caps)
    _collect_report_capabilities(_as_mapping(_as_mapping(payload.get("evaluation")).get("report")), caps)
    _collect_payload_capabilities(payload, caps)


def _collect_report_capabilities(report: Mapping[str, Any], caps: dict[str, set[str]]) -> None:
    for result in _as_list(report.get("results")):
        case = _as_mapping(result)
        metadata = _as_mapping(case.get("metadata"))
        environment_state = _as_mapping(metadata.get("environment_state"))
        _add_capabilities(caps, "environment_state_keys", environment_state.keys())
        for state in environment_state.values():
            _collect_payload_capabilities(state, caps)
        _collect_payload_capabilities(_as_mapping(case.get("evaluation")), caps)


def _collect_payload_capabilities(
    value: Any,
    caps: dict[str, set[str]],
    *,
    depth: int = 0,
) -> None:
    if depth > 12:
        return
    if isinstance(value, Mapping):
        item = _as_mapping(value)
        _collect_summary_capabilities(_as_mapping(item.get("summary")), caps)
        _add_capability(caps, "frameworks", item.get("framework"))
        _add_capability(caps, "providers", item.get("provider"))
        _add_capability(caps, "providers", item.get("provider_id"))
        _add_capability(caps, "providers", item.get("provider_type"))
        _add_capability(caps, "channels", item.get("channel"))
        _add_capability(caps, "channels", item.get("modality"))
        _add_capability(caps, "modalities", item.get("modality"))
        _add_capabilities(caps, "metrics", _as_mapping(item.get("metrics")).keys())
        if _suite_key(item.get("type")) in _KNOWN_ENVIRONMENT_TYPES:
            _add_capability(caps, "environment_types", item.get("type"))
        for metric in _as_list(item.get("metrics")):
            metric_item = _as_mapping(metric)
            _add_capability(caps, "metrics", metric_item.get("name"))
        for child in item.values():
            _collect_payload_capabilities(child, caps, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            _collect_payload_capabilities(child, caps, depth=depth + 1)


def _collect_summary_capabilities(summary: Mapping[str, Any], caps: dict[str, set[str]]) -> None:
    if not summary:
        return
    _add_capabilities(caps, "search_paths", summary.get("search_paths"))
    _add_capabilities(caps, "providers", summary.get("observed_providers"))
    _add_capabilities(caps, "providers", summary.get("required_providers"))
    _add_capabilities(caps, "channels", summary.get("observed_channels"))
    _add_capabilities(caps, "channels", summary.get("required_channels"))
    _add_capabilities(caps, "frameworks", summary.get("trace_frameworks"))
    _add_capabilities(caps, "frameworks", summary.get("observed_frameworks"))
    _add_capabilities(caps, "frameworks", summary.get("required_trace_frameworks"))
    _add_capabilities(caps, "frameworks", summary.get("frameworks"))
    _add_capabilities(caps, "environment_state_keys", summary.get("environment_state_keys"))
    _add_capabilities(caps, "metrics", summary.get("observed_metrics"))
    _add_capabilities(caps, "metrics", summary.get("required_metrics"))
    _add_capabilities(caps, "metrics", summary.get("eval_metrics"))
    _add_capabilities(caps, "metrics", _as_mapping(summary.get("metric_averages")).keys())
    provider_channels = _as_mapping(summary.get("provider_channels"))
    _add_capabilities(caps, "providers", provider_channels.keys())
    for channels in provider_channels.values():
        _add_capabilities(caps, "channels", channels)


def _add_capabilities(
    caps: dict[str, set[str]],
    key: str,
    values: Any,
) -> None:
    if isinstance(values, Mapping):
        values = values.keys()
    elif values is None:
        return
    elif isinstance(values, (str, bytes)):
        values = [values]
    else:
        try:
            values = list(values)
        except TypeError:
            values = [values]
    for value in values:
        _add_capability(caps, key, value)


def _add_capability(caps: dict[str, set[str]], key: str, value: Any) -> None:
    normalized = _suite_key(value)
    if normalized:
        caps[key].add(normalized)


def _suite_findings(children: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for child in children:
        exit_code = int(child.get("exit_code", 1))
        if exit_code != 0:
            findings.append(
                {
                    "type": "suite_child_failed",
                    "level": "error",
                    "reason": (
                        f"{child.get('command')} {child.get('id')} exited "
                        f"{exit_code}."
                    ),
                    "job": child.get("id"),
                    "command": child.get("command"),
                    "path": child.get("path"),
                }
            )
        for finding in list(child.get("findings") or []):
            if isinstance(finding, Mapping):
                copied = copy.deepcopy(dict(finding))
                copied.setdefault("job", child.get("id"))
                copied.setdefault("command", child.get("command"))
                copied.setdefault("path", child.get("path"))
                findings.append(copied)
    return findings


def _suite_sarif_findings(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    findings = []
    for finding in list(result.get("findings") or []):
        if isinstance(finding, Mapping):
            findings.append(copy.deepcopy(dict(finding)))
    return findings


def _load_child_source(job: Mapping[str, Any], *, base_dir: Path) -> dict[str, Any]:
    path = _job_path(job, base_dir=base_dir)
    loaded = _load_json_or_yaml(path)
    if not isinstance(loaded, Mapping):
        raise SuiteError(f"suite job source must be an object: {path}")
    return dict(loaded)


def _suite_jobs(suite: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [dict(job) for job in _as_list(suite.get("jobs"))]


def _job_path(job: Mapping[str, Any], *, base_dir: Path) -> Path:
    raw = (
        job.get("path")
        or job.get("manifest")
        or job.get("suite")
        or job.get("file")
        or job.get("current")
        or job.get("result")
    )
    if not raw:
        replay_paths = _as_list(job.get("manifests") or job.get("paths"))
        if replay_paths:
            raw = replay_paths[0]
    if not raw:
        raise SuiteError(f"suite job {job.get('id') or ''} requires path")
    return _resolve_path(str(raw), base_dir)


def _job_compare_baseline_path(job: Mapping[str, Any], *, base_dir: Path) -> Path:
    raw = job.get("baseline") or job.get("baseline_path") or job.get("baseline-path")
    if not raw:
        raise SuiteError(f"suite compare job {job.get('id') or ''} requires baseline")
    return _resolve_path(str(raw), base_dir)


def _job_replay_manifest_paths(job: Mapping[str, Any], *, base_dir: Path) -> list[Path]:
    raw_values = _as_list(
        job.get("manifests")
        or job.get("paths")
        or job.get("path")
        or job.get("manifest")
    )
    paths = [_resolve_path(str(value), base_dir) for value in raw_values if str(value)]
    if not paths:
        raise SuiteError(f"suite replay job {job.get('id') or ''} requires manifests")
    return paths


def _job_optional_path(
    job: Mapping[str, Any],
    *,
    base_dir: Path,
    keys: Sequence[str],
) -> Optional[Path]:
    for key in keys:
        raw = job.get(key)
        if raw not in (None, ""):
            return _resolve_path(str(raw), base_dir)
    return None


def _job_output_paths(job: Mapping[str, Any], base_dir: Path) -> dict[str, list[Path]]:
    outputs: dict[str, list[Path]] = {
        "json": [],
        "junit": [],
        "sarif": [],
        "markdown": [],
    }
    suite_outputs = dict(job.get("outputs") or {})
    raw_json = [*_as_list(job.get("output")), *_as_list(suite_outputs.get("json"))]
    raw_junit = _as_list(suite_outputs.get("junit"))
    raw_sarif = _as_list(suite_outputs.get("sarif"))
    raw_markdown = [
        *_as_list(suite_outputs.get("markdown")),
        *_as_list(suite_outputs.get("md")),
    ]
    for value in raw_json:
        path = _resolve_path(str(value), base_dir)
        if path.name.endswith((".junit.xml", ".xml")):
            outputs["junit"].append(path)
        elif path.name.endswith((".sarif", ".sarif.json")):
            outputs["sarif"].append(path)
        else:
            outputs["json"].append(path)
    outputs["junit"].extend(_resolve_path(str(value), base_dir) for value in raw_junit)
    outputs["sarif"].extend(_resolve_path(str(value), base_dir) for value in raw_sarif)
    outputs["markdown"].extend(
        _resolve_path(str(value), base_dir) for value in raw_markdown
    )
    return outputs


def _normalize_command(value: Any) -> str:
    command = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "simulation": "run",
        "simulate": "run",
        "evaluation": "eval",
        "evalartifact": "eval_artifact",
        "eval_artifacts": "eval_artifact",
        "eval_report": "eval_artifact",
        "eval_reports": "eval_artifact",
        "artifact_eval": "eval_artifact",
        "artifact_evaluation": "eval_artifact",
        "evaltask": "eval_task",
        "eval_tasks": "eval_task",
        "eval_evidence": "eval_task",
        "task_eval": "eval_task",
        "task_evaluation": "eval_task",
        "task_evidence_eval": "eval_task",
        "red_team": "redteam",
        "optimization": "optimize",
        "optimizeeval": "optimize_eval",
        "optimizesuite": "optimize_suite",
        "suite_optimization": "optimize_suite",
        "suite_optimizer": "optimize_suite",
        "subsuite": "suite",
        "sub_suite": "suite",
        "promotion": "promote_to_regression",
        "regression_promotion": "promote_to_regression",
        "promote": "promote_to_regression",
    }
    command = aliases.get(command, command)
    if command not in _CHILD_COMMANDS:
        allowed = ", ".join(sorted(_CHILD_COMMANDS))
        raise SuiteError(f"unsupported suite job command: {command}; expected {allowed}")
    return command


def _normalize_suite_job(job: Mapping[str, Any], index: int) -> dict[str, Any]:
    item = copy.deepcopy(dict(job))
    command = _normalize_command(item.get("command") or item.get("type"))
    path = item.get("path") or item.get("manifest") or item.get("suite")
    if path in (None, ""):
        raise ValueError(f"suite job {index} requires a path")
    item["command"] = command
    item["path"] = _suite_path_text(path)
    item["id"] = str(item.get("id") or item.get("name") or f"{command}-{index}")
    return item


def _suite_path_text(path: str | Path) -> str:
    return str(path)


def _unique_strings(values: Sequence[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    items = values if isinstance(values, (list, tuple, set)) else _as_list(values)
    for value in items:
        text = str(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _job_name(job: Mapping[str, Any]) -> Optional[str]:
    value = job.get("name")
    if value in (None, ""):
        return None
    return str(value)


def _job_threshold(
    job: Mapping[str, Any],
    suite_options: SuiteRunOptions,
) -> Optional[float]:
    if job.get("threshold") is not None:
        return float(job["threshold"])
    return suite_options.threshold


def _job_max_candidates(
    job: Mapping[str, Any],
    suite_options: SuiteRunOptions,
) -> Optional[int]:
    if job.get("max_candidates") is not None:
        return int(job["max_candidates"])
    if job.get("max-candidates") is not None:
        return int(job["max-candidates"])
    return suite_options.max_candidates


def _job_dry_run(job: Mapping[str, Any], suite_options: SuiteRunOptions) -> bool:
    return bool(suite_options.dry_run or job.get("dry_run") or job.get("dry-run"))


def _job_int(
    job: Mapping[str, Any],
    *keys: str,
    default: int,
) -> int:
    for key in keys:
        if job.get(key) is not None:
            return int(job[key])
    return default


def _job_float(
    job: Mapping[str, Any],
    *keys: str,
    default: float,
) -> float:
    for key in keys:
        if job.get(key) is not None:
            return float(job[key])
    return default


def _job_optional_float(
    job: Mapping[str, Any],
    *keys: str,
) -> Optional[float]:
    for key in keys:
        if job.get(key) is not None:
            return float(job[key])
    return None


def _merge_options(
    options: Optional[SuiteRunOptions],
    *,
    name: Optional[str] = None,
    threshold: Optional[float] = None,
    max_candidates: Optional[int] = None,
    dry_run: Optional[bool] = None,
    fail_fast: Optional[bool] = None,
) -> SuiteRunOptions:
    base = options or SuiteRunOptions()
    return SuiteRunOptions(
        name=name if name is not None else base.name,
        threshold=threshold if threshold is not None else base.threshold,
        max_candidates=(
            max_candidates if max_candidates is not None else base.max_candidates
        ),
        dry_run=dry_run if dry_run is not None else base.dry_run,
        fail_fast=fail_fast if fail_fast is not None else base.fail_fast,
    )


def _merge_optimization_options(
    options: Optional[SuiteOptimizationOptions],
    *,
    name: Optional[str] = None,
    threshold: Optional[float] = None,
    max_candidates: Optional[int] = None,
    dry_run: Optional[bool] = None,
) -> SuiteOptimizationOptions:
    base = options or SuiteOptimizationOptions()
    return SuiteOptimizationOptions(
        name=name if name is not None else base.name,
        threshold=threshold if threshold is not None else base.threshold,
        max_candidates=(
            max_candidates if max_candidates is not None else base.max_candidates
        ),
        dry_run=dry_run if dry_run is not None else base.dry_run,
    )


def _optimization_cli() -> Any:
    import importlib

    return importlib.import_module("fi.simulate.cli")


def _load_json_or_yaml(path: Path) -> Any:
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency clarity
            raise SuiteError("YAML suite manifests require PyYAML.") from exc
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _suite_base_dir(suite_path: str | Path) -> Path:
    path = Path(suite_path).expanduser().resolve()
    if path.suffix:
        return path.parent
    return path


def _resolve_path(value: str, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _run_async(awaitable: Any) -> Any:
    return asyncio.run(awaitable)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _as_string_list(value: Any) -> list[str]:
    return [str(item) for item in _as_list(value) if str(item)]


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _suite_key(value: Any) -> str:
    if isinstance(value, Mapping):
        return ""
    if isinstance(value, (list, tuple, set)):
        return ""
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


_KNOWN_ENVIRONMENT_TYPES = {
    "adversarial_attack_pack",
    "agent_control_plane",
    "agent_integration",
    "agent_memory_lineage",
    "agent_trust_boundary",
    "autonomy_loop",
    "browser",
    "domain_package",
    "framework_capability",
    "framework_lifecycle",
    "framework_portability",
    "framework_probe",
    "framework_trace",
    "multimodal_image",
    "multi_agent_room",
    "observability_replay",
    "optimizer_trace",
    "red_team_campaign",
    "red_team_readiness",
    "retrieval_memory",
    "streaming_trace",
    "voice",
    "workspace_run_manifest",
    "world_attack_replay",
    "world_contract",
    "world_orchestration_replay",
}


def _md_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


__all__ = [
    "AGENT_LEARNING_SUITE_KIND",
    "AGENT_LEARNING_SUITE_OPTIMIZATION_KIND",
    "SuiteError",
    "SuiteOptimizationOptions",
    "SuiteRunOptions",
    "build_regression_artifact_suite_manifest",
    "build_suite_manifest",
    "build_trinity_suite_manifest",
    "load_suite",
    "load_suite_file",
    "missing_suite_env",
    "optimize_suite",
    "optimize_suite_file",
    "render_junit",
    "render_markdown",
    "render_sarif",
    "required_suite_env",
    "run_suite",
    "run_suite_file",
    "validate_suite_env",
    "write_suite_file",
]
