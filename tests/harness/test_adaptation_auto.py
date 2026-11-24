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


PAIRING = {"lock_card": "block_card", "send_sms": None}


class Bench:
    """The workbench's surface `auto.adapt` uses, scripted."""

    def __init__(
        self, *, submits=(), proofs=(), mode=BindingMode.TOOL_ROUTED, pairing=PAIRING
    ):
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
        self.pairing: Any = pairing

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
    asyncio.run(auto.adapt(bench))
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


def test_a_scenario_still_failing_after_the_retry_fails_the_adaptation(monkeypatch):
    """One scenario proved and one failed is not a smaller score: the mapping did not achieve
    the scenario, so nothing is sealed and no verdict is rewritten."""
    seen = _model(monkeypatch, DRAFT, DRAFT)
    failing = {"S01": PROVED, "S02": FAILED}
    bench = Bench(
        submits=[(True, "accepted"), (True, "accepted")],
        proofs=[(False, failing), (False, failing)],
    )
    asyncio.run(auto.adapt(bench))
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
    asyncio.run(auto.adapt(bench))
    assert bench.failed[0] == "binding_unproved" and not bench.sealed


def test_run_mode_without_the_reviewed_pairing_binds_nothing(monkeypatch):
    seen = _model(monkeypatch, DRAFT)
    bench = Bench(
        submits=[(True, "accepted")], proofs=[(True, {"S01": PROVED})], pairing=None
    )
    with pytest.raises(AdaptationFailure) as refused:
        asyncio.run(auto.adapt(bench))
    assert refused.value.code == "adaptation_pairing_missing"
    assert seen == [] and bench.submitted == [] and not bench.sealed


def test_the_model_is_shown_the_complete_pairing(monkeypatch):
    seen = _model(monkeypatch, DRAFT)
    bench = Bench(submits=[(True, "accepted")], proofs=[(True, {"S01": PROVED})])
    asyncio.run(auto.adapt(bench))
    assert bench.sealed
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

    def never(*_arguments):
        raise AssertionError("run mode must not wait for a builder")

    monkeypatch.setattr(hosted, "_await_result", never)
    built: dict[str, Any] = {}

    class FakeWorkbench:
        def __init__(self, **kwargs):
            built.update(kwargs)

        binding = SimpleNamespace(digest=SHA)
        mode = BindingMode.TOOL_ROUTED

        def fail(self, code, message):
            raise AssertionError(f"{code}: {message}")

        def proof_ref(self):
            return {"digest": "sha256:" + "cd" * 32}

    async def adapt(workbench):
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
    # What this process sealed is recorded outside the workspace, from its own workbench.
    assert json.loads(hosted.verified_path(output.parent).read_text()) == {
        "schema_version": "futureagi.adaptation-verified.v1",
        "binding": {"digest": SHA, "mode": "tool_routed"},
        "binding_proof": {"digest": "sha256:" + "cd" * 32},
    }


def test_the_hosted_command_takes_auto():
    from fi.alk.harness.adaptation.__main__ import _parser

    args = _parser().parse_args(
        ["hosted", "--job", "j", "--releases", "r", "--output", "o", "--auto"]
    )
    assert args.auto is True


# --- review mode: the control process proves the builder's binding again --------------------------


def _left_by_the_builder(output: Path, proof: dict[str, Any]) -> None:
    """What a builder's session leaves, with the proof it claims."""
    output.mkdir(parents=True)
    (output / "binding.json").write_text("{}", encoding="utf-8")
    (output / "trajectories.json").write_text(
        json.dumps({"S01": [{"tool": "block_card", "arguments": {}}]}),
        encoding="utf-8",
    )
    (output / "binding-proof.json").write_text(json.dumps(proof), encoding="utf-8")
    (output / RESULT).write_text(
        json.dumps(
            {
                "status": "completed",
                "environment": [{"digest": SHA}],
                "binding": {"digest": SHA, "mode": "tool_routed"},
                "binding_proof": {"digest": "sha256:" + "ee" * 32},
                "alignment": {"mapped": [], "unscorable": []},
            }
        ),
        encoding="utf-8",
    )


