from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fi.alk import simulate as public_simulate
from fi.simulate.results import LocalFilesystemResultSink
from fi.simulate.runtime import (
    AgentEndpointSpec,
    EnvironmentSpec,
    RunStatus,
    SimulationSpec,
    SimulatorPolicySpec,
)
from fi.simulate.runtime.runner import SimulationRunner
from fi.simulate.simulation.engines.local_text import LocalTextEngine
from fi.simulate.simulation.models import Persona, Scenario


def _scenario() -> Scenario:
    return Scenario(
        name="runtime-chat",
        dataset=[
            Persona(
                persona={"name": "Morgan"},
                situation="I need a status update.",
                outcome="The status is complete.",
            )
        ],
    )


def _spec(*, timeout: float = 5.0) -> SimulationSpec:
    return SimulationSpec(
        run_id="run_chat_test",
        environment=EnvironmentSpec(
            adapter="chat",
            world_kind="conversation",
            config={"max_turns": 1, "min_turns": 1},
        ),
        target=AgentEndpointSpec(adapter="callable"),
        simulator=SimulatorPolicySpec(adapter="synthetic_user"),
        scenario=_scenario(),
        execution={"timeout": {"run_seconds": timeout}},
    )


def test_runtime_contracts_are_available_from_public_facade() -> None:
    assert public_simulate.SimulationSpec is SimulationSpec
    assert public_simulate.SimulationRunner is SimulationRunner
    assert public_simulate.LocalFilesystemResultSink is LocalFilesystemResultSink


def test_runner_writes_canonical_local_report(tmp_path: Path) -> None:
    async def target(_input):
        return "The status is complete."

    sink = LocalFilesystemResultSink(tmp_path)
    report = asyncio.run(
        SimulationRunner().run(
            _spec(),
            target=target,
            result_sink=sink,
        )
    )

    assert report.status == RunStatus.COMPLETED
    assert report.test_cases[0].result is not None
    assert report.test_cases[0].result.transcript
    assert (tmp_path / report.run_id / "spec.json").exists()
    assert (tmp_path / report.run_id / "plan.json").exists()
    assert (tmp_path / report.run_id / "report.json").exists()


def test_runner_returns_redacted_typed_failure(caplog) -> None:
    secret = "-".join(("customer", "secret", "value"))

    async def target(_input):
        raise ValueError(secret)

    with caplog.at_level(logging.ERROR, logger="fi.simulate.runtime.runner"):
        report = asyncio.run(SimulationRunner().run(_spec(), target=target))

    assert report.status == RunStatus.FAILED
    assert report.failure is not None
    assert report.failure.code == "simulation_failed"
    assert report.failure.details == {"exception_type": "ValueError"}
    assert secret not in report.model_dump_json()
    assert secret not in caplog.text
    assert "ValueError: details redacted" in caplog.text
    assert report.test_cases == []


def test_runner_enforces_run_timeout() -> None:
    async def target(_input):
        await asyncio.sleep(1)
        return "late"

    report = asyncio.run(
        SimulationRunner().run(_spec(timeout=0.01), target=target)
    )

    assert report.status == RunStatus.TIMED_OUT
    assert report.failure is not None
    assert report.failure.code == "simulation_timeout"


def test_local_text_engine_remains_legacy_compatible() -> None:
    async def target(_input):
        return "The status is complete."

    report = asyncio.run(
        LocalTextEngine().run(
            scenario=_scenario(),
            agent_callback=target,
            max_turns=1,
            min_turns=1,
        )
    )

    assert report.results[0].metadata["engine"] == "local_text"
    assert "run_id" not in report.results[0].metadata
