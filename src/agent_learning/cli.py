from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .config import current_config


AGENT_LEARNING_EVAL_KIND = "agent-learning.eval.v1"
AGENT_LEARNING_EVAL_OPTIMIZATION_KIND = "agent-learning.eval-optimization.v1"
AGENT_LEARNING_OPTIMIZATION_KIND = "agent-learning.optimization.v1"
AGENT_LEARNING_REDTEAM_KIND = "agent-learning.redteam.v1"
AGENT_LEARNING_RUN_KIND = "agent-learning.run.v1"


SIMULATE_COMMANDS = {
    "baseline",
    "compare",
    "init",
    "promote-to-regression",
    "replay",
    "report",
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    if not args:
        return _help()
    command = args[0]
    if command == "doctor":
        return _doctor()
    if command == "run":
        return _run(args[1:])
    if command == "eval":
        return _eval(args[1:])
    if command == "redteam":
        return _redteam(args[1:])
    if command == "optimize":
        return _optimize(args[1:])
    if command == "optimize-eval":
        return _optimize_eval(args[1:])
    if command == "simulate":
        return _simulate(args[1:])
    if command in SIMULATE_COMMANDS:
        return _simulate(args)
    return _help(f"unknown command: {command}")


def _simulate(args: Sequence[str]) -> int:
    try:
        cli = importlib.import_module("fi.simulate.cli")
    except Exception as exc:
        print(
            "agent-learn: simulation commands require "
            "`agent-learning-kit[simulate]` or `agent-learning-kit[trinity]`.",
            file=sys.stderr,
        )
        print(f"agent-learn: import failed: {exc}", file=sys.stderr)
        return 2
    return int(cli.main(list(args)))


def _run(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-learn run",
        description="Run a local simulation/evaluation manifest with Agent Learning Kit.",
    )
    _add_manifest_run_args(parser)
    parsed = parser.parse_args(list(args))

    try:
        from fi.simulate.manifest import (
            load_manifest_file,
            render_junit,
            render_markdown,
            render_sarif,
            run_manifest_file,
        )
    except Exception as exc:
        print(
            "agent-learn run requires `agent-learning-kit[simulate]` "
            "or `agent-learning-kit[trinity]`.",
            file=sys.stderr,
        )
        print(f"agent-learn: import failed: {exc}", file=sys.stderr)
        return 2

    manifest_path = Path(parsed.manifest).expanduser().resolve()
    try:
        manifest = load_manifest_file(manifest_path)
        payload = _run_async(
            run_manifest_file(
                manifest_path,
                name=parsed.name,
                threshold=parsed.threshold,
                no_eval=bool(parsed.no_eval),
                dry_run=bool(parsed.dry_run),
            )
        )
    except Exception as exc:
        print(f"agent-learn run: {exc}", file=sys.stderr)
        return 1

    payload["kind"] = AGENT_LEARNING_RUN_KIND
    written = _write_result_outputs(
        payload,
        manifest,
        parsed,
        manifest_path,
        render_junit=render_junit,
        render_sarif=render_sarif,
        render_markdown=render_markdown,
    )
    payload["outputs_written"] = written
    if not written and not parsed.quiet:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return int(payload.get("exit_code", 0))


def _eval(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-learn eval",
        description="Run a promptfoo-style eval suite with Agent Learning Kit.",
    )
    _add_eval_suite_args(parser, optimize=False)
    parsed = parser.parse_args(list(args))

    try:
        from fi.simulate.manifest import render_junit, render_markdown, render_sarif
        from fi.simulate.suite import load_eval_suite_file, run_eval_suite_file
    except Exception as exc:
        print(
            "agent-learn eval requires `agent-learning-kit[simulate]` "
            "or `agent-learning-kit[trinity]`.",
            file=sys.stderr,
        )
        print(f"agent-learn: import failed: {exc}", file=sys.stderr)
        return 2

    suite_path = Path(parsed.suite).expanduser().resolve()
    try:
        suite = load_eval_suite_file(suite_path)
        payload = run_eval_suite_file(
            suite_path,
            name=parsed.name,
            threshold=parsed.threshold,
            dry_run=bool(parsed.dry_run),
        )
    except Exception as exc:
        print(f"agent-learn eval: {exc}", file=sys.stderr)
        return 1

    payload["kind"] = AGENT_LEARNING_EVAL_KIND
    written = _write_result_outputs(
        payload,
        suite,
        parsed,
        suite_path,
        render_junit=render_junit,
        render_sarif=render_sarif,
        render_markdown=render_markdown,
    )
    payload["outputs_written"] = written
    if not written and not parsed.quiet:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return int(payload.get("exit_code", 0))


def _redteam(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-learn redteam",
        description="Run a red-team simulation manifest with Agent Learning Kit.",
    )
    _add_redteam_args(parser)
    parsed = parser.parse_args(list(args))

    try:
        from fi.simulate.manifest import (
            load_manifest_file,
            redteam_manifest_file,
            render_junit,
            render_markdown,
            render_sarif,
        )
    except Exception as exc:
        print(
            "agent-learn redteam requires `agent-learning-kit[simulate]` "
            "or `agent-learning-kit[trinity]`.",
            file=sys.stderr,
        )
        print(f"agent-learn: import failed: {exc}", file=sys.stderr)
        return 2

    manifest_path = Path(parsed.manifest).expanduser().resolve()
    try:
        manifest = load_manifest_file(manifest_path)
        payload = _run_async(
            redteam_manifest_file(
                manifest_path,
                name=parsed.name,
                threshold=parsed.threshold,
                dry_run=bool(parsed.dry_run),
            )
        )
    except Exception as exc:
        print(f"agent-learn redteam: {exc}", file=sys.stderr)
        return 1

    payload["kind"] = AGENT_LEARNING_REDTEAM_KIND
    written = _write_result_outputs(
        payload,
        manifest,
        parsed,
        manifest_path,
        render_junit=render_junit,
        render_sarif=render_sarif,
        render_markdown=render_markdown,
    )
    payload["outputs_written"] = written
    if not written and not parsed.quiet:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return int(payload.get("exit_code", 0))


def _optimize(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-learn optimize",
        description="Optimize a simulation manifest with Agent Learning Kit.",
    )
    _add_manifest_optimization_args(parser)
    parsed = parser.parse_args(list(args))

    try:
        from fi.simulate.manifest import (
            load_manifest_file,
            optimize_manifest_file,
            render_junit,
            render_markdown,
            render_sarif,
        )
    except Exception as exc:
        print(
            "agent-learn optimize requires `agent-learning-kit[trinity]`.",
            file=sys.stderr,
        )
        print(f"agent-learn: import failed: {exc}", file=sys.stderr)
        return 2

    manifest_path = Path(parsed.manifest).expanduser().resolve()
    try:
        manifest = load_manifest_file(manifest_path)
        payload = optimize_manifest_file(
            manifest_path,
            name=parsed.name,
            threshold=parsed.threshold,
            max_candidates=parsed.max_candidates,
            dry_run=bool(parsed.dry_run),
        )
    except Exception as exc:
        print(f"agent-learn optimize: {exc}", file=sys.stderr)
        return 1

    payload["kind"] = AGENT_LEARNING_OPTIMIZATION_KIND
    written = _write_result_outputs(
        payload,
        manifest,
        parsed,
        manifest_path,
        render_junit=render_junit,
        render_sarif=render_sarif,
        render_markdown=render_markdown,
    )
    payload["outputs_written"] = written
    if not written and not parsed.quiet:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return int(payload.get("exit_code", 0))


def _optimize_eval(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-learn optimize-eval",
        description=(
            "Optimize a promptfoo-style eval suite with the unified agent "
            "learning runtime."
        ),
    )
    _add_eval_suite_args(parser, optimize=True)
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=None,
        help="Override optimization.optimizer.max_candidates.",
    )
    parsed = parser.parse_args(list(args))

    try:
        from fi.simulate.manifest import render_junit, render_markdown, render_sarif
        from fi.simulate.suite import load_eval_suite_file, optimize_eval_suite_file
    except Exception as exc:
        print(
            "agent-learn optimize-eval requires "
            "`agent-learning-kit[trinity]`.",
            file=sys.stderr,
        )
        print(f"agent-learn: import failed: {exc}", file=sys.stderr)
        return 2

    suite_path = Path(parsed.suite).expanduser().resolve()
    try:
        suite = load_eval_suite_file(suite_path)
        payload = optimize_eval_suite_file(
            suite_path,
            name=parsed.name,
            threshold=parsed.threshold,
            max_candidates=parsed.max_candidates,
            dry_run=bool(parsed.dry_run),
        )
    except Exception as exc:
        print(f"agent-learn optimize-eval: {exc}", file=sys.stderr)
        return 1

    payload["kind"] = AGENT_LEARNING_EVAL_OPTIMIZATION_KIND
    written = _write_result_outputs(
        payload,
        suite,
        parsed,
        suite_path,
        render_junit=render_junit,
        render_sarif=render_sarif,
        render_markdown=render_markdown,
    )
    payload["outputs_written"] = written
    if not written and not parsed.quiet:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return int(payload.get("exit_code", 0))