def _control(monkeypatch, **scenarios: tuple[str, str]):
    """The control process's own proof of the sealed binding, scripted per scenario."""
    from fi.alk.harness import releases
    from fi.alk.harness.adaptation import proofs
    from fi.alk.harness.binding_proof import BindingProofV1

    ref = {"digest": SHA}
    proof = BindingProofV1(
        binding=ref,
        suite=ref,
        environment=ref,
        scenarios={
            key: {"status": status, "reason": reason}
            for key, (status, reason) in scenarios.items()
        },
        passed=all(status != "failed" for status, _ in scenarios.values()),
    )
    seen: dict[str, Any] = {}

    def prove(suite_root, binding, trajectories, *, environment_root, postgres_dsn):
        seen.update(suite=suite_root, trajectories=trajectories, dsn=postgres_dsn)
        return proof

    monkeypatch.setattr(
        releases,
        "load_agent_binding",
        lambda _path: SimpleNamespace(digest=SHA, mode=BindingMode.TOOL_ROUTED),
    )
    monkeypatch.setattr(proofs, "prove_binding_on", prove)
    seen["control"] = _own(BindingMode.TOOL_ROUTED)
    return proof, seen


def _own(mode: BindingMode, *, digest: str = SHA, refused: str = "") -> SimpleNamespace:
    """The control process's workbench: what it makes of the mapping the builder sealed."""
    control = SimpleNamespace(
        mode=mode, binding=SimpleNamespace(digest=digest), draft=None
    )

    def submit(body):
        control.draft = SimpleNamespace(
            trajectories={**body["trajectories"], "S02": []}
        )
        return not refused, refused or "accepted"

    control.submit_binding = submit
    return control


def test_the_proof_a_builder_left_is_replaced_by_the_control_processes_own(
    tmp_path, monkeypatch
):
    """The builder's session can write its whole output: here it left a proof with every
    scenario proved. What is sealed is the proof this process made of the same binding."""
    from fi.alk.harness.binding_proof import proof_digest

    output = tmp_path / "adaptation"
    claimed = {"passed": True, "scenarios": {"S01": "proved", "S02": "proved"}}
    _left_by_the_builder(output, claimed)
    proof, seen = _control(
        monkeypatch, S01=("proved", ""), S02=("unscorable", "uses send_sms")
    )

    result = hosted.prove_again(
        output, tmp_path / "environment", tmp_path, "dsn", seen["control"]
    )

    assert result["status"] == "completed"
    assert result["binding_proof"] == {"digest": proof_digest(proof)}
    assert result["alignment"]["unscorable"] == [
        {"scenario_key": "S02", "reason": "uses send_sms"}
    ]
    assert json.loads((output / RESULT).read_text()) == result
    assert json.loads((output / "binding-proof.json").read_text()) == proof.model_dump(
        mode="json"
    )
    # Proved from this process's own trajectories (the sealed ones, and those code derives over
    # them) against the released suite, nothing else.
    assert seen["suite"] == tmp_path and set(seen["trajectories"]) == {"S01", "S02"}
    assert set(json.loads((output / "trajectories.json").read_text())) == {"S01"}


def test_a_binding_the_control_process_cannot_prove_fails_whatever_was_left(
    tmp_path, monkeypatch
):
    output = tmp_path / "adaptation"
    _left_by_the_builder(output, {"passed": True, "scenarios": {"S01": "proved"}})
    _proof, seen = _control(
        monkeypatch, S01=("proved", ""), S02=("failed", "grace_booked did not hold")
    )

    result = hosted.prove_again(
        output, tmp_path / "environment", tmp_path, "dsn", seen["control"]
    )

    assert result["status"] == "failed"
    assert result["failure"]["code"] == "binding_unproved"
    assert "S02: failed: grace_booked did not hold" in result["failure"]["message"]
    assert json.loads((output / RESULT).read_text())["status"] == "failed"


