from __future__ import annotations

import asyncio
import copy
import importlib
import importlib.util
import inspect
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .manifest import CLI_SCHEMA_VERSION, ManifestError


EVAL_SUITE_SCHEMA_VERSION = "agent-simulate.eval.v1"
EVAL_SUITE_OPTIMIZATION_SCHEMA_VERSION = "agent-learning.eval-optimization.v1"


@dataclass(frozen=True)
class EvalSuiteOptions:
    name: Optional[str] = None
    threshold: Optional[float] = None
    dry_run: bool = False


@dataclass(frozen=True)
class EvalSuiteOptimizationOptions:
    name: Optional[str] = None
    threshold: Optional[float] = None
    max_candidates: Optional[int] = None
    dry_run: bool = False


def load_eval_suite_file(path: str | Path) -> Dict[str, Any]:
    suite_path = Path(path).expanduser().resolve()
    suite = _load_json_or_yaml(suite_path)
    if not isinstance(suite, Mapping):
        raise ManifestError("eval suite root must be an object")
    return _prepare_eval_suite(dict(suite), base_dir=suite_path.parent)


def run_eval_suite_file(
    path: str | Path,
    *,
    options: Optional[EvalSuiteOptions] = None,
    name: Optional[str] = None,
    threshold: Optional[float] = None,
    dry_run: Optional[bool] = None,
) -> Dict[str, Any]:
    suite_path = Path(path).expanduser().resolve()
    suite = load_eval_suite_file(suite_path)
    return run_eval_suite(
        suite,
        suite_path=suite_path,
        options=_merge_eval_suite_options(
            options,
            name=name,
            threshold=threshold,
            dry_run=dry_run,
        ),
    )


def optimize_eval_suite_file(
    path: str | Path,
    *,
    options: Optional[EvalSuiteOptimizationOptions] = None,
    name: Optional[str] = None,
    threshold: Optional[float] = None,
    max_candidates: Optional[int] = None,
    dry_run: Optional[bool] = None,
) -> Dict[str, Any]:
    """Load and optimize a promptfoo-style eval suite with agent-opt."""

    suite_path = Path(path).expanduser().resolve()
    suite = load_eval_suite_file(suite_path)
    return optimize_eval_suite(
        suite,
        suite_path=suite_path,
        options=_merge_eval_suite_optimization_options(
            options,
            name=name,
            threshold=threshold,
            max_candidates=max_candidates,
            dry_run=dry_run,
        ),
    )


