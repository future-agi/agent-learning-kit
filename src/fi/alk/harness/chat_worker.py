"""Isolate chat model sessions and generated tool handlers from other leased worlds."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import socket
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .backends import GOOGLE_ADK_APPROVAL_ENV
from .bundle_v2 import EvidenceSeam
from .call_runner import ArtifactUploader, CallRunnerContext
from .chat_call_runner import _check_text_usage, handler_child_for
from .handler_child import HandlerChannel
from .hosted_scheduler import USER_CANCELED, CallAborted, CallCancelled, CallOutcome, Scenario, World, call_sealed
from .isolated_process import IsolatedWorkerError, run_json_worker, run_worker, stop_requested
from .outbound import ArtifactKind
from .privilege import IsolationMode, isolation_mode
from .process_runtime import EnvironmentRuntime, _allowlisted_ambient_env
from .usage import simulator_funding
from .world.runtime import Call

logger = logging.getLogger(__name__)

# Model configuration only: no platform callback capability or source-fetch credential.
_CHAT_ENV = frozenset(
    {
        # Platform-owned model gateway credentials are simulator configuration,
        # not target-agent or callback credentials.  The isolated worker needs
        # them when the Claude SDK wire protocol is routed to a non-Claude model.
        "AGENTCC_API_KEY",
        "AGENTCC_BASE_URL",
        "ALK_CLAUDE_GATEWAY_API_KEY",
        "ALK_CLAUDE_GATEWAY_URL",
        "ALK_HARNESS",
        GOOGLE_ADK_APPROVAL_ENV,
        "ALK_HARNESS_MODEL",
        "ALK_HARNESS_THINKING",
        "ALK_VERTEX_LOCATION",
        "ALK_CHAT_TARGET_TIMEOUT_SECONDS",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_VERTEX_PROJECT_ID",
        "CLOUD_ML_REGION",
        "ALK_STAGE_IDLE_TIMEOUT_SECONDS",
        "ALK_STAGE_IDLE_RETRIES",
        # Not a credential: it tells the worker's usage records who pays for the simulator.
        "ALK_SIMULATOR_FUNDING",
    }
)


class IsolatedChatCallRunner:
    # A stop reaches the worker, which seals the call it cut; the scheduler gives it the seal bound to do so.
    stops_at_turn_boundary = True

    def __init__(self, adapter: ArtifactUploader, context: CallRunnerContext) -> None:
        from .call_runner import TrialRecords, sealed_seed

        self._adapter = adapter
        self._context = context
        # Each trial runs in a worker of its own, so whether it started from the seed is read here, before its setup.
        self._records = TrialRecords(sealed_seed(context.work_directory))

    async def before_setup(self, runtime: EnvironmentRuntime) -> None:
        """Read the trial's start on its reset world, before the scenario's setup writes."""
        world_db = runtime.endpoints.get("world_db")
        dsn = world_db.address if world_db is not None and world_db.protocol == "postgres" else None
        await asyncio.to_thread(self._records.before_setup, id(runtime), dsn)

    async def run(
        self,
        scenario: Scenario,
        runtime: EnvironmentRuntime,
        *,
        world: World | None = None,
    ) -> CallOutcome:
        context = self._context
        environ = _allowlisted_ambient_env(dict(os.environ))
        environ.update(
            {key: value for key, value in os.environ.items() if key in _CHAT_ENV}
        )
        environ.update(
            {
                key: value
                for key, value in context.simulator_provider_secret_values.items()
                if key in _CHAT_ENV
            }
        )
        # The reporter cannot cross into the worker: the plan is checked here, and what the
        # worker's call used is recorded here.
        funding = simulator_funding()
        await _check_text_usage(context.usage_reporter, funding)
        mode = isolation_mode()
        # Under per-user isolation the tool handlers, and the agent code they import, run in a
        # child of their own; the worker gets its socket and never the source.
        handlers = await asyncio.to_thread(handler_child_for, context, runtime)
        handler_fd = handlers.sock.fileno() if handlers is not None else None
        try:
            result = await run_json_worker(
                "fi.alk.harness.chat_worker",
                {
                    "job": context.job.model_dump(mode="json"),
                    "bundle_dir": str(context.bundle_dir),
                    "work_directory": str(context.work_directory),
                    "source_directory": str(context.source_directory)
                    if context.source_directory and mode is not IsolationMode.PER_USER
                    else None,
                    "process_isolation": mode.value,
                    "handler_fd": handler_fd,
                    "target_secrets": dict(context.target_provider_secret_values),
                    "local_auth_values": dict(runtime._local_auth_values),
                    "attempt_number": context.attempt_number,
                    "evidence_seam": context.evidence_seam.value if context.evidence_seam else None,
                    "runtime": runtime.model_dump(mode="json"),
                    "scenario_key": scenario.scenario_key,
                    "source_scenario_key": getattr(
                        scenario, "source_scenario_key", None
                    ),
                    "scenario_id": scenario.scenario_id,
                    "start": self._records.start_of(id(runtime)),
                },
                environ=environ,
                work_directory=context.work_directory / "chat-workers",
                stop_requested=context.stop_requested,
                pass_fds=(handler_fd,) if handler_fd is not None else (),
            )
        except IsolatedWorkerError:
            if not context.stop_requested():
                raise
            # Stopped before the worker could seal anything: it was still starting, or failed on the way out.
            raise CallCancelled() from None
        finally:
            # This side's copy of the socket goes with the child, once the worker is done.
            if handlers is not None:
                handlers.close()
        # The worker sealed the call: what follows delivers it, and a stop no longer cuts that.
        call_sealed()
        await _record_worker_usage(context.usage_reporter, result.get("usage") or [], funding)
        artifact_ids = {}
        artifacts = list(result.get("artifacts", []))
        # A transcript names the trial's end state by the worker's own id, so the end state is stored before it.
        ends = [one for one in artifacts if one["kind"] == ArtifactKind.WORLD_STATE.value]
        for artifact in ends + [one for one in artifacts if one not in ends]:
            data = base64.b64decode(artifact["data"], validate=True)
            if artifact["kind"] == "transcript":
                data = _with_stored_end_state(data, artifact_ids)
            artifact_ids[artifact["id"]] = await self._adapter.upload_artifact(
                data,
                kind=ArtifactKind(artifact["kind"]),
                scenario_key=scenario.scenario_key,
            )
        raw_outcome = result.get("partial") if "error" in result else result["outcome"]
        outcome = None
        if raw_outcome is not None:
            raw_outcome["calls"] = tuple(Call(**call) for call in raw_outcome["calls"])
            raw_outcome["recording_artifacts"] = tuple(
                artifact_ids[item]
                for item in raw_outcome.get("recording_artifacts", [])
            )
            raw_outcome["transcript_artifact"] = artifact_ids.get(
                raw_outcome["transcript_artifact"]
            )
            outcome = CallOutcome(**raw_outcome)
        if result.get("error_code") == USER_CANCELED:
            # The worker sealed the call the person stopped: what it kept goes with the stop.
            raise CallCancelled(partial=outcome)
        if "error" in result:
            raise CallAborted(
                f"chat_worker_failed: {result['error']}",
                marker=result.get("error_marker"),
                code=result.get("error_code") or "call_failed",
                partial=outcome,
            )
        return outcome


def _with_stored_end_state(data: bytes, artifact_ids: dict[str, Any]) -> bytes:
    """A transcript whose World changes name the end state by the worker's id, naming it by the id it was stored
    under instead; any other transcript is returned as it was."""
    try:
        document = json.loads(data)
    except ValueError:
        return data
    world = document.get("world_actors") if isinstance(document, dict) else None
    local = world.get("end_state_artifact") if isinstance(world, dict) else None
    if local is None:
        return data
    stored = artifact_ids.get(str(local))
    if stored:
        world["end_state_artifact"] = stored
    else:
        world.pop("end_state_artifact", None)
    return json.dumps(document, sort_keys=True, default=str).encode("utf-8")


async def _record_worker_usage(
    reporter: Any, records: list[dict[str, Any]], funding: str
) -> None:
    """Record what the worker's call used, under the funding this process decided."""
    if reporter is None:
        return
    for facts in records:
        try:
            await asyncio.to_thread(
                reporter.record,
                **{
                    **facts,
                    "funding": funding,
                    "occurred_at": datetime.fromisoformat(facts["occurred_at"]),
                },
            )
        except Exception:  # noqa: BLE001 - a metering failure must not hide the call's result
            logger.exception(
                "Could not record hosted text usage for scenario %s",
                facts.get("scenario_key"),
            )