def test_a_failed_result_is_left_as_it_is(tmp_path, monkeypatch):
    output = tmp_path / "adaptation"
    _left_by_the_builder(output, {})
    failed = {"status": "failed", "failure": {"code": "adaptation_declined"}}
    (output / RESULT).write_text(json.dumps(failed), encoding="utf-8")
    control = _own(BindingMode.TOOL_ROUTED)
    assert hosted.prove_again(output, tmp_path, tmp_path, "dsn", control) == failed


@pytest.mark.parametrize(
    "left, control, said",
    [
        # The profile routes this agent: an unproved canonical binding is not its binding.
        (
            BindingMode.CANONICAL,
            _own(BindingMode.TOOL_ROUTED),
            "is canonical, the agent's is tool_routed",
        ),
        # Another mapping than the reviewed one, refused by this process's own workbench.
        (
            BindingMode.TOOL_ROUTED,
            _own(
                BindingMode.TOOL_ROUTED, refused="not the reviewed pairing: block_card"
            ),
            "not the reviewed pairing: block_card",
        ),
        # The same mapping over other agent tools, or any other edit, seals another digest.
        (
            BindingMode.TOOL_ROUTED,
            _own(BindingMode.TOOL_ROUTED, digest="sha256:" + "12" * 32),
            "not the one this process makes",
        ),
        (
            BindingMode.CANONICAL,
            _own(BindingMode.CANONICAL, digest="sha256:" + "12" * 32),
            "not the one this process makes",
        ),
    ],
    ids=["canonical-swap", "another-pairing", "another-binding", "another-canonical"],
)
def test_a_sealed_binding_this_process_would_not_make_is_refused(
    tmp_path, monkeypatch, left, control, said
):
    from fi.alk.harness import releases

    output = tmp_path / "adaptation"
    _left_by_the_builder(output, {"passed": True, "scenarios": {"S01": "proved"}})
    _control(monkeypatch, S01=("proved", ""))
    monkeypatch.setattr(
        releases,
        "load_agent_binding",
        lambda _path: SimpleNamespace(digest=SHA, mode=left),
    )

    result = hosted.prove_again(output, tmp_path, tmp_path, "dsn", control)

    assert result["failure"]["code"] == "binding_unproved"
    assert said in result["failure"]["message"]
    assert json.loads((output / RESULT).read_text())["status"] == "failed"


@pytest.mark.parametrize("body", ["not json", "[]"])
def test_a_malformed_result_fails_instead_of_crashing(tmp_path, body):
    output = tmp_path / "adaptation"
    _left_by_the_builder(output, {})
    (output / RESULT).write_text(body, encoding="utf-8")
    result = hosted.prove_again(
        output, tmp_path, tmp_path, "dsn", _own(BindingMode.TOOL_ROUTED)
    )
    assert result["failure"]["code"] == "binding_unproved"


def test_a_canonical_binding_is_reported_as_this_process_read_it(tmp_path, monkeypatch):
    """Nothing to prove, but the binding named is the sealed one, whatever the result file says."""
    from fi.alk.harness import releases

    output = tmp_path / "adaptation"
    _left_by_the_builder(output, {})
    monkeypatch.setattr(
        releases,
        "load_agent_binding",
        lambda _path: SimpleNamespace(digest=SHA, mode=BindingMode.CANONICAL),
    )
    result = hosted.prove_again(
        output, tmp_path, tmp_path, "dsn", _own(BindingMode.CANONICAL)
    )
    assert result["binding"] == {"digest": SHA, "mode": "canonical"}
    assert result["binding_proof"] is None


def test_what_was_verified_is_recorded_outside_the_workspace(tmp_path):
    """Beside the workspace, in the folder the session's tools refuse, readable by its owner
    only: the platform reads the proof's digest from here, not from the output."""
    workspace = tmp_path / "authoring"
    (workspace / "adaptation").mkdir(parents=True)
    digest = "sha256:" + "cd" * 32

    hosted.record_verified(
        workspace, binding={"digest": SHA, "mode": "tool_routed"}, proof_digest=digest
    )

    path = hosted.verified_path(workspace)
    assert path == tmp_path / ".authoring-credentials" / "adaptation-verified.json"
    assert workspace not in path.parents
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text()) == {
        "schema_version": "futureagi.adaptation-verified.v1",
        "binding": {"digest": SHA, "mode": "tool_routed"},
        "binding_proof": {"digest": digest},
    }
    from fi.alk.harness.backends.files import secret_path

    assert secret_path(str(path))


