import asyncio
import importlib.util
import json
import sys
from types import ModuleType
from pathlib import Path

import pytest


ROOT = (
    Path(__file__).resolve().parents[2] / "examples/harness/sse_transport_compatibility"
)


def _module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _passing_report(qualifier, digest="a" * 64):
    receipts = []
    for case_id in qualifier.CASE_IDS:
        framework = qualifier.FRAMEWORKS.get(case_id)
        source = None
        if framework:
            subdirectories = {
                "crewai": "integrations/crew-ai/python",
                "langgraph": "integrations/langgraph/python",
                "google_adk": "integrations/adk-middleware/python",
            }
            source = {
                "repository": "https://github.com/ag-ui-protocol/ag-ui.git",
                "commit": "2b967c34c49759c4afe0f0a176b281a0f645de34",
                "subdirectory": subdirectories[framework],
                "framework": framework,
                "model": qualifier.SOURCE_MODELS[framework],
                "endpoint": qualifier.SOURCE_ENDPOINTS[framework],
                "provider_patch_sha256": qualifier.PROVIDER_PATCH_SHA256,
                "versions": dict(qualifier.SOURCE_VERSIONS[framework]),
            }
        receipts.append(
            {
                "case_id": case_id,
                "status": "passed",
                "reason": "ok",
                "framework": framework,
                "response_characters": 5,
                "stream_events": 3,
                "terminal": "RUN_FINISHED",
                "source": source,
            }
        )
    return {
        "schema_version": "futureagi.sse-e2b-qualification.v1",
        "provenance": {
            "expected_sha256": digest,
            "imported_sha256": digest,
            "imported_from_branch": True,
        },
        "receipts": receipts,
        "passed": True,
    }


def test_report_requires_exactly_ten_ordered_receipts_and_branch_hash():
    qualifier = _module("sse_e2b_qualify", "e2b_qualify.py")
    report = _passing_report(qualifier)
    assert qualifier.validate_report(json.dumps(report).encode(), "a" * 64) == report

    report["receipts"].pop()
    with pytest.raises(ValueError, match="exactly ten"):
        qualifier.validate_report(json.dumps(report).encode(), "a" * 64)


def test_retained_report_matches_the_current_sse_module():
    qualifier = _module("sse_e2b_qualify_retained", "e2b_qualify.py")
    raw = json.loads(
        (ROOT / "results/e2b-2026-11-10.json").read_text(encoding="utf-8")
    )
    sandbox = raw.pop("sandbox")
    validated = qualifier.validate_report(
        json.dumps(raw).encode(), qualifier._sha256(qualifier.SSE_MODULE)
    )
    assert validated["passed"] is True
    assert sandbox == {
        "id": "redacted",
        "template": "redacted",
        "cleanup_succeeded": True,
    }

    report = _passing_report(qualifier)
    report["provenance"]["imported_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="different ALK"):
        qualifier.validate_report(json.dumps(report).encode(), "a" * 64)

    report = _passing_report(qualifier)
    report["receipts"][0]["source"]["unexpected"] = "not allowlisted"
    with pytest.raises(ValueError, match="source provenance"):
        qualifier.validate_report(json.dumps(report).encode(), "a" * 64)

    report = _passing_report(qualifier)
    report["receipts"][0]["source"]["versions"]["crewai"] = "999"
    with pytest.raises(ValueError, match="versions"):
        qualifier.validate_report(json.dumps(report).encode(), "a" * 64)


def test_failed_and_withheld_framework_receipts_remain_valid():
    qualifier = _module("sse_e2b_qualify_failures", "e2b_qualify.py")
    cases = _module("sse_e2b_cases_failures", "e2b_cases.py")
    failed = cases._receipt(
        "crewai_continuity",
        passed=False,
        reason="continuity_not_observed",
        framework="crewai",
        source={"must": "not leak"},
    )
    assert failed.source is None

    report = _passing_report(qualifier)
    for index, case_id in enumerate(qualifier.CASE_IDS[:3]):
        receipt = cases._withheld(case_id)
        report["receipts"][index] = {
            **receipt.__dict__,
        }
    report["passed"] = False
    assert (
        qualifier.validate_report(json.dumps(report).encode(), "a" * 64)["passed"]
        is False
    )


def test_controller_fails_before_e2b_without_key(tmp_path):
    qualifier = _module("sse_e2b_qualify_missing_key", "e2b_qualify.py")
    credential = tmp_path / "vertex.json"
    credential.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="E2B_API_KEY"):
        qualifier._validate_inputs(
            {"VERTEXAI_PROJECT": "project"}, credential, tmp_path / "report.json"
        )


