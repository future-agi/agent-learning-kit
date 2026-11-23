"""Run mode (`hosted --auto`): one model call drafts the binding, code proves and seals it, and a
scenario whose proof still fails is left out of the score instead of blocking the seal.

The flow, the workbench's run-mode gate and the hosted control process run offline with the model
mocked. The end-to-end cases bind CardGuard to Banking on PostgreSQL, like
`test_adaptation_verticals.py`, and run only when both are named:

    ALK_ADAPTATION_RELEASES=<dir with banking/environment and banking/suite> \\
    ALK_RELEASE_POSTGRES_DSN=postgresql://... pytest tests/harness/test_adaptation_auto.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adaptation_fixtures as fixtures

from fi.alk.harness import spend
from fi.alk.harness.adaptation import auto, hosted
from fi.alk.harness.adaptation.profile import profile_from_file
from fi.alk.harness.adaptation.proofs import BindingVerdict
from fi.alk.harness.adaptation.workbench import RESULT, STATE, Workbench
from fi.alk.harness.bindings import MappedToolBinding, UnsupportedToolBinding
from fi.alk.harness.releases import BindingMode

SHA = "sha256:" + "ab" * 32
RELEASES = os.environ.get("ALK_ADAPTATION_RELEASES", "")
DSN = os.environ.get("ALK_RELEASE_POSTGRES_DSN", "")
needs_releases = pytest.mark.skipif(
    not (RELEASES and DSN),
    reason="set ALK_ADAPTATION_RELEASES and ALK_RELEASE_POSTGRES_DSN",
)


def _model(monkeypatch, *answers: Any) -> list[list[dict[str, str]]]:
    """The model call, mocked: each call returns the next answer (fenced, as models do)."""
    seen: list[list[dict[str, str]]] = []
    queue = list(answers)

    async def complete(messages):
        seen.append([dict(one) for one in messages])
        answer = queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return f"```json\n{json.dumps(answer)}\n```"

    monkeypatch.setattr(auto, "_complete", complete)
    return seen


# --- the flow, over a stand-in workbench ------------------------------------------------------


class Bench:
    """The workbench's surface `auto.adapt` uses, scripted."""

    def __init__(self, *, submits=(), proofs=(), mode=BindingMode.TOOL_ROUTED):
        self.mode = mode
        self.submits = list(submits)
        self.proofs = list(proofs)
        self.policy = SimpleNamespace(rules=[SimpleNamespace(id="confirm_before_lock")])
        self.profile = SimpleNamespace(
            target={"instructions": fixtures.CARDGUARD_INSTRUCTIONS}
        )
        self.submitted: list[Any] = []
        self.verdicts: dict[str, BindingVerdict] = {}
        self.conflicts: Any = None
        self.failed: tuple[str, str] | None = None
        self.answer = ""
        self.sealed = False

    def brief(self) -> str:
        return "THE BRIEF"

    def submit_binding(self, body):
        self.submitted.append(body)
        return self.submits.pop(0)

    def run_proof(self):
        passed, verdicts = self.proofs.pop(0)
        self.verdicts = verdicts
        lines = [f"{key}: {one.status}: {one.reason}" for key, one in verdicts.items()]
        return passed, ("Proved. " if passed else "Not proved. ") + "\n".join(lines)

    def leave_failed_unscorable(self):
        self.verdicts = {
            key: BindingVerdict(
                "unscorable", f"the mapping failed the check: {one.reason}"
            )
            if one.status == "failed"
            else one
            for key, one in self.verdicts.items()
        }

    def report_conflicts(self, items):
        self.conflicts = items
        return True, ""

    async def finish(self, approve):
        self.answer = await approve("Use this binding?", ["yes", "no"])
        self.sealed = True
        return True, "Sealed."

    def fail(self, code, message):
        self.failed = (code, message)

    def stop_code(self):
        return "binding_unproved"


PROVED = BindingVerdict("proved", "")
FAILED = BindingVerdict("failed", "grace_booked did not hold")
DRAFT = {"binding": {"tools": []}, "conflicts": []}


def test_a_refused_binding_is_retried_once_with_the_refusal(monkeypatch):
    seen = _model(monkeypatch, DRAFT, DRAFT)
    bench = Bench(
        submits=[(False, "tools: block_card is missing"), (True, "accepted")],
        proofs=[(True, {"S01": PROVED})],
    )
    asyncio.run(auto.adapt(bench, {"lock_card": "block_card", "send_sms": None}))
    assert bench.sealed and bench.answer == "yes" and bench.failed is None
    first, retry = seen
    assert '"lock_card": "block_card"' in first[1]["content"]
    assert "The mapping language" in first[0]["content"]
    # The same conversation, with code's refusal as the next turn.
    assert retry[:2] == first[:2] and retry[2]["role"] == "assistant"
    assert "block_card is missing" in retry[3]["content"]


