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


def test_a_world_that_can_call_and_declares_nothing_is_trivially_fine():
    """Vacuously true, and only for a world that could have answered. This test used to pass a
    bare SimpleNamespace, which has no forward seam either, and asserted it was a pass: it was
    asserting the defect. A world that cannot call anything has not verified nothing, it has
    verified nothing *and cannot say so*, which is the case the verdict type exists to keep
    apart. See test_a_world_that_cannot_call_anything_is_not_a_pass."""

    class Empty:
        runtime_tools: set[str] = set()

        def forward(self, endpoint, arguments, *, record=False):
            raise AssertionError("nothing to call")

    verdict = verify_runtime_tools(Empty(), _contract())
    assert verdict.checked is True
    assert verdict.ok is True
    assert verdict.reason == "no runtime tools declared"


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
    cause = asyncio.run(
        _scheduler(_contract("book_ride"))._verify_world(
            world, SimpleNamespace(tag="rt"), 0
        )
    )
    assert "runtime tools did not answer" in cause
    assert "book_ride" in cause


def test_scheduler_allows_an_unverifiable_world_but_names_what_goes_ungraded(caplog):
    # Hosted has no seam yet, so failing every run would be a worse lie than the silence. It is
    # allowed through, loudly, at a level the hosted guest actually emits.
    scheduler = _scheduler(_contract("book_ride"))
    with caplog.at_level(logging.WARNING, logger="fi.alk.harness.hosted_scheduler"):
        cause = asyncio.run(
            scheduler._verify_world(_NoSeam("book_ride"), SimpleNamespace(tag="rt"), 3)
        )
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
    # One runtime held across the loop: the same world leased for three scenarios, which is the
    # caching this gate exists for. A NEW runtime object would rightly be verified again.
    runtime = SimpleNamespace(tag="rt")
    for _ in range(3):
        assert asyncio.run(scheduler._verify_world(world, runtime, 0)) == ""
    assert calls == ["book_ride"]


def test_no_contract_means_no_gate():
    scheduler = _scheduler(None)
    assert (
        asyncio.run(
            scheduler._verify_world(_NoSeam("book_ride"), SimpleNamespace(tag="rt"), 0)
        )
        == ""
    )


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


# --- an index is a position, not an identity ----------------------------------------------------
#
# Reconcile replaces a demoted world with a fresh one at the same index. That replacement is
# exactly when checking matters most: the world it replaced may have been demoted BY this gate.


def _counting_world(calls: list[str], name: str):
    class W:
        runtime_tools = {"book_ride"}
        endpoint_for = {"book_ride": "book_ride"}

        def forward(self, endpoint, arguments, *, record=False):
            from fi.alk.harness.world.runtime import Call

            calls.append(name)
            return Call(name=endpoint, arguments={}, ok=True)

    return W()


def test_a_replacement_world_at_the_same_index_is_verified_again():
    import asyncio

    seen: list[str] = []
    scheduler = _scheduler(_contract("book_ride"))

    first_runtime = SimpleNamespace(world_index=0, tag="original")
    assert (
        asyncio.run(
            scheduler._verify_world(_counting_world(seen, "first"), first_runtime, 0)
        )
        == ""
    )
    assert seen == ["first"]

    # Same index, same world payload, but reconcile built a NEW runtime object.
    second_runtime = SimpleNamespace(world_index=0, tag="rebuilt")
    assert (
        asyncio.run(
            scheduler._verify_world(_counting_world(seen, "second"), second_runtime, 0)
        )
        == ""
    )
    assert seen == ["first", "second"], "the replacement world was never verified"


def test_the_same_runtime_is_still_verified_only_once():
    # The caching this gate exists for must survive the identity fix: ten scenarios on one world
    # must not call every declared tool ten times.
    import asyncio

    seen: list[str] = []
    scheduler = _scheduler(_contract("book_ride"))
    runtime = SimpleNamespace(world_index=0)
    for _ in range(5):
        asyncio.run(scheduler._verify_world(_counting_world(seen, "same"), runtime, 0))
    assert seen == ["same"]


def test_a_rebuilt_world_that_is_still_broken_is_caught_again():
    # The case the defect hid: the gate demotes a world, reconcile rebuilds it just as broken,
    # and the successor must not be waved through as already verified.
    import asyncio

    from fi.alk.harness.world.runtime import Call

    class Broken:
        runtime_tools = {"book_ride"}
        endpoint_for = {"book_ride": "book_ride"}

        def forward(self, endpoint, arguments, *, record=False):
            return Call(name=endpoint, arguments={}, ok=False, error="500")

    scheduler = _scheduler(_contract("book_ride"))
    first = asyncio.run(scheduler._verify_world(Broken(), SimpleNamespace(tag="a"), 0))
    second = asyncio.run(scheduler._verify_world(Broken(), SimpleNamespace(tag="b"), 0))
    assert "runtime tools did not answer" in first
    assert "runtime tools did not answer" in second, "successor skipped verification"