def test_the_control_process_records_the_proof_it_made_not_the_one_left(
    tmp_path, monkeypatch
):
    """Review mode end to end, offline: the builder leaves a proof and a matching digest in its
    output; the record outside the workspace carries the control process's own."""
    from fi.alk.harness.binding_proof import proof_digest

    workspace = tmp_path / "authoring"
    output = workspace / "adaptation"
    _left_by_the_builder(output, {"passed": True, "scenarios": {"S01": "proved"}})
    (workspace / hosted.FINISHED_MARKER).write_text("{}")
    proof, seen = _control(
        monkeypatch, S01=("proved", ""), S02=("unscorable", "uses send_sms")
    )

    assert (
        hosted._await_result(
            output, tmp_path / "environment", tmp_path, "dsn", seen["control"]
        )
        == 0
    )

    recorded = json.loads(hosted.verified_path(workspace).read_text())
    assert recorded["binding"] == {"digest": SHA, "mode": "tool_routed"}
    assert recorded["binding_proof"] == {"digest": proof_digest(proof)}
    # A later writer changes the output's pair to another that agrees with itself.
    forged = {"status": "completed", "binding_proof": {"digest": "sha256:" + "ee" * 32}}
    (output / RESULT).write_text(json.dumps(forged))
    assert json.loads(hosted.verified_path(workspace).read_text()) == recorded


def test_a_record_is_a_new_file_never_one_already_there(tmp_path):
    workspace = tmp_path / "authoring"
    (workspace / "adaptation").mkdir(parents=True)
    path = hosted.verified_path(workspace)
    path.parent.mkdir()
    elsewhere = workspace / "planted.json"
    elsewhere.write_text("{}")
    path.symlink_to(elsewhere)

    hosted.record_verified(
        workspace, binding={"digest": SHA, "mode": "canonical"}, proof_digest=None
    )

    assert elsewhere.read_text() == "{}" and not path.is_symlink()
    assert path.stat().st_mode & 0o777 == 0o600


def test_a_failed_adaptation_records_nothing_verified(tmp_path, monkeypatch):
    workspace = tmp_path / "authoring"
    output = workspace / "adaptation"
    _left_by_the_builder(output, {})
    (workspace / hosted.FINISHED_MARKER).write_text("{}")
    _proof, seen = _control(monkeypatch, S01=("failed", "grace_booked did not hold"))

    assert (
        hosted._await_result(
            output, tmp_path / "environment", tmp_path, "dsn", seen["control"]
        )
        == 2
    )
    assert not hosted.verified_path(workspace).exists()


# --- end to end: CardGuard on Banking ----------------------------------------------------------