def test_a_binding_refused_twice_fails_as_invalid(monkeypatch):
    _model(monkeypatch, DRAFT, DRAFT)
    bench = Bench(submits=[(False, "bad"), (False, "still bad")])
    asyncio.run(auto.adapt(bench))
    assert bench.failed == ("binding_invalid", "still bad") and not bench.sealed


def test_a_scenario_still_failing_after_the_retry_is_left_unscorable(monkeypatch):
    seen = _model(monkeypatch, DRAFT, DRAFT)
    failing = {"S01": PROVED, "S02": FAILED}
    bench = Bench(
        submits=[(True, "accepted"), (True, "accepted")],
        proofs=[(False, failing), (False, failing)],
    )
    asyncio.run(auto.adapt(bench))
    assert "S02: failed: grace_booked did not hold" in seen[1][-1]["content"]
    assert "S01" not in seen[1][-1]["content"]
    assert bench.sealed and bench.failed is None
    assert bench.verdicts["S02"].status == "unscorable"


def test_nothing_proved_fails_as_unproved(monkeypatch):
    _model(monkeypatch, DRAFT, RuntimeError("gateway down"))
    bench = Bench(
        submits=[(True, "accepted")], proofs=[(False, {"S01": FAILED, "S02": FAILED})]
    )
    asyncio.run(auto.adapt(bench))
    assert bench.failed[0] == "binding_unproved" and not bench.sealed


def test_conflicts_that_are_not_quoted_or_not_rules_are_dropped(monkeypatch):
    conflicts = [
        # Quoted with other spacing and case: kept.
        {
            "rule": "confirm_before_lock",
            "agent": "Block it at once;   do not wait for them to confirm",
        },
        {"rule": "confirm_before_lock", "agent": "never asks anyone"},
        {"rule": "no_such_rule", "agent": "block it at once"},
        "not an object",
    ]
    _model(monkeypatch, {**DRAFT, "conflicts": conflicts})
    bench = Bench(submits=[(True, "accepted")], proofs=[(True, {"S01": PROVED})])
    asyncio.run(auto.adapt(bench))
    assert bench.conflicts == [conflicts[0]]


def test_without_a_binding_only_conflicts_are_asked_and_a_failed_call_reports_none(
    monkeypatch,
):
    seen = _model(monkeypatch, RuntimeError("gateway down"))
    bench = Bench(mode=BindingMode.CANONICAL)
    asyncio.run(auto.adapt(bench))
    assert bench.conflicts == [] and bench.sealed and bench.submitted == []
    assert '"conflicts"' in seen[0][0]["content"]


def test_the_model_call_is_one_json_completion_through_the_gateway(monkeypatch):
    calls: list[dict[str, Any]] = []

    class Completions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                usage=None,
                choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
            )

    class Client:
        def __init__(self, *, base_url, api_key):
            calls.append({"base_url": base_url, "api_key": api_key})
            self.chat = SimpleNamespace(completions=Completions())

    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", Client)
    monkeypatch.setenv("AGENTCC_API_KEY", "test-key")
    monkeypatch.setenv("AGENTCC_BASE_URL", "http://gateway.test")
    monkeypatch.setenv("ALK_HARNESS_MODEL", "gemini-3.7-flash")
    assert asyncio.run(auto._complete([{"role": "user", "content": "x"}])) == "{}"
    client, request = calls
    assert client == {"base_url": "http://gateway.test/v1", "api_key": "test-key"}
    assert request["model"] == "vertex_ai/gemini-3.7-flash"
    assert request["temperature"] == 0
    assert request["response_format"] == {"type": "json_object"}


# --- the workbench's run-mode gate and approval data -------------------------------------------