def test_sandbox_command_returns_nonzero_case_exit(monkeypatch):
    qualifier = _module("sse_e2b_qualify_command_exit", "e2b_qualify.py")

    class CommandExitException(Exception):
        exit_code = 7

    class Commands:
        def run(self, *_args, **_kwargs):
            raise CommandExitException

    e2b = ModuleType("e2b")
    e2b.CommandExitException = CommandExitException
    monkeypatch.setitem(sys.modules, "e2b", e2b)
    sandbox = type("Sandbox", (), {"commands": Commands()})()
    assert qualifier._sandbox_command(sandbox, "runner", timeout=1) == 7


def test_controller_writes_report_only_after_sandbox_cleanup(tmp_path, monkeypatch):
    qualifier = _module("sse_e2b_qualify_lifecycle", "e2b_qualify.py")
    monkeypatch.setattr(qualifier.time, "sleep", lambda _seconds: None)
    digest = qualifier._sha256(qualifier.SSE_MODULE)
    remote_report = json.dumps(_passing_report(qualifier, digest)).encode()

    class Commands:
        def run(self, *_args, **_kwargs):
            return type("Result", (), {"exit_code": 0})()

    class Files:
        def __init__(self):
            self.removed = []

        def write(self, *_args, **_kwargs):
            return None

        def read(self, *_args, **_kwargs):
            return remote_report

        def remove(self, path, **_kwargs):
            self.removed.append(path)

    class Sandbox:
        sandbox_id = "sandbox-1"

        def __init__(self):
            self.commands = Commands()
            self.files = Files()
            self.killed = False

        def kill(self):
            self.killed = True

        @classmethod
        def list(cls, **_kwargs):
            return type("Paginator", (), {"next_items": lambda self: []})()

    sandbox = Sandbox()
    credential = tmp_path / "vertex.json"
    credential.write_text("{}", encoding="utf-8")
    output = tmp_path / "report.json"
    assert (
        qualifier.qualify(
            credential=credential,
            output=output,
            template="base",
            timeout_seconds=600,
            env={"E2B_API_KEY": "secret", "VERTEXAI_PROJECT": "project"},
            sandbox_factory=lambda **_kwargs: sandbox,
        )
        == 0
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert "secret" not in output.read_text(encoding="utf-8")
    assert report["sandbox"] == {
        "id": "sandbox-1",
        "template": "base",
        "cleanup_succeeded": True,
    }
    assert sandbox.killed is True
    assert sandbox.files.removed == ["/work/sse-qualification/secrets/vertex.json"]
    assert output.stat().st_mode & 0o777 == 0o600


def test_seven_transport_cases_observe_http_wrapper_behavior():
    cases = _module("sse_e2b_cases", "e2b_cases.py")
    receipts = asyncio.run(cases.run_transport_cases())
    assert [receipt.case_id for receipt in receipts] == list(cases.CASE_IDS[3:])
    assert all(receipt.status == "passed" for receipt in receipts)


def test_framework_wrapper_accepts_delta_and_snapshot_text():
    cases = _module("sse_e2b_cases_framework_mapping", "e2b_cases.py")
    wrapper = cases._agui_wrapper("http://127.0.0.1:1/agent")
    assert [rule.mode for rule in wrapper.sse.text] == ["delta", "cumulative"]


def test_runner_always_writes_ten_receipts_on_unexpected_case_error(
    tmp_path, monkeypatch
):
    cases = _module("sse_e2b_cases_errors", "e2b_cases.py")
    digest = "a" * 64
    monkeypatch.setattr(
        cases,
        "imported_provenance",
        lambda: {
            "expected_sha256": digest,
            "imported_sha256": digest,
            "imported_from_branch": True,
        },
    )

    async def fail(*_args, **_kwargs):
        raise RuntimeError("must not escape")

    monkeypatch.setattr(cases, "run_transport_cases", fail)
    monkeypatch.setattr(cases, "run_framework_cases", fail)
    output = tmp_path / "report.json"
    assert asyncio.run(cases.qualify(output, tmp_path / "work")) == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert [receipt["case_id"] for receipt in report["receipts"]] == list(
        cases.CASE_IDS
    )
    assert all(
        receipt["status"] == "failed" and receipt["reason"] == "runner_exception"
        for receipt in report["receipts"]
    )