def _banking(
    tmp_path: Path, *, pairing: dict[str, Any] | None = None
) -> tuple[Workbench, dict[str, Any]]:
    """CardGuard on Banking, with the pairing its mapping is, or `pairing` over it."""
    release = Path(RELEASES) / "banking"
    profile, mapping = fixtures.cardguard(release / "environment")
    workbench = Workbench(
        environments=[release / "environment"],
        suite_root=release / "suite",
        output=tmp_path / "adaptation",
        postgres_dsn=DSN,
        profile=profile_from_file(profile),
        finished_marker=tmp_path / hosted.FINISHED_MARKER,
        pairing={**_pairing(mapping), **(pairing or {})},
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
def test_a_written_trajectory_never_replaces_one_code_derives(tmp_path, monkeypatch):
    """The model writes an empty trajectory for every scenario. Code derives each from the
    reference solution, so the derived ones are proved and the written ones are set aside:
    a bad draft can neither shrink the score nor fail what the binding does achieve."""
    workbench, mapping = _banking(tmp_path)
    nothing = {entry.key: [] for entry in workbench.base_suite.scenarios}
    _model(monkeypatch, {"binding": mapping, "trajectories": nothing, "conflicts": []})
    asyncio.run(auto.adapt(workbench))
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
    _profile, mapping = fixtures.cardguard(Path(RELEASES) / "banking" / "environment")
    workbench, mapping = _banking(
        tmp_path, pairing={next(iter(_pairing(mapping))): None}
    )
    answer = {"binding": mapping, "trajectories": {}, "conflicts": []}
    _model(monkeypatch, answer, answer)
    asyncio.run(auto.adapt(workbench))
    result = _result(workbench)
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "binding_invalid"
    assert "not the reviewed pairing" in result["failure"]["message"]
    assert not (workbench.output / "binding-proof.json").exists()


@needs_releases
def test_a_proof_edited_in_the_workspace_does_not_survive_the_control_process(
    tmp_path, monkeypatch
):
    """CardGuard is bound and sealed, then the proof file is edited as a session could: the
    control process's own proof of the sealed binding replaces it."""
    workbench, mapping = _banking(tmp_path)
    _model(monkeypatch, {"binding": mapping, "trajectories": {}, "conflicts": []})
    asyncio.run(auto.adapt(workbench))
    sealed = _result(workbench)
    assert sealed["status"] == "completed", sealed["failure"]
    genuine = json.loads((workbench.output / "binding-proof.json").read_text())
    key = next(iter(genuine["scenarios"]))
    edited = {
        **genuine,
        "scenarios": {
            **genuine["scenarios"],
            key: {"status": "unscorable", "reason": "edited", "adversarial": {}},
        },
    }
    (workbench.output / "binding-proof.json").write_text(json.dumps(edited))

    release = Path(RELEASES) / "banking"
    control, _mapping = _banking(tmp_path / "control")
    result = hosted.prove_again(
        workbench.output, release / "environment", release / "suite", DSN, control
    )

    assert result["status"] == "completed", result.get("failure")
    assert result["binding_proof"] == sealed["binding_proof"]
    assert json.loads((workbench.output / "binding-proof.json").read_text()) == genuine


@needs_releases
def test_the_control_process_refuses_a_binding_of_another_pairing(
    tmp_path, monkeypatch
):
    """The builder's inputs could be edited, so its own check proves nothing: what it sealed is
    held to the pairing the job carries."""
    workbench, mapping = _banking(tmp_path)
    _model(monkeypatch, {"binding": mapping, "trajectories": {}, "conflicts": []})
    asyncio.run(auto.adapt(workbench))
    assert _result(workbench)["status"] == "completed"
    dropped = next(iter(_pairing(mapping)))
    control, _mapping = _banking(tmp_path / "control", pairing={dropped: None})

    release = Path(RELEASES) / "banking"
    result = hosted.prove_again(
        workbench.output, release / "environment", release / "suite", DSN, control
    )

    assert result["failure"]["code"] == "binding_unproved"
    assert "not the reviewed pairing" in result["failure"]["message"]


@needs_releases
def test_an_agent_with_the_environments_tools_is_held_to_the_pairing(tmp_path):
    """Its tools are the environment's, so it would be canonical and served every one. The person
    left one unpaired: it is routed, and that tool is unsupported."""
    release = Path(RELEASES) / "banking"
    names = [
        tool["name"]
        for tool in json.loads((release / "environment" / "tools.json").read_text())
    ]
    profile, _mapping = fixtures.declared_agent(
        release / "environment",
        name="Teller",
        instructions="Help.",
        renames={name: (name, {}) for name in names},
    )
    identity = {name: name for name in names}
    dropped = names[0]

    def bench(where: str, pairing: dict[str, Any]) -> Workbench:
        return Workbench(
            environments=[release / "environment"],
            suite_root=release / "suite",
            output=tmp_path / where,
            postgres_dsn=DSN,
            profile=profile_from_file(profile),
            pairing=pairing,
        )

    assert bench("whole", identity).mode is BindingMode.CANONICAL
    held = bench("held", {**identity, dropped: None})
    assert held.mode is BindingMode.TOOL_ROUTED
    entry = next(one for one in held.binding.tools if one.agent_tool == dropped)
    assert isinstance(entry, UnsupportedToolBinding)