def _bench(tmp_path: Path) -> Workbench:
    """A workbench past its proof, without releases: only what the gate and the review read."""
    workbench = Workbench.__new__(Workbench)
    workbench.output = tmp_path
    workbench.mode = BindingMode.TOOL_ROUTED
    workbench.binding = SimpleNamespace(
        digest=SHA,
        tools=[
            MappedToolBinding.model_validate(
                {
                    "agent_tool": "block_card",
                    "steps": [
                        {
                            "id": "call",
                            "tool": "lock_card",
                            "arguments": {"card_id": {"arg": "card"}},
                        }
                    ],
                }
            ),
            UnsupportedToolBinding.model_validate(
                {"agent_tool": "send_sms_alert", "unsupported": "no SMS channel"}
            ),
        ],
    )
    workbench.draft = None
    workbench.base_suite = SimpleNamespace(scenarios=[1, 2, 3])
    workbench.conflicts = [
        {"rule": "confirm_before_lock", "policy": "Confirm first.", "agent": "at once"}
    ]
    workbench.declined = ""
    ref = {"digest": SHA}
    workbench.proof = {
        "schema_version": "futureagi.binding-proof.v1",
        "binding": ref,
        "suite": ref,
        "environment": ref,
        "passed": False,
        "scenarios": {
            "S01": {
                "status": "proved",
                "reason": "",
                "adversarial": {"no_calls": "failed"},
            },
            "S02": {
                "status": "failed",
                "reason": "adversarial variants held: no_calls",
                "adversarial": {"no_calls": "held"},
            },
            "S03": {
                "status": "unscorable",
                "reason": "uses send_sms",
                "adversarial": {},
            },
        },
    }
    from fi.alk.harness.adaptation.proofs import binding_verdicts
    from fi.alk.harness.binding_proof import BindingProofV1

    workbench.verdicts = binding_verdicts(
        BindingProofV1.model_validate(workbench.proof)
    )
    return workbench


def test_a_failed_scenario_becomes_unscorable_and_the_proof_passes(tmp_path):
    workbench = _bench(tmp_path)
    assert not workbench._proved(SHA)
    workbench.leave_failed_unscorable()
    assert workbench._proved(SHA)
    assert workbench.proof["passed"] is True
    assert workbench.proof["scenarios"]["S02"] == {
        "status": "unscorable",
        "reason": "the mapping failed the check: adversarial variants held: no_calls",
        "adversarial": {},
    }
    assert workbench.verdicts["S02"].status == "unscorable"
    # Saved, so a reopened workbench restores the same proof.
    saved = json.loads((tmp_path / STATE).read_text(encoding="utf-8"))
    assert saved["proof"] == workbench.proof


def test_the_approval_data_is_the_question_as_data(tmp_path):
    workbench = _bench(tmp_path)
    workbench.leave_failed_unscorable()
    data = workbench.approval_data()
    assert data == {
        "mapped": [{"agent_tool": "block_card", "steps": ["lock_card"]}],
        "unsupported": [{"agent_tool": "send_sms_alert", "reason": "no SMS channel"}],
        "unscorable": [
            {
                "scenario_key": "S02",
                "reason": "the mapping failed the check: adversarial variants held: no_calls",
            },
            {"scenario_key": "S03", "reason": "uses send_sms"},
        ],
        "conflicts": [
            {
                "rule": "confirm_before_lock",
                "policy": "Confirm first.",
                "agent": "at once",
            }
        ],
        "total": 3,
        "scorable": 1,
    }
    assert json.loads(json.dumps(data)) == data


# --- the hosted control process ----------------------------------------------------------------


