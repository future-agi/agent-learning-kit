"""The gate that earns the build stage its shell.

A world is allowed to grade an agent only once it has shown it answers that agent's own tools.
The case these tests exist for is the middle one: a world that could not be asked must not read
the same as a world that was asked and was fine.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from types import SimpleNamespace

from fi.alk.harness.world.probe import verify_runtime_tools
from fi.alk.harness.world.runtime import Call


def _contract(*names: str) -> SimpleNamespace:
    return SimpleNamespace(
        tools=[SimpleNamespace(name=n, args={}, arg_types={}) for n in names]
    )


class _Reachable:
    """A world whose tools can actually be called."""

    def __init__(self, outcomes: dict[str, Call]) -> None:
        self.runtime_tools = set(outcomes)
        self.endpoint_for = {name: name for name in outcomes}
        self._outcomes = outcomes

    def forward(self, endpoint, arguments, *, record=False):
        return self._outcomes[endpoint]


class _NoSeam:
    """A world that declares runtime tools and offers no way to call them."""

    def __init__(self, *names: str) -> None:
        self.runtime_tools = set(names)
        self.endpoint_for = {}


def test_a_world_that_answers_is_checked_and_ok():
    world = _Reachable({"book_ride": Call(name="book_ride", arguments={}, ok=True)})
    verdict = verify_runtime_tools(world, _contract("book_ride"))
    assert verdict.checked is True
    assert verdict.broken == []
    assert verdict.ok is True


def test_a_refusing_tool_is_working_not_broken():
    # A world that rejects a nonexistent id is doing its job; only a crash counts against it.
    world = _Reachable(
        {"book_ride": Call(name="book_ride", arguments={}, ok=False, refused=True)}
    )
    assert verify_runtime_tools(world, _contract("book_ride")).ok is True


def test_a_server_error_is_broken():
    world = _Reachable(
        {
            "book_ride": Call(
                name="book_ride",
                arguments={},
                ok=False,
                error="500 Internal Server Error",
            )
        }
    )
    verdict = verify_runtime_tools(world, _contract("book_ride"))
    assert verdict.checked is True
    assert verdict.ok is False
    assert "book_ride" in verdict.broken[0]


def test_a_world_with_no_seam_is_not_checked_and_never_reads_as_ok():
    # The defect this whole gate replaces: recording an unexecuted tool as passing.
    world = _NoSeam("book_ride", "cancel_ride")
    verdict = verify_runtime_tools(world, _contract("book_ride", "cancel_ride"))
    assert verdict.checked is False
    assert verdict.ok is False
    assert sorted(verdict.tools) == ["book_ride", "cancel_ride"]
    assert "forward" in verdict.reason


def test_a_world_declaring_no_runtime_tools_is_trivially_fine():
    verdict = verify_runtime_tools(SimpleNamespace(), _contract())
    assert verdict.checked is True
    assert verdict.ok is True


# --- the scheduler's use of it ----------------------------------------------------------------


def _scheduler(contract):
    from fi.alk.harness.hosted_scheduler import HostedScheduler

    return HostedScheduler(
        pool=SimpleNamespace(),
        world_factory=SimpleNamespace(),
        call_runner=SimpleNamespace(),
        outbound=SimpleNamespace(),
        job_seed=1,
        contract=contract,
    )


def test_scheduler_refuses_a_world_whose_tools_are_broken():
    world = _Reachable(
        {"book_ride": Call(name="book_ride", arguments={}, ok=False, error="boom")}
    )
    cause = asyncio.run(_scheduler(_contract("book_ride"))._verify_world(world, 0))
    assert "runtime tools did not answer" in cause
    assert "book_ride" in cause


def test_scheduler_allows_an_unverifiable_world_but_names_what_goes_ungraded(caplog):
    # Hosted has no seam yet, so failing every run would be a worse lie than the silence. It is
    # allowed through, loudly, at a level the hosted guest actually emits.
    scheduler = _scheduler(_contract("book_ride"))
    with caplog.at_level(logging.WARNING, logger="fi.alk.harness.hosted_scheduler"):
        cause = asyncio.run(scheduler._verify_world(_NoSeam("book_ride"), 3))
    assert cause == ""
    assert "ungraded" in caplog.text
    assert "book_ride" in caplog.text


def test_a_world_is_verified_once_not_per_scenario():
    calls: list[str] = []

    class Counting(_Reachable):
        def forward(self, endpoint, arguments, *, record=False):
            calls.append(endpoint)
            return super().forward(endpoint, arguments, record=record)

    world = Counting({"book_ride": Call(name="book_ride", arguments={}, ok=True)})
    scheduler = _scheduler(_contract("book_ride"))
    for _ in range(3):
        assert asyncio.run(scheduler._verify_world(world, 0)) == ""
    assert calls == ["book_ride"]


def test_no_contract_means_no_gate():
    scheduler = _scheduler(None)
    assert asyncio.run(scheduler._verify_world(_NoSeam("book_ride"), 0)) == ""


# --- the receipt boundary ----------------------------------------------------------------------
#
# A runner the build stage wrote is free in how it works and not in what it returns, because the
# platform renders a fixed shape. Same treatment as `submit_scenario`: reject, and say how to fix.


def _outcome(**over):
    from fi.alk.harness.hosted_scheduler import CallOutcome

    base = dict(
        calls=(),
        turns=6,
        started_at="2026-08-30T00:00:00.000Z",
        ended_at="2026-08-30T00:02:00.000Z",
        duration_ms=120000,
        transcript_artifact="sha256:abc",
        recording_artifacts=("sha256:def",),
    )
    base.update(over)
    return CallOutcome(**base)


def test_a_complete_voice_outcome_passes():
    from fi.alk.harness.hosted_scheduler import call_evidence_faults

    wanted = ("turns", "transcript", "recordings", "timing")
    assert call_evidence_faults(_outcome(), wanted) == []


def test_a_runner_that_promised_nothing_is_held_to_nothing():
    # An empty requirement set is a caller exercising the scheduler, not a runner shipping a
    # receipt. Holding it to a contract it never declared would fail every scheduler test.
    from fi.alk.harness.hosted_scheduler import call_evidence_faults

    assert call_evidence_faults(_outcome(turns=0, transcript_artifact=None)) == []


def test_a_text_agent_is_not_delinquent_for_having_no_audio():
    from fi.alk.harness.hosted_scheduler import call_evidence_faults

    wanted = ("turns", "transcript", "timing")
    assert call_evidence_faults(_outcome(recording_artifacts=()), wanted) == []


def test_a_voice_runner_without_recordings_is_rejected():
    from fi.alk.harness.hosted_scheduler import call_evidence_faults

    faults = call_evidence_faults(
        _outcome(recording_artifacts=()),
        ("turns", "transcript", "recordings", "timing"),
    )
    assert len(faults) == 1
    assert "recording_artifacts" in faults[0]
    assert "Upload the audio" in faults[0]  # tells the runner how to repair it


def test_every_missing_piece_is_reported_at_once_with_repair_instructions():
    from fi.alk.harness.hosted_scheduler import call_evidence_faults

    faults = call_evidence_faults(
        _outcome(
            turns=4,
            transcript_artifact=None,
            recording_artifacts=(),
            duration_ms=0,
            started_at=None,
        ),
        ("turns", "transcript", "recordings", "timing"),
    )
    # One round trip, not four: a runner should be able to fix everything and return once.
    # There is no turn-count fault: the floor was removed, because a short real call is a real
    # call and a zero-turn outcome is a diagnosis this contract must not touch.
    assert len(faults) == 4


def test_the_boundary_raises_rather_than_passing_an_unrenderable_outcome():
    import asyncio

    from fi.alk.harness.hosted_scheduler import CallEvidenceMissing, _run_call

    class Runner:
        async def run(self, scenario, runtime, *, world=None):
            return _outcome(transcript_artifact=None)

    with pytest.raises(CallEvidenceMissing) as raised:
        asyncio.run(_run_call(Runner(), None, None, None, ("transcript",)))
    assert "Fix these and return the outcome again" in str(raised.value)


# --- a zero-turn outcome is a diagnosis, not a broken receipt -----------------------------------
#
# The voice runner deliberately maps the engine's "agent joined but never spoke" codes to a normal
# CallOutcome so the scheduler can report evidence_missing/simulator. Policing that here would
# replace a precise finding with "the runner produced an unrenderable outcome" -- the exact
# misleading-message failure this contract exists to prevent.


def test_a_silent_agent_outcome_is_not_treated_as_a_broken_receipt():
    from fi.alk.harness.hosted_scheduler import call_evidence_faults

    # What call_runner returns for no_conversation / conversation_silence_timeout at zero turns:
    # no calls, no transcript, no recordings. That shape IS the diagnosis.
    silent = _outcome(
        turns=0,
        transcript_artifact=None,
        recording_artifacts=(),
        duration_ms=0,
        started_at=None,
    )
    assert (
        call_evidence_faults(silent, ("turns", "transcript", "recordings", "timing"))
        == []
    )


def test_a_silent_agent_never_surfaces_as_call_failed_or_evidence_missing():
    import asyncio

    from fi.alk.harness.hosted_scheduler import _run_call

    class SilentAgentRunner:
        async def run(self, scenario, runtime, *, world=None):
            return _outcome(
                turns=0,
                transcript_artifact=None,
                recording_artifacts=(),
                duration_ms=0,
                started_at=None,
            )

    # It must pass straight through, so the scheduler's own coverage rule can report the real
    # cause rather than this boundary masking it.
    outcome = asyncio.run(
        _run_call(
            SilentAgentRunner(), None, None, None, ("turns", "transcript", "timing")
        )
    )
    assert outcome.turns == 0
    assert outcome.calls == ()


def test_a_one_turn_call_is_a_real_call_not_a_broken_receipt():
    # The agent answered and the caller rang off. Whether that went far enough to judge is what
    # sub-goal grading decides, not this contract.
    from fi.alk.harness.hosted_scheduler import call_evidence_faults

    assert (
        call_evidence_faults(_outcome(turns=1), ("turns", "transcript", "timing")) == []
    )


def test_a_completed_call_missing_its_transcript_is_still_rejected():
    # The contract must still bite where it should: a call that ran and cannot be rendered.
    from fi.alk.harness.hosted_scheduler import call_evidence_faults

    faults = call_evidence_faults(
        _outcome(turns=8, transcript_artifact=None), ("transcript",)
    )
    assert len(faults) == 1
    assert "transcript_artifact" in faults[0]


def test_missing_evidence_gets_its_own_code_not_the_generic_one():
    from fi.alk.harness.hosted_scheduler import _CODE_DOMAIN, _RETRYABLE_CODES, _failure

    assert "call_evidence_missing" in _CODE_DOMAIN
    failure = _failure("call_evidence_missing", "x")
    assert failure.code == "call_evidence_missing"
    assert failure.domain == "simulator"
    # Deterministic: a runner that omits a transcript omits it again, so a retry buys nothing.
    assert "call_evidence_missing" not in _RETRYABLE_CODES