def test_verification_is_tracked_per_index_not_globally():
    import asyncio

    seen: list[str] = []
    scheduler = _scheduler(_contract("book_ride"))
    shared = SimpleNamespace(tag="r")
    asyncio.run(scheduler._verify_world(_counting_world(seen, "w0"), shared, 0))
    # A different index holding a different runtime must still be checked.
    asyncio.run(
        scheduler._verify_world(
            _counting_world(seen, "w1"), SimpleNamespace(tag="s"), 1
        )
    )
    assert seen == ["w0", "w1"]


# --- every outcome has to be observable, and each a different observation ----------------------
#
# Three live hosted runs read as clean because the success path logged nothing. "Verified 20
# tools" and "there was nothing to verify" were the same silence from outside, which is this
# verdict type's own conflation moved out of the return value and into the logging.


class _HostedLike:
    """`HostedWorld`: no forward seam, and no runtime_tools attribute either.

    Both are absent through ordinary AttributeError, which is what the real handle's __getattr__
    is careful to preserve, so `getattr(world, "runtime_tools", set())` answers "none declared"
    for a world that was never able to answer at all.
    """

    def __getattr__(self, name):
        raise AttributeError(name)


def _verify_log(world, caplog):
    import fi.alk.harness.hosted_scheduler as hs

    scheduler = _scheduler(_contract("book_ride"))
    with caplog.at_level(logging.WARNING, logger=hs.__name__):
        fault = asyncio.run(
            scheduler._verify_world(world, SimpleNamespace(tag="r"), 0)
        )
    return fault, caplog.text


def test_a_world_that_cannot_call_anything_is_not_a_pass(caplog):
    """The defect underneath the silence, and the reason it was invisible: emptiness was checked
    before the seam was. A hosted world has neither attribute, so "which tools do you declare"
    answered "none", the function returned checked=True with no faults, and that is this type's
    definition of a pass. The gate has therefore been a no-op on the whole hosted lane."""
    verdict = verify_runtime_tools(_HostedLike(), _contract("book_ride"))
    assert verdict.checked is False, "a world with no seam proved nothing"
    assert verdict.ok is False
    assert "no forward seam" in verdict.reason

    fault, said = _verify_log(_HostedLike(), caplog)
    # It still does not fail the run: this reports truthfully, it does not change the verdict.
    assert fault == ""
    assert "NOT verified" in said
    assert "could not say which tools it has" in said


def test_a_verified_world_says_what_it_proved(caplog):
    fault, said = _verify_log(
        _Reachable({"book_ride": Call(name="book_ride", arguments={}, ok=True)}), caplog
    )
    assert fault == ""
    assert "verified 1 runtime tools" in said
    assert "book_ride" in said


def test_a_world_with_nothing_to_verify_says_that_instead(caplog):
    """Truthful, and a different sentence. A world that genuinely declares no runtime tools is a
    vacuous pass, and reporting it in the same words as a real one is what hid the hosted no-op."""

    class Empty:
        runtime_tools: set[str] = set()

        def forward(self, endpoint, arguments, *, record=False):
            raise AssertionError("nothing to call")

    fault, said = _verify_log(Empty(), caplog)
    assert fault == ""
    assert "no runtime tools declared, nothing verified" in said
    assert "verified 1" not in said


def test_the_four_outcomes_are_four_distinguishable_observations(caplog):
    """The invariant, stated once. If any two of these produce the same observable result then an
    operator reading a run cannot tell them apart, which is how three runs passed review."""
    import fi.alk.harness.hosted_scheduler as hs

    class Empty:
        runtime_tools: set[str] = set()

        def forward(self, endpoint, arguments, *, record=False):
            raise AssertionError("nothing to call")

    seen = []
    for world in (
        _Reachable({"book_ride": Call(name="book_ride", arguments={}, ok=True)}),
        Empty(),
        _HostedLike(),
        _Reachable(
            {"book_ride": Call(name="book_ride", arguments={}, ok=False, error="boom")}
        ),
    ):
        caplog.clear()
        scheduler = _scheduler(_contract("book_ride"))
        with caplog.at_level(logging.WARNING, logger=hs.__name__):
            fault = asyncio.run(
                scheduler._verify_world(world, SimpleNamespace(tag="r"), 0)
            )
        seen.append((fault, caplog.text.strip()))

    assert len(set(seen)) == 4, (
        "two outcomes are indistinguishable from outside:\n"
        + "\n".join(f"  fault={f!r} log={t!r}" for f, t in seen)
    )