def test_hosted_auto_binds_in_process_and_never_waits_for_a_builder(
    tmp_path, monkeypatch
):
    from fi.alk.harness import job as job_module
    from fi.alk.harness.adaptation import profile as profile_module

    pairing = {"lock_card": "block_card"}
    job_path = tmp_path / "job.json"
    job_path.write_text(
        json.dumps({"metadata": {"adaptation": {"mode": "run", "pairing": pairing}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        job_module.HarnessJob,
        "model_validate_json",
        classmethod(lambda cls, text: SimpleNamespace(**json.loads(text))),
    )
    monkeypatch.setattr(
        hosted,
        "_environments",
        lambda job, releases: ([tmp_path / "environment"], tmp_path / "suite"),
    )
    monkeypatch.setattr(
        profile_module,
        "profile_from_job",
        lambda job, credentials: SimpleNamespace(document=lambda: {"agent": {}}),
    )

    def never(_output):
        raise AssertionError("run mode must not wait for a builder")

    monkeypatch.setattr(hosted, "_await_result", never)
    built: dict[str, Any] = {}

    class FakeWorkbench:
        def __init__(self, **kwargs):
            built.update(kwargs)

        def fail(self, code, message):
            raise AssertionError(f"{code}: {message}")

    async def adapt(workbench, given):
        built["pairing"] = given
        (built["output"] / RESULT).write_text('{"status": "completed"}')
        built["finished_marker"].write_text('{"status": "completed"}')

    monkeypatch.setattr(hosted, "Workbench", FakeWorkbench)
    monkeypatch.setattr(auto, "adapt", adapt)
    output = tmp_path / "authoring" / "adaptation"
    args = argparse.Namespace(
        job=str(job_path),
        releases=str(tmp_path),
        output=str(output),
        postgres_dsn="postgresql://nowhere/postgres",
        auto=True,
    )
    try:
        assert hosted.run_hosted(args) == 0
    finally:
        spend.journal_to(None)
    assert built["pairing"] == pairing
    assert built["postgres_dsn"] == "postgresql://nowhere/postgres"
    assert built["finished_marker"] == output.parent / hosted.FINISHED_MARKER
    assert not (output / "inputs.json").exists()


def test_the_hosted_command_takes_auto():
    from fi.alk.harness.adaptation.__main__ import _parser

    args = _parser().parse_args(
        ["hosted", "--job", "j", "--releases", "r", "--output", "o", "--auto"]
    )
    assert args.auto is True


# --- end to end: CardGuard on Banking ----------------------------------------------------------


def _banking(tmp_path: Path) -> tuple[Workbench, dict[str, Any]]:
    release = Path(RELEASES) / "banking"
    profile, mapping = fixtures.cardguard(release / "environment")
    workbench = Workbench(
        environments=[release / "environment"],
        suite_root=release / "suite",
        output=tmp_path / "adaptation",
        postgres_dsn=DSN,
        profile=profile_from_file(profile),
        finished_marker=tmp_path / hosted.FINISHED_MARKER,
    )
    return workbench, mapping


def _result(workbench: Workbench) -> dict[str, Any]:
    return json.loads((workbench.output / RESULT).read_text(encoding="utf-8"))


@needs_releases
def test_cardguard_is_bound_in_run_mode_after_one_refusal(tmp_path, monkeypatch):
    workbench, mapping = _banking(tmp_path)
    conflicts = [
        *fixtures.CARDGUARD_CONFLICTS,
        {"rule": "confirm_before_lock", "agent": "x"},
    ]
    broken = {"tools": [{"agent_tool": "block_card", "steps": []}]}
    _model(
        monkeypatch,
        {"binding": broken, "trajectories": {}, "conflicts": []},
        {"binding": mapping, "trajectories": {}, "conflicts": conflicts},
    )
    asyncio.run(auto.adapt(workbench))
    result = _result(workbench)
    assert result["status"] == "completed", result["failure"]
    assert {one["rule"] for one in result["alignment"]["conflicts"]} == {
        "confirm_before_lock",
        "explain_pending_policy",
    }
    assert len(result["alignment"]["conflicts"]) == 2
    assert result["alignment"]["unscorable"] == []
    assert json.loads((tmp_path / hosted.FINISHED_MARKER).read_text()) == {
        "status": "completed"
    }


@needs_releases
def test_a_scenario_whose_proof_fails_is_left_out_and_the_rest_seals(
    tmp_path, monkeypatch
):
    workbench, mapping = _banking(tmp_path)
    key = workbench.base_suite.scenarios[0].key
    # The agent makes no call in this one scenario, in both answers: its checks cannot hold.
    answer = {"binding": mapping, "trajectories": {key: []}, "conflicts": []}
    _model(monkeypatch, answer, answer)
    asyncio.run(auto.adapt(workbench))
    result = _result(workbench)
    assert result["status"] == "completed", result["failure"]
    (left_out,) = result["alignment"]["unscorable"]
    assert left_out["scenario_key"] == key
    assert left_out["reason"].startswith("the mapping failed the check: ")
    proof = json.loads((workbench.output / "binding-proof.json").read_text())
    assert proof["passed"] is True and proof["scenarios"][key]["status"] == "unscorable"
    trajectories = json.loads((workbench.output / "trajectories.json").read_text())
    assert key not in trajectories and len(trajectories) == len(proof["scenarios"]) - 1


@needs_releases
def test_a_binding_that_proves_nothing_fails(tmp_path, monkeypatch):
    workbench, mapping = _banking(tmp_path)
    nothing = {entry.key: [] for entry in workbench.base_suite.scenarios}
    answer = {"binding": mapping, "trajectories": nothing, "conflicts": []}
    _model(monkeypatch, answer, answer)
    asyncio.run(auto.adapt(workbench))
    result = _result(workbench)
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "binding_unproved"
