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

_CHILD_COMMANDS = {
    "run",
    "eval",
    "redteam",
    "optimize",
    "optimize_eval",
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


def render_junit(result: Mapping[str, Any]) -> str:
    name = escape(str(result.get("name") or "agent-learning-suite"))
    children = list(result.get("children") or result.get("jobs") or [])
    failures = sum(1 for child in children if int(child.get("exit_code", 1)) != 0)
    lines = [
        (
            f'<testsuite name="{name}" tests="{len(children)}" '
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
    suite_passed = len(failed) == 0 and len(children) == job_count
    score = round(len(passed) / job_count, 4) if job_count else 0.0
    command_counts: dict[str, int] = {}
    for child in children:
        command = str(child.get("command") or "unknown")
        command_counts[command] = command_counts.get(command, 0) + 1
    capabilities = _suite_capability_summary(children)
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
        },
        "children": list(children),
        "jobs": list(children),
        "findings": _suite_findings(children),
        "duration_seconds": duration_seconds,
    }


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


def _collect_result_capabilities(payload: Mapping[str, Any], caps: dict[str, set[str]]) -> None:
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
    )
    if not raw:
        raise SuiteError(f"suite job {job.get('id') or ''} requires path")
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


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
        "red_team": "redteam",
        "optimization": "optimize",
        "optimizeeval": "optimize_eval",
    }
    command = aliases.get(command, command)
    if command not in _CHILD_COMMANDS:
        allowed = ", ".join(sorted(_CHILD_COMMANDS))
        raise SuiteError(f"unsupported suite job command: {command}; expected {allowed}")
    return command


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
    "SuiteError",
    "SuiteRunOptions",
    "load_suite",
    "load_suite_file",
    "missing_suite_env",
    "render_junit",
    "render_markdown",
    "render_sarif",
    "required_suite_env",
    "run_suite",
    "run_suite_file",
    "validate_suite_env",
]