def _add_manifest_optimization_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("manifest", help="Path to a JSON/YAML optimization manifest.")
    parser.add_argument(
        "-o",
        "--output",
        action="append",
        default=[],
        help=(
            "Write JSON output to this path. .xml paths are treated as JUnit; "
            ".sarif paths as SARIF."
        ),
    )
    parser.add_argument(
        "--junit",
        action="append",
        default=[],
        help="Write compact JUnit XML output.",
    )
    parser.add_argument(
        "--sarif",
        action="append",
        default=[],
        help="Write SARIF 2.1.0 findings output.",
    )
    parser.add_argument(
        "--markdown",
        "--md",
        action="append",
        default=[],
        help="Write Markdown report output.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Override optimization.threshold.",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=None,
        help="Override optimization.optimizer.max_candidates.",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Override the optimization run name.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate manifest/search space without executing optimization.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print JSON summary when no output path is configured.",
    )


def _add_manifest_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("manifest", help="Path to a JSON/YAML manifest.")
    parser.add_argument(
        "-o",
        "--output",
        action="append",
        default=[],
        help=(
            "Write JSON output to this path. .xml paths are treated as JUnit; "
            ".sarif paths as SARIF."
        ),
    )
    parser.add_argument(
        "--junit",
        action="append",
        default=[],
        help="Write compact JUnit XML output.",
    )
    parser.add_argument(
        "--sarif",
        action="append",
        default=[],
        help="Write SARIF 2.1.0 findings output.",
    )
    parser.add_argument(
        "--markdown",
        "--md",
        action="append",
        default=[],
        help="Write Markdown report output.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Override evaluation.agent_report.threshold.",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Override the run name.",
    )
    parser.add_argument(
        "--no-eval",
        action="store_true",
        help="Run simulation only.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate manifest/env without executing.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print JSON summary when no output path is configured.",
    )


