"""Run mode (`hosted --auto`): one model call drafts the binding, code holds it to the pairing
the person reviewed, proves it and seals it. A scenario whose proof still fails after the retry
fails the adaptation; it is never relabelled unscorable.

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
from fi.alk.harness.adaptation.errors import AdaptationFailure
from fi.alk.harness.adaptation.profile import profile_from_file
from fi.alk.harness.adaptation.proofs import BindingVerdict
from fi.alk.harness.adaptation.workbench import (
    RESULT,
    Workbench,
    frozen_pairing,
    pairing_problems,
)
from fi.alk.harness.bindings import MappedToolBinding, UnsupportedToolBinding
from fi.alk.harness.releases import BindingMode, ToolDefinition

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
        self.pairing: Any = None

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

    def freeze_pairing(self, pairing):
        self.pairing = dict(pairing)

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
PAIRING = {"lock_card": "block_card", "send_sms": None}


def test_a_refused_binding_is_retried_once_with_the_refusal(monkeypatch):
    seen = _model(monkeypatch, DRAFT, DRAFT)
    bench = Bench(
        submits=[(False, "tools: block_card is missing"), (True, "accepted")],
        proofs=[(True, {"S01": PROVED})],
    )
    asyncio.run(auto.adapt(bench, PAIRING))
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
    asyncio.run(auto.adapt(bench, PAIRING))
    assert bench.failed == ("binding_invalid", "still bad") and not bench.sealed


def test_a_scenario_still_failing_after_the_retry_fails_the_adaptation(monkeypatch):
    """One scenario proved and one failed is not a smaller score: the mapping did not achieve
    the scenario, so nothing is sealed and no verdict is rewritten."""
    seen = _model(monkeypatch, DRAFT, DRAFT)
    failing = {"S01": PROVED, "S02": FAILED}
    bench = Bench(
        submits=[(True, "accepted"), (True, "accepted")],
        proofs=[(False, failing), (False, failing)],
    )
    asyncio.run(auto.adapt(bench, PAIRING))
    assert "S02: failed: grace_booked did not hold" in seen[1][-1]["content"]
    assert "S01" not in seen[1][-1]["content"]
    assert not bench.sealed
    code, message = bench.failed
    assert code == "binding_unproved" and "S02: failed" in message
    assert bench.verdicts["S02"].status == "failed"


def test_a_failed_proof_fails_even_when_the_retry_cannot_be_asked(monkeypatch):
    _model(monkeypatch, DRAFT, RuntimeError("gateway down"))
    bench = Bench(
        submits=[(True, "accepted")], proofs=[(False, {"S01": PROVED, "S02": FAILED})]
    )
    asyncio.run(auto.adapt(bench, PAIRING))
    assert bench.failed[0] == "binding_unproved" and not bench.sealed


def test_run_mode_without_the_reviewed_pairing_binds_nothing(monkeypatch):
    seen = _model(monkeypatch, DRAFT)
    bench = Bench(submits=[(True, "accepted")], proofs=[(True, {"S01": PROVED})])
    with pytest.raises(AdaptationFailure) as refused:
        asyncio.run(auto.adapt(bench))
    assert refused.value.code == "adaptation_pairing_missing"
    assert seen == [] and bench.submitted == [] and not bench.sealed


def test_the_pairing_is_frozen_before_the_model_is_asked(monkeypatch):
    seen = _model(monkeypatch, DRAFT)
    bench = Bench(submits=[(True, "accepted")], proofs=[(True, {"S01": PROVED})])
    asyncio.run(auto.adapt(bench, PAIRING))
    assert bench.pairing == PAIRING and bench.sealed
    assert '"send_sms": null' in seen[0][1]["content"]


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
    asyncio.run(auto.adapt(bench, PAIRING))
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


# --- the reviewed pairing, held in code ---------------------------------------------------------

LOCK = ToolDefinition(
    name="lock_card",
    description="Lock a card.",
    parameters={"type": "object", "properties": {"card_id": {"type": "string"}}},
    mutates_state=True,
)
FIND = ToolDefinition(
    name="find_card",
    description="Find a card.",
    parameters={"type": "object", "properties": {"last4": {"type": "string"}}},
    mutates_state=False,
)
CLOSE = ToolDefinition(
    name="close_account",
    description="Close the account.",
    parameters={"type": "object", "properties": {}},
    mutates_state=True,
)
WORLD = [LOCK, FIND, CLOSE]
AGENT = ["block_card", "card_lookup", "send_sms_alert"]


def _entry(agent_tool: str, *steps: str):
    return MappedToolBinding.model_validate(
        {
            "agent_tool": agent_tool,
            "steps": [
                {"id": f"s{number}", "tool": tool, "arguments": {}}
                for number, tool in enumerate(steps, start=1)
            ],
        }
    )


def _unsupported(agent_tool: str):
    return UnsupportedToolBinding.model_validate(
        {"agent_tool": agent_tool, "unsupported": "the agent has no such channel"}
    )


def _bound(*entries) -> SimpleNamespace:
    return SimpleNamespace(tools=list(entries))


def test_a_pairing_is_completed_and_names_only_tools_each_side_has():
    assert frozen_pairing({"lock_card": "block_card"}, WORLD, AGENT) == {
        "lock_card": "block_card",
        "find_card": None,
        "close_account": None,
    }
    for pairing, said in (
        ({"freeze_card": "block_card"}, "'freeze_card' is not an environment tool"),
        ({"lock_card": "blok_card"}, "'blok_card' is not one of the agent's admitted"),
        (["lock_card"], "must be an object"),
    ):
        with pytest.raises(AdaptationFailure) as refused:
            frozen_pairing(pairing, WORLD, AGENT)
        assert refused.value.code == "adaptation_pairing_invalid"
        assert said in refused.value.message


def test_a_binding_that_is_the_reviewed_pairing_is_taken():
    pairing = frozen_pairing(
        {"lock_card": "block_card", "find_card": "card_lookup"}, WORLD, AGENT
    )
    binding = _bound(
        # A step that only reads may look a value up before the paired call.
        _entry("block_card", "find_card", "lock_card"),
        _entry("card_lookup", "find_card"),
        _unsupported("send_sms_alert"),
    )
    assert pairing_problems(binding, pairing, WORLD) == []


@pytest.mark.parametrize(
    "entries, said",
    [
        # Another valid mapping than the one reviewed: the paired tool is never called.
        (
            [_entry("block_card", "find_card"), _unsupported("card_lookup")],
            "block_card: paired with lock_card, which no step calls",
        ),
        # A tool the person paired with nothing is mapped anyway.
        (
            [_entry("block_card", "lock_card"), _entry("card_lookup", "find_card")],
            "card_lookup: the reviewed pairing gives it no environment tool",
        ),
        # A paired tool is dropped as unsupported.
        (
            [_unsupported("block_card"), _unsupported("card_lookup")],
            "block_card: the reviewed pairing maps it onto lock_card",
        ),
        # The paired call is there, and so is a write nobody reviewed.
        (
            [
                _entry("block_card", "lock_card", "close_account"),
                _unsupported("card_lookup"),
            ],
            "block_card: a step changes state through close_account",
        ),
    ],
    ids=["another-target", "unpaired-mapped", "paired-unsupported", "unreviewed-write"],
)
def test_a_binding_that_is_not_the_reviewed_pairing_is_refused(entries, said):
    pairing = frozen_pairing({"lock_card": "block_card"}, WORLD, AGENT)
    problems = pairing_problems(_bound(*entries), pairing, WORLD)
    assert any(said in one for one in problems), problems


# --- the approval data -------------------------------------------


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
        "passed": True,
        "scenarios": {
            "S01": {
                "status": "proved",
                "reason": "",
                "adversarial": {"no_calls": "failed"},
            },
            "S02": {
                "status": "proved",
                "reason": "",
                "adversarial": {"no_calls": "failed"},
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


def test_the_approval_data_is_the_question_as_data(tmp_path):
    workbench = _bench(tmp_path)
    data = workbench.approval_data()
    assert data == {
        "mapped": [{"agent_tool": "block_card", "steps": ["lock_card"]}],
        "unsupported": [{"agent_tool": "send_sms_alert", "reason": "no SMS channel"}],
        # Only the scenario no route of which the binding reaches.
        "unscorable": [{"scenario_key": "S03", "reason": "uses send_sms"}],
        "conflicts": [
            {
                "rule": "confirm_before_lock",
                "policy": "Confirm first.",
                "agent": "at once",
            }
        ],
        "total": 3,
        "scorable": 2,
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


def _pairing(mapping: dict[str, Any]) -> dict[str, str]:
    """The pairing a person would have reviewed for this mapping: each environment tool an
    entry's steps call, onto that entry's agent tool."""
    return {
        step["tool"]: entry["agent_tool"]
        for entry in mapping["tools"]
        if not entry.get("unsupported")
        for step in entry["steps"]
    }


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
    asyncio.run(auto.adapt(workbench, _pairing(mapping)))
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
def test_a_written_trajectory_never_replaces_one_code_derives(tmp_path, monkeypatch):
    """The model writes an empty trajectory for every scenario. Code derives each from the
    reference solution, so the derived ones are proved and the written ones are set aside:
    a bad draft can neither shrink the score nor fail what the binding does achieve."""
    workbench, mapping = _banking(tmp_path)
    nothing = {entry.key: [] for entry in workbench.base_suite.scenarios}
    _model(monkeypatch, {"binding": mapping, "trajectories": nothing, "conflicts": []})
    asyncio.run(auto.adapt(workbench, _pairing(mapping)))
    result = _result(workbench)
    assert result["status"] == "completed", result["failure"]
    assert result["alignment"]["unscorable"] == []
    proof = json.loads((workbench.output / "binding-proof.json").read_text())
    assert proof["passed"] is True
    assert {one["status"] for one in proof["scenarios"].values()} == {"proved"}
    trajectories = json.loads((workbench.output / "trajectories.json").read_text())
    assert all(trajectories[key] for key in nothing)


@needs_releases
def test_a_binding_that_is_not_the_reviewed_pairing_is_never_proved(
    tmp_path, monkeypatch
):
    """The person unpaired one tool the model maps anyway, in both answers."""
    workbench, mapping = _banking(tmp_path)
    pairing = _pairing(mapping)
    dropped = next(iter(pairing))
    answer = {"binding": mapping, "trajectories": {}, "conflicts": []}
    _model(monkeypatch, answer, answer)
    asyncio.run(auto.adapt(workbench, {**pairing, dropped: None}))
    result = _result(workbench)
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "binding_invalid"
    assert "not the reviewed pairing" in result["failure"]["message"]
    assert not (workbench.output / "binding-proof.json").exists()
