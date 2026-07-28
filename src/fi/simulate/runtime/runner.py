from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any

from fi.simulate._logging import redacted_exc_info
from fi.simulate.agent.wrapper import AgentWrapper, SimulationArtifact, SimulationEvent
from fi.simulate.artifacts import ArtifactManifest
from fi.simulate.environment import EnvironmentAdapter
from fi.simulate.environments.chat import ChatEnvironment
from fi.simulate.evidence import EvidenceSourceSummary
from fi.simulate.results.base import ResultSink
from fi.simulate.simulation.models import Persona

from .events import CanonicalEvent
from .failures import FailureStage, SimulationFailure
from .plan import SimulationPlan
from .planner import build_plan
from .report import SimulationReport
from .run import CleanupStatus, RunStatus
from .spec import SimulationSpec

logger = logging.getLogger(__name__)


class SimulationRunner:
    async def run(
        self,
        spec: SimulationSpec,
        *,
        target: Callable[..., Any] | AgentWrapper | Any,
        result_sink: ResultSink | None = None,
        artifacts: list[SimulationArtifact | dict[str, Any]] | None = None,
        events: list[SimulationEvent | dict[str, Any]] | None = None,
        environment: EnvironmentAdapter | Iterable[EnvironmentAdapter] | None = None,
        auto_execute_tools: bool = True,
        stop_when: Callable[[list[dict[str, Any]], Persona], bool] | None = None,
        agent_wrapper_kwargs: dict[str, Any] | None = None,
    ) -> SimulationReport:
        started_at = datetime.now(timezone.utc)
        plan: SimulationPlan | None = None
        try:
            plan = build_plan(spec)
            if result_sink is not None:
                result_sink.prepare(spec, plan)
            self._write_event(
                result_sink,
                CanonicalEvent.create(
                    run_id=spec.run_id,
                    test_case_id="run",
                    event_type="session.started",
                    source="runtime",
                    sequence=0,
                ),
            )
            if spec.environment.adapter != "chat":
                raise ValueError(
                    f"environment_adapter_unsupported: {spec.environment.adapter}"
                )
            legacy_report = await asyncio.wait_for(
                ChatEnvironment().run(
                    scenario=spec.scenario,
                    agent_callback=target,
                    max_turns=int(spec.environment.config.get("max_turns", 6)),
                    min_turns=int(spec.environment.config.get("min_turns", 2)),
                    attacks=spec.environment.config.get("attacks"),
                    modality=str(spec.environment.config.get("modality", "text")),
                    artifacts=artifacts,
                    events=events,
                    environment=environment,
                    auto_execute_tools=auto_execute_tools,
                    stop_when=stop_when,
                    agent_wrapper_kwargs=agent_wrapper_kwargs,
                ),
                timeout=spec.execution.timeout.run_seconds,
            )
        except asyncio.TimeoutError:
            report = self._failure_report(
                spec,
                plan=plan,
                started_at=started_at,
                status=RunStatus.TIMED_OUT,
                failure=SimulationFailure(
                    stage=FailureStage.RUNNING,
                    code="simulation_timeout",
                    message="Simulation exceeded its run deadline",
                    retryable=True,
                ),
            )
        except Exception as exc:
            stage = FailureStage.PLANNING if plan is None else FailureStage.RUNNING
            report = self._failure_report(
                spec,
                plan=plan,
                started_at=started_at,
                status=RunStatus.FAILED,
                failure=SimulationFailure(
                    stage=stage,
                    code="simulation_failed",
                    message="Simulation execution failed",
                    retryable=False,
                    details={"exception_type": type(exc).__name__},
                ),
            )
            logger.error(
                "Simulation run failed",
                exc_info=redacted_exc_info(exc),
                extra={
                    "run_id": spec.run_id,
                    "exception_type": type(exc).__name__,
                },
            )
        else:
            ended_at = datetime.now(timezone.utc)
            report = SimulationReport.from_legacy(
                legacy_report,
                run_id=spec.run_id,
                plan_id=plan.plan_id,
                spec_hash=spec.spec_hash or spec.content_hash(),
                status=RunStatus.COMPLETED,
                started_at=started_at,
                ended_at=ended_at,
                artifacts=ArtifactManifest(run_id=spec.run_id),
                evidence=[
                    EvidenceSourceSummary(
                        source_id=source.source_id,
                        adapter=source.adapter,
                        evidence_class=source.evidence_class,
                        capabilities=source.capabilities,
                    )
                    for source in spec.evidence.sources
                ],
            )
            report.cleanup_status = CleanupStatus.COMPLETED
            report = SimulationReport.model_validate(
                report.model_dump(exclude={"report_hash"})
            )
            self._write_event(
                result_sink,
                CanonicalEvent.create(
                    run_id=spec.run_id,
                    test_case_id="run",
                    event_type="session.ended",
                    source="runtime",
                    sequence=1,
                    payload={"status": report.status.value},
                ),
            )
        self._write_report(result_sink, report)
        return report

    def _failure_report(
        self,
        spec: SimulationSpec,
        *,
        plan: SimulationPlan | None,
        started_at: datetime,
        status: RunStatus,
        failure: SimulationFailure,
    ) -> SimulationReport:
        return SimulationReport(
            run_id=spec.run_id,
            plan_id=plan.plan_id if plan is not None else None,
            spec_hash=spec.spec_hash or spec.content_hash(),
            status=status,
            cleanup_status=CleanupStatus.COMPLETED,
            started_at=started_at,
            ended_at=datetime.now(timezone.utc),
            artifacts=ArtifactManifest(run_id=spec.run_id),
            failure=failure,
        )

    def _write_event(
        self,
        result_sink: ResultSink | None,
        event: CanonicalEvent,
    ) -> None:
        if result_sink is None:
            return
        try:
            result_sink.write_event(event)
        except Exception as exc:
            logger.error(
                "Simulation event sink failed",
                exc_info=redacted_exc_info(exc),
                extra={
                    "run_id": event.run_id,
                    "event_id": event.event_id,
                    "exception_type": type(exc).__name__,
                },
            )

    def _write_report(
        self,
        result_sink: ResultSink | None,
        report: SimulationReport,
    ) -> None:
        if result_sink is None:
            return
        try:
            result_sink.write_report(report)
        except Exception as exc:
            logger.error(
                "Simulation report sink failed",
                exc_info=redacted_exc_info(exc),
                extra={
                    "run_id": report.run_id,
                    "exception_type": type(exc).__name__,
                },
            )