def _add_redteam_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("manifest", help="Path to a JSON/YAML red-team manifest.")
    parser.add_argument(
        "-o",
        "--output",
        action="append",
        default=[],
        help=(
            "Write JSON output to this path. .xml paths are treated as JUnit; "
            ".sarif paths as SARIF."
        ),
    )
    parser.add_argument(
        "--junit",
        action="append",
        default=[],
        help="Write compact JUnit XML output.",
    )
    parser.add_argument(
        "--sarif",
        action="append",
        default=[],
        help="Write SARIF 2.1.0 findings output.",
    )
    parser.add_argument(
        "--markdown",
        "--md",
        action="append",
        default=[],
        help="Write Markdown report output.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Override evaluation.agent_report.threshold.",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Override the red-team run name.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate red-team manifest/env without executing.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print JSON summary when no output path is configured.",
    )


def _add_eval_suite_args(parser: argparse.ArgumentParser, *, optimize: bool) -> None:
    parser.add_argument(
        "suite",
        help="Path to a JSON/YAML eval suite.",
    )
    parser.add_argument(
        "-o",
        "--output",
        action="append",
        default=[],
        help=(
            "Write JSON output to this path. .xml paths are treated as JUnit; "
            ".sarif paths as SARIF."
        ),
    )
    parser.add_argument(
        "--junit",
        action="append",
        default=[],
        help="Write compact JUnit XML output.",
    )
    parser.add_argument(
        "--sarif",
        action="append",
        default=[],
        help="Write SARIF 2.1.0 findings output.",
    )
    parser.add_argument(
        "--markdown",
        "--md",
        action="append",
        default=[],
        help="Write Markdown report output.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help=(
            "Override optimization.threshold."
            if optimize
            else "Override suite threshold."
        ),
    )
    parser.add_argument(
        "--name",
        default=None,
        help=(
            "Override the optimization run name."
            if optimize
            else "Override the suite run name."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate suite/search space without executing optimization."
            if optimize
            else "Validate suite shape without executing providers."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print JSON summary when no output path is configured.",
    )


def _write_result_outputs(
    payload: Dict[str, Any],
    suite: Mapping[str, Any],
    args: argparse.Namespace,
    suite_path: Path,
    *,
    render_junit: Any,
    render_sarif: Any,
    render_markdown: Any,
) -> List[str]:
    output_paths = _result_output_paths(suite, args, suite_path.parent)
    written: List[str] = []
    for path in output_paths["json"]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        written.append(str(path))
    for path in output_paths["junit"]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_junit(payload), encoding="utf-8")
        written.append(str(path))
    for path in output_paths["sarif"]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_sarif(payload, manifest_path=suite_path), encoding="utf-8")
        written.append(str(path))
    for path in output_paths["markdown"]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_markdown(payload, source_path=suite_path), encoding="utf-8")
        written.append(str(path))
    return written