def optimize_eval_suite(
    suite: Mapping[str, Any],
    *,
    suite_path: str | Path = ".",
    options: Optional[EvalSuiteOptimizationOptions] = None,
    name: Optional[str] = None,
    threshold: Optional[float] = None,
    max_candidates: Optional[int] = None,
    dry_run: Optional[bool] = None,
) -> Dict[str, Any]:
    """Optimize an in-memory eval suite and return a unified artifact payload."""

    started = time.time()
    opts = _merge_eval_suite_optimization_options(
        options,
        name=name,
        threshold=threshold,
        max_candidates=max_candidates,
        dry_run=dry_run,
    )
    suite_path = _suite_file_like_path(suite_path)
    runtime_suite = copy.deepcopy(dict(suite))
    if opts.name:
        runtime_suite["name"] = opts.name
    if opts.threshold is not None:
        runtime_suite.setdefault("optimization", {})["threshold"] = opts.threshold
    if opts.max_candidates is not None:
        runtime_suite.setdefault("optimization", {}).setdefault(
            "optimizer", {}
        )["max_candidates"] = opts.max_candidates

    prepared = _prepare_eval_suite(runtime_suite, base_dir=suite_path.parent)
    cli = _cli()
    optimization = cli._optimization_config(prepared)
    target_config = cli._target_config(optimization)
    optimizer_config = cli._optimizer_config(optimization)
    if opts.dry_run:
        return {
            "schema_version": CLI_SCHEMA_VERSION,
            "kind": EVAL_SUITE_OPTIMIZATION_SCHEMA_VERSION,
            "name": str(prepared.get("name") or suite_path.stem),
            "status": "passed",
            "exit_code": 0,
            "dry_run": True,
            "summary": {
                "provider_count": len(_as_list(prepared.get("providers"))),
                "prompt_count": len(_as_list(prepared.get("prompts"))),
                "test_count": len(_as_list(prepared.get("tests"))),
                "search_path_count": len(target_config.get("search_space", {})),
                "max_candidates": optimizer_config.get("max_candidates"),
            },
            "eval_suite": _eval_suite_descriptor(prepared),
            "duration_seconds": round(time.time() - started, 4),
        }

    try:
        from fi.opt import problem_from_eval_suite
    except Exception as exc:  # pragma: no cover - optional dependency clarity
        raise ManifestError("agent-opt is required for eval-suite optimization.") from exc

    problem = problem_from_eval_suite(
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
    payload["kind"] = EVAL_SUITE_OPTIMIZATION_SCHEMA_VERSION
    payload["eval_suite"] = _eval_suite_descriptor(prepared)
    payload["summary"]["provider_count"] = len(_as_list(prepared.get("providers")))
    payload["summary"]["prompt_count"] = len(_as_list(prepared.get("prompts")))
    payload["summary"]["test_count"] = len(_as_list(prepared.get("tests")))
    payload["optimization"]["source"] = "eval_suite"
    if "manifest_optimization" in payload["optimization"]:
        artifact = copy.deepcopy(payload["optimization"]["manifest_optimization"])
        artifact["kind"] = "eval_suite_optimization"
        artifact["source"] = "eval_suite"
        payload["optimization"]["eval_suite_optimization"] = artifact
    return payload


def run_eval_suite(
    suite: Mapping[str, Any],
    *,
    suite_path: str | Path = ".",
    options: Optional[EvalSuiteOptions] = None,
) -> Dict[str, Any]:
    started = time.time()
    opts = options or EvalSuiteOptions()
    base_dir = Path(suite_path).expanduser().resolve().parent
    prepared = _prepare_eval_suite(dict(suite), base_dir=base_dir)
    name = str(opts.name or prepared.get("name") or "agent-simulate-eval")
    threshold = float(opts.threshold if opts.threshold is not None else prepared.get("threshold", 1.0))
    if opts.dry_run:
        return _suite_result(
            name=name,
            suite=prepared,
            cases=[],
            threshold=threshold,
            duration_seconds=round(time.time() - started, 4),
            dry_run=True,
        )

    cases: List[Dict[str, Any]] = []
    providers = [_as_dict(provider) for provider in _as_list(prepared.get("providers"))]
    prompts = [_as_dict(prompt) for prompt in _as_list(prepared.get("prompts"))]
    tests = [_as_dict(test) for test in _as_list(prepared.get("tests"))]
    for provider in providers:
        for prompt in prompts:
            for test_index, test in enumerate(tests, start=1):
                cases.append(
                    _run_eval_case(
                        provider=provider,
                        prompt=prompt,
                        test=test,
                        test_index=test_index,
                        base_dir=base_dir,
                    )
                )
    return _suite_result(
        name=name,
        suite=prepared,
        cases=cases,
        threshold=threshold,
        duration_seconds=round(time.time() - started, 4),
        dry_run=False,
    )


def _prepare_eval_suite(suite: Dict[str, Any], *, base_dir: Path) -> Dict[str, Any]:
    providers = [_as_dict(item) for item in _as_list(suite.get("providers") or suite.get("provider"))]
    prompts = [_as_dict(item) for item in _as_list(suite.get("prompts") or suite.get("prompt"))]
    tests = _suite_tests(suite, base_dir=base_dir)
    if not providers:
        raise ManifestError("eval suite requires at least one provider")
    if not prompts:
        raise ManifestError("eval suite requires at least one prompt")
    if not tests:
        raise ManifestError("eval suite requires at least one test")
    suite["providers"] = [_normalize_provider(item, index) for index, item in enumerate(providers, start=1)]
    suite["prompts"] = [_normalize_prompt(item, index) for index, item in enumerate(prompts, start=1)]
    suite["tests"] = [_normalize_test(item, index) for index, item in enumerate(tests, start=1)]
    suite.pop("tests_file", None)
    suite.pop("data_file", None)
    suite.pop("data", None)
    suite.setdefault("version", EVAL_SUITE_SCHEMA_VERSION)
    return suite


def _suite_tests(suite: Mapping[str, Any], *, base_dir: Path) -> List[Dict[str, Any]]:
    tests_value = suite.get("tests")
    tests_file = suite.get("tests_file") or suite.get("data") or suite.get("data_file")
    records: List[Dict[str, Any]] = []
    if isinstance(tests_value, str):
        records.extend(_load_test_records(base_dir / tests_value))
    else:
        records.extend(_as_dict(item) for item in _as_list(tests_value))
    for path in _as_list(tests_file):
        records.extend(_load_test_records(base_dir / str(path)))
    return records


def _load_test_records(path: Path) -> List[Dict[str, Any]]:
    source = path.expanduser().resolve()
    if not source.exists():
        raise ManifestError(f"eval suite tests file not found: {source}")
    if source.suffix.lower() == ".jsonl":
        records = []
        for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ManifestError(f"invalid JSONL in {source}:{line_number}") from exc
            records.append(_as_dict(item))
        return records
    data = _load_json_or_yaml(source)
    if isinstance(data, Mapping) and "tests" in data:
        return [_as_dict(item) for item in _as_list(data.get("tests"))]
    return [_as_dict(item) for item in _as_list(data)]


def _normalize_provider(provider: Mapping[str, Any], index: int) -> Dict[str, Any]:
    item = dict(provider)
    item["id"] = str(item.get("id") or item.get("name") or f"provider_{index}")
    item["type"] = str(item.get("type") or item.get("kind") or "echo")
    return item


def _normalize_prompt(prompt: Mapping[str, Any], index: int) -> Dict[str, Any]:
    item = dict(prompt)
    item["id"] = str(item.get("id") or item.get("name") or f"prompt_{index}")
    item["template"] = str(item.get("template") or item.get("content") or item.get("prompt") or "")
    if not item["template"]:
        raise ManifestError(f"prompt `{item['id']}` requires a template")
    return item


def _normalize_test(test: Mapping[str, Any], index: int) -> Dict[str, Any]:
    item = dict(test)
    item["id"] = str(item.get("id") or item.get("name") or f"test_{index}")
    item["vars"] = _as_dict(item.get("vars") or item.get("variables"))
    assertions = _as_list(item.get("assert") or item.get("assertions") or item.get("checks"))
    item["assertions"] = [_normalize_assertion(assertion, item["id"], offset) for offset, assertion in enumerate(assertions, start=1)]
    if not item["assertions"]:
        raise ManifestError(f"test `{item['id']}` requires at least one assertion")
    return item


def _normalize_assertion(assertion: Any, test_id: str, index: int) -> Dict[str, Any]:
    if isinstance(assertion, str):
        item = {"type": "contains", "value": assertion}
    else:
        item = _as_dict(assertion)
    item["type"] = str(item.get("type") or item.get("kind") or "contains").lower().replace("-", "_")
    if "value" not in item and "expected" in item:
        item["value"] = item.get("expected")
    if "value" not in item:
        raise ManifestError(f"assertion {index} in test `{test_id}` requires a value")
    return item


def _run_eval_case(
    *,
    provider: Mapping[str, Any],
    prompt: Mapping[str, Any],
    test: Mapping[str, Any],
    test_index: int,
    base_dir: Path,
) -> Dict[str, Any]:
    variables = _as_dict(test.get("vars"))
    rendered_prompt = _render_template(str(prompt.get("template") or ""), variables)
    output = _provider_output(
        provider=provider,
        prompt=rendered_prompt,
        variables=variables,
        test=test,
        base_dir=base_dir,
    )
    assertion_results = [
        _evaluate_assertion(assertion, output)
        for assertion in _as_list(test.get("assertions"))
    ]
    failures = [item for item in assertion_results if not item.get("passed")]
    case_id = f"{provider.get('id')}::{prompt.get('id')}::{test.get('id')}"
    score = 1.0 if not assertion_results else (len(assertion_results) - len(failures)) / len(assertion_results)
    findings = [
        {
            "type": "eval_assertion_failed",
            "severity": "high",
            "case_id": case_id,
            "provider_id": provider.get("id"),
            "prompt_id": prompt.get("id"),
            "test_id": test.get("id"),
            "assertion_type": failure.get("type"),
            "expected": failure.get("expected"),
            "actual": output,
        }
        for failure in failures
    ]
    return {
        "index": test_index,
        "id": case_id,
        "name": case_id,
        "provider_id": provider.get("id"),
        "provider_type": provider.get("type"),
        "prompt_id": prompt.get("id"),
        "test_id": test.get("id"),
        "input": rendered_prompt,
        "output": output,
        "score": round(score, 4),
        "passed": not failures,
        "assertions": assertion_results,
        "findings": findings,
        "metrics": [
            {
                "name": "eval_assertions",
                "score": round(score, 4),
                "details": {"assertions": assertion_results, "findings": findings},
            }
        ],
    }


def _provider_output(
    *,
    provider: Mapping[str, Any],
    prompt: str,
    variables: Mapping[str, Any],
    test: Mapping[str, Any],
    base_dir: Path,
) -> str:
    provider_type = str(provider.get("type") or "echo").lower().replace("-", "_")
    if provider_type == "echo":
        return prompt
    if provider_type == "scripted":
        template = str(provider.get("response") or provider.get("output") or provider.get("template") or "")
        if not template:
            responses = _as_list(provider.get("responses"))
            template = str(responses[0]) if responses else prompt
        return _render_template(template, {**variables, "prompt": prompt, "input": prompt})
    if provider_type in {"artifact", "artifact_json", "artifact_file"}:
        return _artifact_provider_output(
            provider=provider,
            prompt=prompt,
            variables=variables,
            base_dir=base_dir,
        )
    if provider_type in {"python", "python_callable", "callable"}:
        target = str(provider.get("target") or provider.get("callable") or "")
        if not target:
            raise ManifestError(f"provider `{provider.get('id')}` requires target")
        callback = _load_callable(target, base_dir)
        value = callback(prompt=prompt, vars=dict(variables), test=dict(test), provider=dict(provider))
        if inspect.isawaitable(value):
            value = asyncio.run(value)
        return str(value)
    raise ManifestError(f"unsupported eval suite provider type: {provider_type}")


def _artifact_provider_output(
    *,
    provider: Mapping[str, Any],
    prompt: str,
    variables: Mapping[str, Any],
    base_dir: Path,
) -> str:
    raw_path = (
        provider.get("path")
        or provider.get("source")
        or provider.get("artifact")
        or variables.get("artifact_path")
        or variables.get("artifact")
    )
    if not raw_path:
        raise ManifestError(f"provider `{provider.get('id')}` requires artifact path")
    rendered_path = _render_template(
        str(raw_path),
        {**variables, "prompt": prompt, "input": prompt},
    )
    artifact_path = Path(rendered_path).expanduser()
    if not artifact_path.is_absolute():
        artifact_path = base_dir / artifact_path
    artifact_path = artifact_path.resolve()
    artifact = _load_json_or_yaml(artifact_path)
    fields = _artifact_fields(provider)
    if not fields:
        return json.dumps(artifact, indent=2, sort_keys=True, default=str)
    extracted = {
        label: _extract_artifact_path(artifact, path)
        for label, path in fields
    }
    return json.dumps(
        {
            "artifact_path": str(artifact_path),
            "fields": extracted,
        },
        indent=2,
        sort_keys=True,
        default=str,
    )


def _artifact_fields(provider: Mapping[str, Any]) -> List[tuple[str, str]]:
    raw_fields = (
        provider.get("fields")
        or provider.get("extract")
        or provider.get("paths")
        or provider.get("json_paths")
    )
    fields: List[tuple[str, str]] = []
    for index, raw_field in enumerate(_as_list(raw_fields), start=1):
        if isinstance(raw_field, str):
            fields.append((raw_field, raw_field))
            continue
        item = _as_dict(raw_field)
        path = str(item.get("path") or item.get("json_path") or item.get("field") or "")
        if not path:
            raise ManifestError(f"artifact field {index} requires path")
        label = str(item.get("id") or item.get("name") or item.get("as") or path)
        fields.append((label, path))
    return fields


def _extract_artifact_path(value: Any, path: str) -> Any:
    current = value
    for token in _artifact_path_tokens(path):
        if isinstance(current, Mapping):
            if token not in current:
                raise ManifestError(f"artifact path `{path}` missing key `{token}`")
            current = current[token]
        elif isinstance(current, list):
            try:
                index = int(token)
            except ValueError as exc:
                raise ManifestError(
                    f"artifact path `{path}` expected list index, got `{token}`"
                ) from exc
            try:
                current = current[index]
            except IndexError as exc:
                raise ManifestError(
                    f"artifact path `{path}` index out of range: {index}"
                ) from exc
        else:
            raise ManifestError(f"artifact path `{path}` cannot traverse `{token}`")
    return current


def _artifact_path_tokens(path: str) -> List[str]:
    normalized = path.strip()
    if not normalized:
        raise ManifestError("artifact path cannot be empty")
    if normalized.startswith("$."):
        normalized = normalized[2:]
    elif normalized == "$":
        return []
    tokens: List[str] = []
    for segment in normalized.split("."):
        if not segment:
            continue
        while "[" in segment:
            before, _, rest = segment.partition("[")
            if before:
                tokens.append(before)
            index, marker, tail = rest.partition("]")
            if not marker:
                raise ManifestError(f"invalid artifact path segment `{segment}`")
            tokens.append(index)
            segment = tail
        if segment:
            tokens.append(segment)
    return tokens


def _evaluate_assertion(assertion: Mapping[str, Any], output: str) -> Dict[str, Any]:
    assertion_type = str(assertion.get("type") or "contains").lower().replace("-", "_")
    expected = assertion.get("value")
    text = str(output)
    expected_text = str(expected)
    if assertion_type == "contains":
        passed = expected_text in text
    elif assertion_type == "not_contains":
        passed = expected_text not in text
    elif assertion_type in {"equals", "equal", "is"}:
        passed = text.strip() == expected_text.strip()
    elif assertion_type in {"regex", "matches"}:
        passed = re.search(expected_text, text, flags=re.MULTILINE) is not None
    else:
        raise ManifestError(f"unsupported assertion type: {assertion_type}")
    return {
        "type": assertion_type,
        "expected": expected,
        "actual": output,
        "passed": bool(passed),
    }


def _suite_result(
    *,
    name: str,
    suite: Mapping[str, Any],
    cases: Sequence[Mapping[str, Any]],
    threshold: float,
    duration_seconds: float,
    dry_run: bool,
) -> Dict[str, Any]:
    case_count = len(cases)
    passed_count = sum(1 for case in cases if case.get("passed"))
    assertion_count = sum(len(_as_list(case.get("assertions"))) for case in cases)
    failed_assertion_count = sum(
        1
        for case in cases
        for assertion in _as_list(case.get("assertions"))
        if not _as_dict(assertion).get("passed")
    )
    score = 1.0 if not assertion_count else (assertion_count - failed_assertion_count) / assertion_count
    passed = (score >= threshold) and (passed_count == case_count)
    if dry_run:
        passed = True
    return {
        "schema_version": CLI_SCHEMA_VERSION,
        "kind": EVAL_SUITE_SCHEMA_VERSION,
        "name": name,
        "status": "passed" if passed else "failed",
        "exit_code": 0 if passed else 1,
        "summary": {
            "score": round(score, 4),
            "threshold": threshold,
            "provider_count": len(_as_list(suite.get("providers"))),
            "prompt_count": len(_as_list(suite.get("prompts"))),
            "test_count": len(_as_list(suite.get("tests"))),
            "case_count": case_count,
            "passed_case_count": passed_count,
            "failed_case_count": case_count - passed_count,
            "assertion_count": assertion_count,
            "passed_assertion_count": assertion_count - failed_assertion_count,
            "failed_assertion_count": failed_assertion_count,
            "dry_run": dry_run,
        },
        "eval_suite": {
            "version": suite.get("version") or EVAL_SUITE_SCHEMA_VERSION,
            "providers": [
                {"id": provider.get("id"), "type": provider.get("type")}
                for provider in _as_list(suite.get("providers"))
                if isinstance(provider, Mapping)
            ],
            "prompts": [
                {"id": prompt.get("id")}
                for prompt in _as_list(suite.get("prompts"))
                if isinstance(prompt, Mapping)
            ],
            "tests": [
                {"id": test.get("id")}
                for test in _as_list(suite.get("tests"))
                if isinstance(test, Mapping)
            ],
            "cases": list(cases),
        },
        "evaluation": {
            "passed": passed,
            "score": round(score, 4),
            "threshold": threshold,
            "cases": list(cases),
            "findings": [
                finding
                for case in cases
                for finding in _as_list(case.get("findings"))
                if isinstance(finding, Mapping)
            ],
        },
        "duration_seconds": duration_seconds,
    }


def _render_template(template: str, variables: Mapping[str, Any]) -> str:
    result = template
    for key, value in variables.items():
        result = result.replace("{{" + str(key) + "}}", str(value))
        result = result.replace("{{ " + str(key) + " }}", str(value))
    return result


def _load_json_or_yaml(path: Path) -> Any:
    if not path.exists():
        raise ManifestError(f"eval suite file not found: {path}")
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency clarity
            raise ManifestError("YAML eval suites require PyYAML; use JSON or install PyYAML.") from exc
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _load_callable(target: str, base_dir: Path) -> Callable[..., Any]:
    module_name, _, function_name = target.partition(":")
    if not module_name or not function_name:
        raise ManifestError("python callable must use 'module:function' or 'path.py:function'")
    if module_name.endswith(".py") or "/" in module_name:
        module_path = Path(module_name)
        if not module_path.is_absolute():
            module_path = base_dir / module_path
        spec = importlib.util.spec_from_file_location(module_path.stem, module_path)
        if spec is None or spec.loader is None:
            raise ManifestError(f"cannot load python module: {module_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        module = importlib.import_module(module_name)
    callback = getattr(module, function_name, None)
    if not callable(callback):
        raise ManifestError(f"python callable not found: {target}")
    return callback


def _merge_eval_suite_options(
    options: Optional[EvalSuiteOptions],
    *,
    name: Optional[str],
    threshold: Optional[float],
    dry_run: Optional[bool],
) -> EvalSuiteOptions:
    opts = options or EvalSuiteOptions()
    return EvalSuiteOptions(
        name=opts.name if name is None else name,
        threshold=opts.threshold if threshold is None else threshold,
        dry_run=opts.dry_run if dry_run is None else dry_run,
    )


def _merge_eval_suite_optimization_options(
    options: Optional[EvalSuiteOptimizationOptions],
    *,
    name: Optional[str],
    threshold: Optional[float],
    max_candidates: Optional[int],
    dry_run: Optional[bool],
) -> EvalSuiteOptimizationOptions:
    opts = options or EvalSuiteOptimizationOptions()
    return EvalSuiteOptimizationOptions(
        name=opts.name if name is None else name,
        threshold=opts.threshold if threshold is None else threshold,
        max_candidates=opts.max_candidates if max_candidates is None else max_candidates,
        dry_run=opts.dry_run if dry_run is None else dry_run,
    )


def _suite_file_like_path(path: str | Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    if resolved.is_dir():
        return resolved / "eval_suite.json"
    return resolved


def _eval_suite_descriptor(suite: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "version": suite.get("version") or EVAL_SUITE_SCHEMA_VERSION,
        "providers": [
            {"id": provider.get("id"), "type": provider.get("type")}
            for provider in _as_list(suite.get("providers"))
            if isinstance(provider, Mapping)
        ],
        "prompts": [
            {"id": prompt.get("id")}
            for prompt in _as_list(suite.get("prompts"))
            if isinstance(prompt, Mapping)
        ],
        "tests": [
            {"id": test.get("id")}
            for test in _as_list(suite.get("tests"))
            if isinstance(test, Mapping)
        ],
    }


def _cli() -> Any:
    return importlib.import_module("fi.simulate.cli")


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _as_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


__all__ = [
    "EVAL_SUITE_SCHEMA_VERSION",
    "EVAL_SUITE_OPTIMIZATION_SCHEMA_VERSION",
    "EvalSuiteOptimizationOptions",
    "EvalSuiteOptions",
    "load_eval_suite_file",
    "optimize_eval_suite",
    "optimize_eval_suite_file",
    "run_eval_suite",
    "run_eval_suite_file",
]
