"""A slice writer must think only when the run asked for thinking.

Only the Claude backend acts on this flag, and thinking left on is the configuration that stalled
a run at zero CPU blocked on a read that never returned. The writer used to pass it
unconditionally, so a run started with thinking off still handed its writers thinking on.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from fi.alk.harness.scenariogen.write import stage as scenarios
from fi.alk.harness.contract import AgentContract, ToolSpec
from fi.alk.harness.scenariogen.write.stage import Slice


@pytest.fixture()
def contract():
    return AgentContract(
        agent="ride-agent",
        modality="voice",
        tools=[ToolSpec(name=one) for one in ("book_ride", "cancel_ride", "send_otp")],
        data_schema={"rides": {}, "otp_codes": {}, "users": {}},
    )


def writer_spec(monkeypatch, contract: AgentContract, where: Path):
    """The SessionSpec the writer would have run, captured instead of run.

    The stage stands in as a session that opens, is told nothing, and closes, so the writer runs
    its whole setup and writes no scenarios.
    """
    seen: dict = {}

    class FakeStage:
        def __init__(self, spec, name=""):
            seen["spec"] = spec

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def say(self, *_args, **_kwargs):
            return None

    fake_stage = FakeStage

    monkeypatch.setattr(scenarios, "Stage", fake_stage)
    # The writer reads the seeded world into its prompt; this test is about the flag beside it.
    monkeypatch.setattr(scenarios, "world_summary", lambda _where: "a world")
    where.mkdir(parents=True, exist_ok=True)
    asyncio.run(
        scenarios._write_slice(
            contract,
            Slice(use_case="booking", angle="card declined", asked=1),
            [],
            index=0,
            destination=where,
            on_event=None,
            ask=None,
        )
    )
    return seen["spec"]


def test_a_writer_does_not_think_when_the_run_did_not_ask(
    monkeypatch, contract, tmp_path
) -> None:
    monkeypatch.delenv("ALK_SCENARIO_THINKING", raising=False)

    assert writer_spec(monkeypatch, contract, tmp_path / "off").thinking is False


def test_a_writer_thinks_when_the_run_asked(monkeypatch, contract, tmp_path) -> None:
    monkeypatch.setenv("ALK_SCENARIO_THINKING", "1")

    assert writer_spec(monkeypatch, contract, tmp_path / "on").thinking is True