def _result_output_paths(
    suite: Mapping[str, Any],
    args: argparse.Namespace,
    base_dir: Path,
) -> Dict[str, List[Path]]:
    outputs: Dict[str, List[Path]] = {
        "json": [],
        "junit": [],
        "sarif": [],
        "markdown": [],
    }
    suite_outputs = dict(suite.get("outputs") or {})
    raw_json = [
        *_as_list(suite_outputs.get("json")),
        *_as_list(getattr(args, "output", [])),
    ]
    raw_junit = [
        *_as_list(suite_outputs.get("junit")),
        *_as_list(getattr(args, "junit", [])),
    ]
    raw_sarif = [
        *_as_list(suite_outputs.get("sarif")),
        *_as_list(getattr(args, "sarif", [])),
    ]
    raw_markdown = [
        *_as_list(suite_outputs.get("markdown")),
        *_as_list(suite_outputs.get("md")),
        *_as_list(getattr(args, "markdown", [])),
    ]
    for value in raw_json:
        path = _resolve_output_path(str(value), base_dir)
        if path.name.endswith((".junit.xml", ".xml")):
            outputs["junit"].append(path)
        elif path.name.endswith((".sarif", ".sarif.json")):
            outputs["sarif"].append(path)
        else:
            outputs["json"].append(path)
    outputs["junit"].extend(_resolve_output_path(str(value), base_dir) for value in raw_junit)
    outputs["sarif"].extend(_resolve_output_path(str(value), base_dir) for value in raw_sarif)
    outputs["markdown"].extend(
        _resolve_output_path(str(value), base_dir) for value in raw_markdown
    )
    return outputs


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _resolve_output_path(value: str, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return base_dir / path


def _run_async(awaitable: Any) -> Any:
    try:
        import asyncio
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("asyncio is required for agent-learn run.") from exc
    return asyncio.run(awaitable)


def _doctor() -> int:
    modules = {
        "simulate": "fi.simulate",
        "evaluation": "fi.evals",
        "optimize": "fi.opt",
    }
    payload = {
        "config": {
            "api_key_configured": bool(current_config().api_key),
            "api_url": current_config().api_url,
            "project_id_configured": bool(current_config().project_id),
            "workspace_id_configured": bool(current_config().workspace_id),
        },
        "modules": {},
    }
    for name, module_name in modules.items():
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            payload["modules"][name] = {
                "available": False,
                "module": module_name,
                "error": str(exc),
            }
        else:
            payload["modules"][name] = {
                "available": True,
                "module": module_name,
            }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _help(error: Optional[str] = None) -> int:
    if error:
        print(f"agent-learn: {error}", file=sys.stderr)
    parser = argparse.ArgumentParser(
        prog="agent-learn",
        description="Unified CLI for Future AGI agent simulation, evaluation, and optimization.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        help=(
            "doctor, simulate, run, eval, redteam, optimize, replay, report, "
            "compare, baseline, promote-to-regression, optimize-eval, init"
        ),
    )
    parser.print_help(sys.stderr if error else sys.stdout)
    return 2 if error else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