class _UsageCollector:
    """The worker's usage reporter: it keeps each record for the parent to make.

    The parent checked the plan before it started the worker, so ``check`` passes."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def check(self, _action: str) -> None:
        return

    def record(self, **facts: Any) -> None:
        self.records.append({**facts, "occurred_at": facts["occurred_at"].isoformat()})




class _ArtifactCollector:
    def __init__(self) -> None:
        self.artifacts: list[dict[str, Any]] = []

    async def upload_artifact(
        self, data: bytes, *, kind: ArtifactKind, **_kwargs: Any
    ) -> str:
        identifier = str(len(self.artifacts))
        self.artifacts.append(
            {
                "id": identifier,
                "kind": kind.value,
                "data": base64.b64encode(data).decode("ascii"),
            }
        )
        return identifier


async def main() -> None:
    from .chat_call_runner import HostedChatCallRunner
    from .hosted_agent import CONNECTOR as HOSTED_AGENT
    from .hosted_agent import HostedAgentCallRunner
    from .job import HarnessJob
    from .retell_chat_call_runner import RetellChatCallRunner

    payload = json.load(sys.stdin)
    collector = _ArtifactCollector()
    usage = _UsageCollector()
    try:
        isolated = payload.get("process_isolation") == IsolationMode.PER_USER.value
        native = (
            payload["runtime"].get("metadata", {}).get("observation_mode") == "native"
        )
        if isolated and (
            payload.get("source_directory")
            or (payload.get("handler_fd") is None and not native)
        ):
            # The tool handlers, and the agent's code they import, run only in their handler
            # child; this process never runs them as the harness.
            raise CallAborted(
                "chat_worker_refused: per-user isolation runs tool handlers only in their child",
                code="call_failed",
            )
        handler_fd = payload.get("handler_fd")
        job = HarnessJob.model_validate(payload["job"])
        context = CallRunnerContext(
            job=job,
            bundle_dir=Path(payload["bundle_dir"]),
            work_directory=Path(payload["work_directory"]),
            evidence_seam=EvidenceSeam(payload["evidence_seam"]) if payload.get("evidence_seam") else None,
            target_provider_secret_values=payload["target_secrets"],
            attempt_number=payload["attempt_number"],
            source_directory=Path(payload["source_directory"])
            if payload["source_directory"]
            else None,
            usage_reporter=usage,  # type: ignore[arg-type]
            stop_requested=stop_requested,
            handler_channel=HandlerChannel(socket.socket(fileno=handler_fd))
            if handler_fd is not None
            else None,
        )
        runner_type = {
            "retell_chat": RetellChatCallRunner,
            HOSTED_AGENT: HostedAgentCallRunner,
        }.get(job.agent.connector, HostedChatCallRunner)
        runtime = EnvironmentRuntime.model_validate(payload["runtime"])
        runtime._local_auth_values = dict(payload.get("local_auth_values", {}))
        runner = runner_type(collector, context)
        started = payload.get("start")
        if isinstance(started, dict) and isinstance(runner, HostedChatCallRunner):
            runner.started_before_setup(runtime, started)
        outcome = await runner.run(
            SimpleNamespace(
                scenario_key=payload["scenario_key"],
                source_scenario_key=payload.get("source_scenario_key"),
                scenario_id=payload["scenario_id"],
            ),
            runtime,
        )
        result = {"outcome": asdict(outcome), "artifacts": collector.artifacts}
    except CallAborted as exc:
        # CallAborted messages and structured failure fields are already designed for
        # result receipts. Preserve them across the worker boundary; returning only the
        # exception class made every chat failure indistinguishable and discarded useful
        # retry/attribution codes. Arbitrary exceptions remain type-only below.
        result = {
            "error": str(exc),
            "error_code": exc.code,
            "error_marker": exc.marker,
        }
        if exc.partial is not None:
            result["partial"] = asdict(exc.partial)
        if collector.artifacts:
            result["artifacts"] = collector.artifacts
    except asyncio.CancelledError:
        if not stop_requested():
            raise
        # Stopped outside the conversation (before it began, or while sealing): nothing sealed to return.
        result = {"error": USER_CANCELED, "error_code": USER_CANCELED}
    except Exception as exc:  # noqa: BLE001 - return a secret-safe worker failure
        result = {"error": type(exc).__name__}
    result["usage"] = usage.records
    fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(result, stream, default=str)


if __name__ == "__main__":
    run_worker(main, cancel_on_stop=True)
