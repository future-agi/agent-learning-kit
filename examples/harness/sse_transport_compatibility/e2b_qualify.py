#!/usr/bin/env python3
"""Provision one E2B sandbox and run the fixed ten-case SSE qualification."""

# /// script
# requires-python = ">=3.10"
# dependencies = ["e2b==2.37.1"]
# ///

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[2]
SSE_MODULE = REPOSITORY / "src/fi/simulate/agent/wrappers/http_stream.py"
REMOTE_ROOT = "/work/sse-qualification"
CASE_IDS = (
    "crewai_backend_tool_stream",
    "langgraph_stream_text",
    "adk_stream_text",
    "delta_assembly",
    "cumulative_assembly",
    "final_only",
    "done_before_eof",
    "terminal_error",
    "incomplete_eof",
    "cancellation_recovery",
)
FRAMEWORKS = {
    "crewai_backend_tool_stream": "crewai",
    "langgraph_stream_text": "langgraph",
    "adk_stream_text": "google_adk",
}
RECEIPT_KEYS = {
    "case_id",
    "status",
    "reason",
    "framework",
    "response_characters",
    "stream_events",
    "terminal",
    "source",
}
SOURCE_KEYS = {
    "repository",
    "commit",
    "subdirectory",
    "framework",
    "model",
    "endpoint",
    "provider_patch_sha256",
    "versions",
}
SOURCE_SUBDIRECTORIES = {
    "crewai": "integrations/crew-ai/python",
    "langgraph": "integrations/langgraph/python",
    "google_adk": "integrations/adk-middleware/python",
}
SOURCE_ENDPOINTS = {
    "crewai": "/conversational_flows/backend_tool_rendering",
    "langgraph": "/agent/agentic_chat",
    "google_adk": "/",
}
SOURCE_VERSIONS = {
    "crewai": {
        "crewai": "1.15.16",
        "ag-ui-crewai": "1.0.0",
        "ag-ui-protocol": "1.0.0",
    },
    "langgraph": {
        "langgraph": "1.2.10",
        "ag-ui-langgraph": "0.0.47",
        "ag-ui-protocol": "1.0.0",
    },
    "google_adk": {
        "google-adk": "2.2.0",
        "ag-ui-adk": "0.12.0",
        "ag-ui-protocol": "1.0.0",
    },
}
SOURCE_MODELS = {
    "crewai": "gemini-3.7-flash",
    "langgraph": "deterministic",
    "google_adk": "gemini-3.7-flash",
}
PROVIDER_PATCH_SHA256 = (
    "4dda0890381ec5ee155eb1dc0c44087f65b78a44c9025e991aa3b4d894022f82"
)
REASONS = {
    "ok",
    "unexpected_result",
    "waited_for_eof",
    "error_not_propagated",
    "incomplete_stream_accepted",
    "cancellation_not_observed",
    "framework_setup",
    "continuity_not_observed",
    "framework_runtime",
    "stream_text_not_observed",
    "not_reached",
    "provenance_mismatch",
    "runner_exception",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_inputs(
    env: Mapping[str, str], credential: Path, output: Path
) -> tuple[str, str]:
    api_key = env.get("E2B_API_KEY", "").strip()
    project = env.get("VERTEXAI_PROJECT", "").strip()
    if not api_key:
        raise ValueError("E2B_API_KEY is required")
    if not project:
        raise ValueError("VERTEXAI_PROJECT is required")
    if not credential.is_file() or credential.is_symlink():
        raise ValueError("Vertex credential must be a readable regular file")
    if credential.stat().st_size > 1024 * 1024:
        raise ValueError("Vertex credential file is unexpectedly large")
    output.parent.mkdir(parents=True, exist_ok=True)
    return api_key, project


def _tracked_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
    )
    paths = {Path(value.decode()) for value in completed.stdout.split(b"\0") if value}
    paths.update(
        {
            Path("examples/harness/sse_transport_compatibility/e2b_cases.py"),
            Path("examples/harness/sse_transport_compatibility/e2b_qualify.py"),
        }
    )
    return sorted(path for path in paths if (REPOSITORY / path).is_file())


def _source_archive() -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for relative in _tracked_files():
            path = (REPOSITORY / relative).resolve()
            if REPOSITORY.resolve() not in path.parents:
                raise ValueError(f"source path escapes repository: {relative}")
            archive.add(path, arcname=str(relative), recursive=False)
    return stream.getvalue()


def validate_report(raw: bytes, expected_hash: str) -> dict[str, Any]:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate report key: {key}")
            result[key] = value
        return result

    report = json.loads(raw, object_pairs_hook=no_duplicates)
    if not isinstance(report, dict) or set(report) != {
        "schema_version",
        "provenance",
        "receipts",
        "passed",
    }:
        raise ValueError("qualification report shape is invalid")
    if report["schema_version"] != "futureagi.sse-e2b-qualification.v1":
        raise ValueError("qualification report schema is invalid")
    provenance = report["provenance"]
    if not isinstance(provenance, dict) or set(provenance) != {
        "expected_sha256",
        "imported_sha256",
        "imported_from_branch",
    }:
        raise ValueError("qualification provenance shape is invalid")
    if (
        provenance["expected_sha256"] != expected_hash
        or provenance["imported_sha256"] != expected_hash
        or provenance["imported_from_branch"] is not True
    ):
        raise ValueError("sandbox imported a different ALK SSE module")
    receipts = report["receipts"]
    if not isinstance(receipts, list) or len(receipts) != len(CASE_IDS):
        raise ValueError("qualification must contain exactly ten receipts")
    by_id: dict[str, dict[str, Any]] = {}
    for receipt in receipts:
        if not isinstance(receipt, dict) or set(receipt) != RECEIPT_KEYS:
            raise ValueError("qualification receipt shape is invalid")
        case_id = receipt["case_id"]
        if case_id not in CASE_IDS or case_id in by_id:
            raise ValueError("qualification receipt ID is invalid")
        if receipt["status"] not in {"passed", "failed", "withheld"}:
            raise ValueError("qualification receipt status is invalid")
        if receipt["reason"] not in REASONS:
            raise ValueError("qualification receipt reason is invalid")
        if receipt["terminal"] not in {"RUN_FINISHED", "RUN_ERROR", "none"}:
            raise ValueError("qualification receipt terminal is invalid")
        for count in ("response_characters", "stream_events"):
            if type(receipt[count]) is not int or not 0 <= receipt[count] <= 1_000_000:
                raise ValueError("qualification receipt count is invalid")
        expected_framework = FRAMEWORKS.get(case_id)
        if receipt["framework"] != expected_framework:
            raise ValueError("qualification framework attribution is invalid")
        source = receipt["source"]
        if expected_framework and receipt["status"] == "passed":
            if not isinstance(source, dict) or set(source) != SOURCE_KEYS:
                raise ValueError("passing framework receipt lacks source provenance")
            if (
                source["repository"] != "https://github.com/ag-ui-protocol/ag-ui.git"
                or source["framework"] != expected_framework
                or source["subdirectory"] != SOURCE_SUBDIRECTORIES[expected_framework]
                or source["model"] != SOURCE_MODELS[expected_framework]
            ):
                raise ValueError("framework source provenance is invalid")
            if source["commit"] != "2b967c34c49759c4afe0f0a176b281a0f645de34":
                raise ValueError("framework source revision is invalid")
            if (
                source["endpoint"] != SOURCE_ENDPOINTS[expected_framework]
                or source["provider_patch_sha256"] != PROVIDER_PATCH_SHA256
            ):
                raise ValueError("framework source evidence is invalid")
            if source["versions"] != SOURCE_VERSIONS[expected_framework]:
                raise ValueError("framework versions are invalid")
            if receipt["terminal"] != "RUN_FINISHED":
                raise ValueError("passing framework receipt lacks completion")
        elif source is not None:
            raise ValueError("non-passing receipt must not publish source metadata")
        by_id[case_id] = receipt
    if tuple(by_id) != CASE_IDS:
        raise ValueError("qualification receipt order is invalid")
    passed = all(receipt["status"] == "passed" for receipt in receipts)
    if report["passed"] is not passed:
        raise ValueError("qualification aggregate is inconsistent")
    return report


def _sandbox_command(
    sandbox: Any,
    command: str,
    *,
    timeout: int,
    envs: dict[str, str] | None = None,
) -> int:
    try:
        result = sandbox.commands.run(
            command,
            timeout=timeout,
            envs=envs or {},
            user="root",
        )
    except Exception as exc:
        from e2b import CommandExitException

        if isinstance(exc, CommandExitException):
            return int(exc.exit_code)
        raise
    return int(getattr(result, "exit_code", 1))


def qualify(
    *,
    credential: Path,
    output: Path,
    template: str,
    timeout_seconds: int,
    env: Mapping[str, str] = os.environ,
    sandbox_factory: Callable[..., Any] | None = None,
) -> int:
    api_key, project = _validate_inputs(env, credential, output)
    expected_hash = _sha256(SSE_MODULE)
    archive = _source_archive()
    if sandbox_factory is None:
        from e2b import Sandbox

        sandbox_factory = Sandbox.create
    sandbox = sandbox_factory(
        template=template,
        timeout=timeout_seconds,
        secure=True,
        allow_internet_access=True,
        network={"allow_public_traffic": False},
        metadata={"qualification": "alk-sse-ten-cases"},
        api_key=api_key,
        request_timeout=300,
    )
    sandbox_id = str(sandbox.sandbox_id)
    sandbox_started = time.monotonic()
    raw_report: bytes | None = None
    cleanup_succeeded = False
    try:
        setup = _sandbox_command(
            sandbox,
            f"install -d -m 0700 {REMOTE_ROOT} {REMOTE_ROOT}/secrets "
            f"{REMOTE_ROOT}/output {REMOTE_ROOT}/branch",
            timeout=60,
        )
        if setup:
            raise RuntimeError("sandbox_setup")
        sandbox.files.write(f"{REMOTE_ROOT}/source.tar.gz", archive, user="root")
        sandbox.files.write(
            f"{REMOTE_ROOT}/secrets/vertex.json", credential.read_bytes(), user="root"
        )
        if _sandbox_command(
            sandbox,
            f"chmod 0600 {REMOTE_ROOT}/secrets/vertex.json && "
            f"tar -xzf {REMOTE_ROOT}/source.tar.gz -C {REMOTE_ROOT}/branch && "
            f"rm {REMOTE_ROOT}/source.tar.gz",
            timeout=120,
        ):
            raise RuntimeError("sandbox_upload")
        bootstrap = (
            "command -v uv >/dev/null 2>&1 || python3 -m pip install uv; "
            f"uv venv {REMOTE_ROOT}/alk-venv; "
            f"uv pip install --python {REMOTE_ROOT}/alk-venv/bin/python "
            f"-e {REMOTE_ROOT}/branch"
        )
        if _sandbox_command(sandbox, bootstrap, timeout=1200):
            raise RuntimeError("sandbox_dependencies")
        runner = (
            f"{REMOTE_ROOT}/alk-venv/bin/python "
            f"{REMOTE_ROOT}/branch/examples/harness/"
            "sse_transport_compatibility/e2b_cases.py "
            f"--output {REMOTE_ROOT}/output/report.json "
            f"--work {REMOTE_ROOT}/frameworks"
        )
        remaining = timeout_seconds - int(time.monotonic() - sandbox_started) - 60
        if remaining < 300:
            raise RuntimeError("sandbox_setup_exhausted_timeout")
        _sandbox_command(
            sandbox,
            runner,
            timeout=remaining,
            envs={
                "ALK_BRANCH_ROOT": f"{REMOTE_ROOT}/branch",
                "EXPECTED_ALK_SSE_SHA256": expected_hash,
                "GOOGLE_APPLICATION_CREDENTIALS": f"{REMOTE_ROOT}/secrets/vertex.json",
                "PYTHONPATH": f"{REMOTE_ROOT}/branch/src",
                "VERTEXAI_PROJECT": project,
            },
        )
        raw = sandbox.files.read(
            f"{REMOTE_ROOT}/output/report.json",
            format="bytes",
            user="root",
            request_timeout=60,
        )
        raw_report = bytes(raw)
    finally:
        try:
            sandbox.files.remove(f"{REMOTE_ROOT}/secrets/vertex.json", user="root")
        except Exception:
            pass
        try:
            sandbox.kill()
            cleanup_succeeded = True
            list_sandboxes = getattr(type(sandbox), "list", None)
            if callable(list_sandboxes):
                time.sleep(2)
                active = list_sandboxes(api_key=api_key, limit=100).next_items()
                cleanup_succeeded = sandbox_id not in {
                    str(item.sandbox_id) for item in active
                }
        except Exception:
            cleanup_succeeded = False

    if raw_report is None:
        raise RuntimeError("sandbox_report_missing")
    report = validate_report(raw_report, expected_hash)
    report["sandbox"] = {
        "id": sandbox_id,
        "template": template,
        "cleanup_succeeded": cleanup_succeeded,
    }
    report["passed"] = bool(report["passed"] and cleanup_succeeded)
    body = json.dumps(report, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=output.parent, delete=False
    ) as temporary:
        temporary.write(body)
        temporary_path = Path(temporary.name)
    os.chmod(temporary_path, 0o600)
    temporary_path.replace(output)
    return 0 if report["passed"] else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--credential",
        type=Path,
        default=(
            Path(os.environ["GOOGLE_APPLICATION_CREDENTIALS"])
            if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
            else None
        ),
    )
    parser.add_argument(
        "--template",
        default=os.environ.get("ALK_E2B_TEMPLATE_REFERENCE", "base"),
    )
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    args = parser.parse_args()
    if args.credential is None:
        parser.error(
            "--credential or GOOGLE_APPLICATION_CREDENTIALS is required"
        )
    if not 600 <= args.timeout_seconds <= 3600:
        parser.error("--timeout-seconds must be between 600 and 3600")
    raise SystemExit(
        qualify(
            credential=args.credential,
            output=args.output,
            template=args.template,
            timeout_seconds=args.timeout_seconds,
        )
    )


if __name__ == "__main__":
    main()
