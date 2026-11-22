"""`call_runner.py` against in-memory fakes and scripted `SimulationReport`s -- no real LiveKit
call, no real postgres, no monkeypatching LiveKit internals. Matches this repo's own convention
(`test_hosted_entrypoint.py`'s docstring): `asyncio.run` drives every `async def` seam directly.

The test seam is `CallRunnerImpl`'s own boundary: the injectable `place_call(spec) ->
SimulationReport` callable named by the brief. Real production code (`_default_place_call`)
builds the same `SimulationSpec`/`SimulationRunner().run` pair; these tests never construct or
touch a real `SimulationRunner`, `rtc.Room`, or `AgentSession`.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import sys
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from fi.alk.harness import call_runner as cr
from fi.alk.harness import chat_call_runner as chat
from fi.alk.harness.authoring_entrypoint import _load_provider_import_profile
from fi.alk.harness.bundle_author_v2 import resolve_environment_plan
from fi.alk.harness.bundle_v2 import EvidenceSeam
from fi.alk.harness.hosted_scheduler import CallAborted, CallOutcome
from fi.alk.harness.job import (
    AgentConnection,
    ExecutionMode,
    HarnessJob,
    ProviderExecutionMode,
    RepositorySource,
    SourceKind,
    SourceVisibility,
)
from fi.alk.harness.process_runtime import (
    EnvironmentRuntime,
    RuntimeEndpoint,
    RuntimeState,
)
from fi.alk.harness.world.errors import WorldUnavailable
from fi.alk.harness.world.runtime import Call, graded_calls
from fi.simulate.artifacts import ArtifactManifest
from fi.simulate.runtime.failures import FailureStage, SimulationFailure
from fi.simulate.runtime.report import SimulationReport, SimulationTestCaseResult
from fi.simulate.runtime.run import RunStatus
from fi.simulate.runtime.run import TestCaseStatus as CaseStatus
from fi.simulate.runtime.spec import RuntimeIsolation, RuntimeRequirements
from fi.simulate.simulation.models import Persona as SimPersona
from fi.simulate.simulation.models import TestCaseResult as SimTestCaseResult

LIVEKIT_API_KEY = "LIVEKIT_API_KEY"
LIVEKIT_API_SECRET = "LIVEKIT_API_SECRET"
DEEPGRAM_API_KEY = "DEEPGRAM_API_KEY"
GEMINI_API_KEY = "GEMINI_API_KEY"
RETELL_API_KEY = "RETELL_API_KEY"

_ALL_SECRETS = {
    LIVEKIT_API_KEY: "lk-key",
    LIVEKIT_API_SECRET: "lk-secret",
    DEEPGRAM_API_KEY: "dg-key",
    GEMINI_API_KEY: "gm-key",
}
_ALL_CONFIG = {cr.LIVEKIT_URL_CONFIG_KEY: "wss://example.livekit.cloud"}


# =================================================================================================
# Fixtures -- self-contained (this file touches nothing outside itself + call_runner.py).
# =================================================================================================


def _job(
    *,
    connector: str = "livekit",
    config: dict[str, Any] | None = None,
    execution: ExecutionMode = ExecutionMode.HOSTED,
    mode: ProviderExecutionMode | None = None,
) -> HarnessJob:
    return HarnessJob(
        job_id="job-abcdef12-xyz",
        run_id="run-1",
        execution=execution,
        source=(
            RepositorySource(
                kind=SourceKind.LOCAL_REPOSITORY,
                local_path="/tmp/agent",
                visibility=SourceVisibility.PRIVATE,
            )
            if execution is ExecutionMode.LOCAL
            else RepositorySource(
                kind=SourceKind.GITHUB,
                repository="org/repo",
                visibility=SourceVisibility.PUBLIC,
                commit_sha="a" * 40,
            )
        ),
        agent=AgentConnection(connector=connector, mode=mode, config=config or {}),
        scenario_count=1,
        runtime=RuntimeRequirements(
            isolation=RuntimeIsolation.DEDICATED_VM,
            cpu_units=1,
        ),
        seed=1,
        # A hosted job plays actors and faults only when its platform turns them on.
        metadata={"actors": {"enabled": True, "share": [0.15, 0.25]}},
    )


def _runtime(
    *,
    metadata: dict[str, Any] | None = None,
    endpoints: dict[str, RuntimeEndpoint] | None = None,
    world_index: int = 0,
) -> EnvironmentRuntime:
    return EnvironmentRuntime(
        runtime_id="rt-1",
        world_index=world_index,
        bundle_digest="sha256:" + "0" * 64,
        state=RuntimeState.READY,
        endpoints=endpoints or {},
        metadata=metadata or {},
    )


def _postgres_endpoint(
    *, address: str = "postgresql://harness:pw@localhost:15001/w0"
) -> RuntimeEndpoint:
    return RuntimeEndpoint(
        capability="database",
        protocol="postgres",
        address=address,
        configuration_name="DATABASE_URL",
    )


def test_file_tool_trace_is_collected_from_runtime_metadata(tmp_path: Path) -> None:
    trace = tmp_path / "agent-tool-calls.jsonl"
    trace.write_text(
        json.dumps(
            {
                "name": "process_request",
                "arguments": '{"request":"example"}',
                "output": {"request_id": "request-1"},
                "is_error": False,
                "agent_id": "run-worker-7",
                "agent_key": "worker-a",
                "agent_role": "Request processor",
                "task_id": "task-4",
                "task_name": "Process request",
            }
        )
        + "\n"
        + json.dumps(
            {
                "name": "write_record",
                "arguments": {},
                "output": "declined",
                "is_error": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    calls = cr._collect_file_tool_calls(
        _runtime(metadata={"tool_trace_path": str(trace)})
    )
    assert [call.name for call in calls] == ["process_request", "write_record"]
    assert calls[0].arguments == {"request": "example"}
    assert calls[0].ok is True
    assert calls[0].worker_id == "worker-a"
    assert calls[0].worker_label == "Request processor"
    assert calls[0].task_id == "task-4"
    assert calls[0].task_label == "Process request"
    assert calls[1].ok is False
    assert calls[1].error == "declined"


def test_the_sealed_trace_row_keeps_the_runtime_worker_task_and_event_ids(
    tmp_path: Path,
) -> None:
    from fi.alk.harness.world.runtime import trace_row

    trace = _trace_lines(
        tmp_path / "agent-tool-calls.jsonl",
        {
            "name": "lookup",
            "arguments": {},
            "output": "found",
            "is_error": False,
            "source": "crewai",
            "agent_key": "researcher",
            "task_name": "Find weather",
            "event_id": "e2",
            "parent_event_id": "e1",
        },
        {"name": "lookup", "arguments": {}, "output": "found", "is_error": False},
    )
    observed, plain = (
        trace_row(call)
        for call in cr._collect_file_tool_calls(
            _runtime(metadata={"tool_trace_path": str(trace)})
        )
    )

    assert (observed["worker_id"], observed["task_label"]) == (
        "researcher",
        "Find weather",
    )
    assert observed["runtime_ids"] == {"event_id": "e2", "parent_event_id": "e1"}
    assert not {
        "worker_id",
        "worker_label",
        "task_id",
        "task_label",
        "runtime_ids",
    } & set(plain)


# A worker's run, as a CrewAI agent records it: a hand-off, not a call checks grade.
_HANDOFF_LINE = {
    "name": "Draft checker",
    "arguments": {},
    "output": "valid",
    "is_error": False,
    "source": "crewai",
    "agent_key": "DraftFlow",
    "delegated_to": "Draft checker",
    "at": 4.0,
    "ended_at": 6.5,
    "kind": "handoff",
}

_SPAN_LINES = (
    {
        "name": "ask_writer",
        "arguments": {},
        "output": None,
        "is_error": False,
        "source": "otel",
        "agent_key": "Coordinator",
        "delegated_to": "Writer",
        "at": 1.0,
        "ended_at": 3.0,
    },
    {
        "name": "draft_note",
        "arguments": {},
        "output": None,
        "is_error": False,
        "source": "otel",
        "agent_key": "Writer",
        "at": 2.0,
    },
)


def _observed() -> list[Call]:
    # Stamped once their results were read, later than they started and in the other order.
    return [
        Call(
            name="ask_writer",
            arguments={"topic": "tides"},
            result="done",
            ok=True,
            at=9.2,
        ),
        Call(
            name="draft_note",
            arguments={"topic": "tides"},
            result="drafted",
            ok=True,
            at=9.1,
        ),
    ]


def test_span_lines_name_the_agent_of_calls_another_source_observed_and_add_none(
    tmp_path: Path,
) -> None:
    trace = _trace_lines(
        tmp_path / "agent-tool-calls.jsonl",
        *_SPAN_LINES,
        {"name": "draft_note", "arguments": {}, "output": "written", "is_error": False},
    )
    calls = cr._with_span_calls(
        _observed(), _runtime(metadata={"tool_trace_path": str(trace)})
    )
    assert [
        (c.name, c.arguments, c.worker_id, c.delegated_to, c.at, c.ended_at)
        for c in calls
    ] == [
        ("ask_writer", {"topic": "tides"}, "Coordinator", "Writer", 1.0, 3.0),
        ("draft_note", {"topic": "tides"}, "Writer", "", 2.0, 0.0),
    ]


def test_a_runtime_s_hand_off_keeps_when_the_worker_returned_and_is_never_graded(
    tmp_path: Path,
) -> None:
    # CrewAI's observer writes each agent a flow runs as a hand-off, with its own start and end.
    from fi.alk.harness.world.runtime import graded_calls, trace_row

    trace = _trace_lines(
        tmp_path / "agent-tool-calls.jsonl",
        {
            "name": "Draft checker",
            "arguments": {},
            "output": "valid",
            "is_error": False,
            "source": "crewai",
            "agent_key": "DraftFlow",
            "delegated_to": "Draft checker",
            "at": 4.0,
            "ended_at": 6.5,
            "kind": "handoff",
        },
    )
    calls = cr._collect_file_tool_calls(
        _runtime(metadata={"tool_trace_path": str(trace)})
    )
    (row,) = (trace_row(call) for call in calls)
    assert (
        row["worker_id"],
        row["delegated_to"],
        row["at"],
        row["ended_at"],
        row["kind"],
    ) == ("DraftFlow", "Draft checker", 4.0, 6.5, "delegated")
    assert graded_calls(calls) == []


def test_a_span_that_recorded_no_arguments_reads_as_none_not_as_raw_text(
    tmp_path: Path,
) -> None:
    trace = _trace_lines(
        tmp_path / "agent-tool-calls.jsonl",
        {**_SPAN_LINES[1], "arguments": "", "output": ""},
        {"name": "note", "arguments": "not json", "output": "", "is_error": False},
    )
    calls = cr._collect_file_tool_calls(
        _runtime(metadata={"tool_trace_path": str(trace)})
    )
    assert [(c.name, c.arguments) for c in calls] == [
        ("note", {"raw": "not json"}),
        ("draft_note", {}),
    ]


@pytest.mark.parametrize(
    "seam", [EvidenceSeam.TOOL_TRACE, EvidenceSeam.HTTP_TOOL, None]
)
def test_a_process_agent_s_seam_calls_keep_their_evidence_and_take_the_agent_from_its_spans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seam: EvidenceSeam | None
) -> None:
    trace = _trace_lines(tmp_path / "agent-tool-calls.jsonl", *_SPAN_LINES)
    for reader in ("_collect_tool_trace_calls", "_collect_http_tool_calls"):
        monkeypatch.setattr(cr, reader, lambda runtime, **_: tuple(_observed()))
    _job_obj, context = _context(tmp_path=tmp_path, evidence_seam=seam)
    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=None)
    calls = runner._collect_calls(_runtime(metadata={"tool_trace_path": str(trace)}))
    # With no seam, the spans are the only evidence.
    assert [(c.name, c.result, c.worker_id) for c in calls] == [
        ("ask_writer", "done" if seam else None, "Coordinator"),
        ("draft_note", "drafted" if seam else None, "Writer"),
    ]


def test_a_result_traced_as_the_text_of_a_dict_reaches_checks_as_the_dict(tmp_path: Path) -> None:
    trace = tmp_path / "agent-tool-calls.jsonl"
    trace.write_text(
        "".join(
            json.dumps({"name": "verify_otp", "arguments": {}, "output": output, "is_error": False}) + "\n"
            for output in ("{'verified': True}", '{"verified": true}', "sent")
        ),
        encoding="utf-8",
    )
    calls = cr._collect_file_tool_calls(_runtime(metadata={"tool_trace_path": str(trace)}))
    assert [call.result for call in calls] == [{"verified": True}, {"verified": True}, "sent"]


def _trace_lines(path: Path, *records: dict) -> Path:
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    return path


def test_file_tool_trace_merges_agent_line_with_livekit_hook_time(
    tmp_path: Path,
) -> None:
    # The agent writes what it executed while the tool runs; the hook writes the model's
    # call, with its time, after the step. They are one call.
    trace = _trace_lines(
        tmp_path / "agent-tool-calls.jsonl",
        {
            "name": "cancel_ride",
            "arguments": {"booking_ref": "bk_1", "caller_explicitly_confirmed": True},
            "output": {"status": "cancelled"},
            "is_error": False,
        },
        {
            "name": "cancel_ride",
            "arguments": '{"caller_explicitly_confirmed": true}',
            "output": "{'status': 'cancelled'}",
            "is_error": False,
            "at": 134.9,
            "source": "livekit",
        },
    )
    calls = cr._collect_file_tool_calls(
        _runtime(metadata={"tool_trace_path": str(trace)})
    )
    assert [(call.arguments, call.result, call.at) for call in calls] == [
        (
            {"booking_ref": "bk_1", "caller_explicitly_confirmed": True},
            {"status": "cancelled"},
            134.9,
        )
    ]


@pytest.mark.parametrize("hook_first", [False, True], ids=["agent line first", "hook line first"])
def test_file_tool_trace_counts_each_call_once_whichever_line_comes_first_and_however_the_agent_names_it(
    tmp_path: Path, hook_first: bool
) -> None:
    # The agent's own line names the world route it reached; the hook names the tool the model called.
    agent = [
        {"name": "prepare_cancellation", "arguments": {}, "output": {"confirmation_token": "t1"}, "is_error": False},
        {
            "name": "request_policy_cancellation",
            "arguments": {"confirmation_token": "t1", "reason": "moving", "policy_id": "pol_elena"},
            "output": {"detail": "declined"},
            "is_error": False,
        },
    ]
    hook = [
        {"name": "prepare_cancellation", "arguments": "{}", "output": "{}", "is_error": False, "at": 10.0,
         "source": "livekit"},
        {"name": "cancel_policy", "arguments": '{"confirmation_token": "t1", "reason": "moving"}',
         "output": "{}", "is_error": False, "at": 20.0, "source": "livekit"},
    ]
    trace = _trace_lines(tmp_path / "agent-tool-calls.jsonl", *(hook + agent if hook_first else agent + hook))

    written, _ = cr._read_file_tool_calls(
        _runtime(metadata={"tool_trace_path": str(trace)}),
        routes={"cancel_policy": "request_policy_cancellation"},
    )

    assert sorted((call.name, call.at, call.arguments.get("policy_id")) for call in written) == [
        ("cancel_policy", 20.0, "pol_elena"),
        ("prepare_cancellation", 10.0, None),
    ]


def test_file_tool_trace_counts_once_a_local_tool_the_agent_records_from_two_places(tmp_path: Path) -> None:
    # As the insurance agent writes a state-only tool: its tools client records it, and so does its own
    # function_tools_executed hook; LiveKit's hook then times it once. One call.
    prepared = {"confirmation_token": "bXB3", "summary_to_read": "Cancel MM-5590318 on 2027-01-15."}
    trace = _trace_lines(
        tmp_path / "agent-tool-calls.jsonl",
        {"name": "prepare_cancellation", "arguments": {}, "output": prepared, "is_error": False},
        {"name": "prepare_cancellation", "arguments": {}, "output": prepared, "is_error": False},
        {"name": "prepare_cancellation", "arguments": "{}", "output": str(prepared), "is_error": False, "at": 12.5,
         "source": "livekit"},
    )

    written, _ = cr._read_file_tool_calls(_runtime(metadata={"tool_trace_path": str(trace)}))

    assert [(call.name, call.at) for call in written] == [("prepare_cancellation", 12.5)]


def test_file_tool_trace_keeps_two_calls_of_one_tool_the_hook_timed_twice(tmp_path: Path) -> None:
    prepared = {"confirmation_token": "t1"}
    line = {"name": "prepare_cancellation", "arguments": {}, "output": prepared, "is_error": False}
    hook = {"name": "prepare_cancellation", "arguments": "{}", "output": "{}", "is_error": False, "source": "livekit"}
    trace = _trace_lines(
        tmp_path / "agent-tool-calls.jsonl",
        line, line, {**hook, "at": 5.0}, line, line, {**hook, "at": 9.0},
    )

    written, _ = cr._read_file_tool_calls(_runtime(metadata={"tool_trace_path": str(trace)}))

    assert sorted(call.at for call in written) == [5.0, 9.0]


def test_file_tool_trace_counts_lines_with_one_call_id_as_one_call(tmp_path: Path) -> None:
    prepared = {"confirmation_token": "t1"}
    line = {"name": "prepare_cancellation", "arguments": {}, "output": prepared, "is_error": False}
    trace = _trace_lines(
        tmp_path / "agent-tool-calls.jsonl",
        {**line, "call_id": "call_a"},
        {**line, "call_id": "call_a"},
        {**line, "call_id": "call_b"},
        {"name": "prepare_cancellation", "arguments": "{}", "output": "{}", "is_error": False, "at": 7.0,
         "source": "livekit", "call_id": "call_b"},
    )

    written, _ = cr._read_file_tool_calls(_runtime(metadata={"tool_trace_path": str(trace)}))

    assert [(cr._call_id(call), call.at) for call in written] == [("call_a", 0.0), ("call_b", 7.0)]
    assert cr.repeated_untimed_notices(written) == []


def test_identical_untimed_lines_with_no_call_id_stay_two_calls_and_say_so(tmp_path: Path) -> None:
    # A trial's trace as the insurance agent wrote it: the hook did not time this call, and the agent's two
    # recorders wrote it twice with no call id. Nothing tells one call written twice from two calls.
    phantom = {
        "name": "prepare_cancellation",
        "arguments": {},
        "output": {
            "confirmation_token": "D_L4o2ag2yb0DTRk",
            "summary_to_read": "cancelling policy MM-4471902, which ends the cover on it",
        },
        "is_error": False,
    }
    trace = _trace_lines(
        tmp_path / "agent-tool-calls.jsonl",
        phantom,
        phantom,
        {"name": "verify_identity", "arguments": {"policy_number": "MM-4471902"}, "output": {"policy_id": "pol_dana"},
         "is_error": False, "at": 1794863063.19, "source": "livekit"},
    )

    written, _ = cr._read_file_tool_calls(_runtime(metadata={"tool_trace_path": str(trace)}))

    assert [call.name for call in written] == ["prepare_cancellation", "prepare_cancellation", "verify_identity"]
    [notice] = cr.repeated_untimed_notices(written)
    assert notice["code"] == cr.CALLS_MAYBE_REPEATED
    assert "prepare_cancellation" in notice["message"] and "no call id" in notice["message"]
    assert notice["code"] not in cr.UNMEASURED_NOTICES


def test_file_tool_trace_keeps_a_rejected_call_apart_from_a_same_name_success(
    tmp_path: Path,
) -> None:
    # LiveKit rejects malformed arguments before the agent function runs, so only the
    # successful call of the batch has an agent line, but the hook writes both.
    trace = _trace_lines(
        tmp_path / "agent-tool-calls.jsonl",
        {
            "name": "get_weather",
            "arguments": {"city": "Paris"},
            "output": {"forecast": "sun"},
            "is_error": False,
        },
        {
            "name": "get_weather",
            "arguments": '{"city": ',
            "output": "invalid arguments",
            "is_error": True,
            "at": 100.0,
            "source": "livekit",
        },
        {
            "name": "get_weather",
            "arguments": '{"city": "Paris"}',
            "output": "{'forecast': 'sun'}",
            "is_error": False,
            "at": 101.0,
            "source": "livekit",
        },
    )
    calls = cr._collect_file_tool_calls(
        _runtime(metadata={"tool_trace_path": str(trace)})
    )
    assert sorted((call.at, call.ok, call.result) for call in calls) == [
        (100.0, False, None),
        (101.0, True, {"forecast": "sun"}),
    ]


def test_file_tool_trace_clear_removes_previous_attempt(tmp_path: Path) -> None:
    trace = tmp_path / "agent-tool-calls.jsonl"
    trace.write_text("stale\n", encoding="utf-8")
    cr._clear_file_tool_calls(_runtime(metadata={"tool_trace_path": str(trace)}))
    assert not trace.exists()


def test_provider_reported_tool_call_is_used_when_guest_trace_is_empty(
    tmp_path: Path,
) -> None:
    _job_obj, context = _context(
        tmp_path=tmp_path,
        connector="retell",
        mode=ProviderExecutionMode.PROVIDER_IMPORT,
        config={"agent_id": "source-retell-agent"},
        secrets={RETELL_API_KEY: "retell-key"},
    )
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")

    async def place_call(spec):
        report = _report(
            transcript="user: window\nagent: saved",
            messages=[
                {"role": "user", "content": "window"},
                {"role": "assistant", "content": "saved"},
            ],
        )
        assert report.test_cases[0].result is not None
        report.test_cases[0].result.metadata["evidence"] = [
            {
                "adapter": "retell",
                "metadata": {
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "name": "record_preference",
                            "arguments": {"preference": "window"},
                            "result": {"recorded": True},
                            "ok": True,
                            "at": 4.25,
                        }
                    ]
                },
            }
        ]
        return report

    adapter = FakeAdapter()
    outcome = _run(
        cr.CallRunnerImpl(adapter, context, place_call=place_call),
        _FakeScenario("k1"),
        _runtime(metadata={"provider_target_id": "cloned-retell-agent"}),
    )

    assert len(outcome.calls) == 1
    assert outcome.calls[0].name == "record_preference"
    assert outcome.calls[0].arguments == {"preference": "window"}
    assert outcome.calls[0].result == {"recorded": True}
    assert any(kind is cr.ArtifactKind.TOOL_TRACE for kind, _, _ in adapter.uploads)


def _context(
    *,
    tmp_path: Path,
    connector: str = "livekit",
    config: dict[str, Any] | None = None,
    secrets: dict[str, str] | None = None,
    simulator_secrets: dict[str, str] | None = None,
    execution: ExecutionMode = ExecutionMode.HOSTED,
    evidence_seam: EvidenceSeam | None = EvidenceSeam.HTTP_TOOL,
    attempt_number: int = 1,
    mode: ProviderExecutionMode | None = None,
) -> tuple[HarnessJob, cr.CallRunnerContext]:
    job = _job(
        connector=connector,
        config=config if config is not None else dict(_ALL_CONFIG),
        execution=execution,
        mode=mode,
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    context = cr.CallRunnerContext(
        job=job,
        bundle_dir=bundle_dir,
        work_directory=tmp_path / "work",
        evidence_seam=evidence_seam,
        target_provider_secret_values=secrets
        if secrets is not None
        else dict(_ALL_SECRETS),
        attempt_number=attempt_number,
        simulator_provider_secret_values=(
            simulator_secrets
            if simulator_secrets is not None
            else {
                "SIMULATOR_LIVEKIT_URL": "wss://platform-livekit.example",
                "SIMULATOR_LIVEKIT_API_KEY": "platform-livekit-key",
                "SIMULATOR_LIVEKIT_API_SECRET": "platform-livekit-secret",
                "SIMULATOR_DEEPGRAM_API_KEY": _ALL_SECRETS[DEEPGRAM_API_KEY],
                "SIMULATOR_GEMINI_API_KEY": _ALL_SECRETS[GEMINI_API_KEY],
            }
        ),
    )
    return job, context


def _write_scenario_doc(
    bundle_dir: Path,
    *,
    scenario_key: str,
    folder_name: str | None = None,
    instruction: str = "Cancel order #42.",
    persona: dict[str, Any] | None = None,
    fixture: dict[str, Any] | None = None,
    tests: str = "",
) -> None:
    folder = bundle_dir / "scenarios" / (folder_name or scenario_key)
    folder.mkdir(parents=True, exist_ok=True)
    body = {
        "scenario_key": scenario_key,
        "scenario_id": "",
        "sub_goals": [],
        "instruction": instruction,
        "persona": persona,
        "fixture": fixture or {},
        "tests": tests,
    }
    (folder / "scenario.json").write_text(json.dumps(body), encoding="utf-8")


@dataclass
class _FakeScenario:
    """`CallRunnerImpl.run` reads only `.scenario_key` off the scheduler's `Scenario` protocol."""

    scenario_key: str
    scenario_id: str = ""


@dataclass
class FakeAdapter:
    """Narrow `ArtifactUploader` fake -- records every call, returns a real `sha256:<hex>` id
    unless the kind is in `refuse_kinds` (mirrors the real adapter's null-not-crash contract)."""

    refuse_kinds: frozenset = field(default_factory=frozenset)
    uploads: list[tuple[Any, str | None, bytes]] = field(default_factory=list)

    async def upload_artifact(
        self, data: bytes, *, kind, scenario_key=None, deadline=None
    ) -> str | None:
        if kind in self.refuse_kinds:
            return None
        import hashlib

        digest = hashlib.sha256(data).hexdigest()
        self.uploads.append((kind, scenario_key, data))
        return f"sha256:{digest}"


def _persona() -> SimPersona:
    return SimPersona(persona={"name": "customer"}, situation="s", outcome="o")


def _report(
    *,
    status: RunStatus = RunStatus.COMPLETED,
    case_status: CaseStatus | None = CaseStatus.COMPLETED,
    transcript: str = "hello there",
    messages: list[dict[str, str]] | None = None,
    failure: SimulationFailure | None = None,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    no_cases: bool = False,
    run_id: str = "sim-run-1",
    result_metadata: dict[str, Any] | None = None,
) -> SimulationReport:
    messages = messages if messages is not None else [{"role": "user", "content": "hi"}]
    started_at = started_at or datetime.now(timezone.utc)
    ended_at = ended_at or (started_at + timedelta(seconds=30))
    cases: list[SimulationTestCaseResult] = []
    if not no_cases:
        assert case_status is not None
        result = SimTestCaseResult(
            persona=_persona(),
            transcript=transcript,
            messages=messages,
            metadata=result_metadata or {},
        )
        cases.append(
            SimulationTestCaseResult(
                test_case_id="tc-1",
                status=case_status,
                persona=_persona(),
                result=result,
                failure=failure,
                started_at=started_at,
                ended_at=ended_at,
            )
        )
    return SimulationReport(
        run_id=run_id,
        spec_hash="hash",
        status=status,
        started_at=started_at,
        ended_at=ended_at,
        test_cases=cases,
        artifacts=ArtifactManifest(run_id=run_id),
    )


def _run(
    runner: cr.CallRunnerImpl, scenario: _FakeScenario, runtime: EnvironmentRuntime
) -> CallOutcome:
    return asyncio.run(runner.run(scenario, runtime))


def _run_expect_abort(
    runner: cr.CallRunnerImpl, scenario: _FakeScenario, runtime: EnvironmentRuntime
) -> CallAborted:
    try:
        asyncio.run(runner.run(scenario, runtime))
    except CallAborted as exc:
        return exc
    raise AssertionError("expected CallAborted, nothing was raised")


def _run_expect_world_unavailable(
    runner: cr.CallRunnerImpl, scenario: _FakeScenario, runtime: EnvironmentRuntime
) -> WorldUnavailable:
    try:
        asyncio.run(runner.run(scenario, runtime))
    except WorldUnavailable as exc:
        return exc
    raise AssertionError("expected WorldUnavailable, nothing was raised")


# =================================================================================================
# Room naming (deterministic scheme, pinned verbatim by this file). WHY only a prefix at the wire:
# engines/livekit.py::_resolve_room_name appends its own `-{invocation_id}-{test_case_id[-12:]}`
# suffix in managed room_mode unless `room_name_verbatim` is set (this runner does not set it), so
# the pinned string below is the deterministic PREFIX every dialed room carries, not the full
# on-the-wire room name.
# =================================================================================================


def test_room_name_matches_the_pinned_deterministic_scheme() -> None:
    name = cr._room_name(
        job_id="abcdef1234567890",
        attempt_number=2,
        scenario_key="cancel-order",
        scenario_attempt=3,
    )
    assert name == "harness-abcdef12-a2-cancel-order-s3"


def test_room_name_uses_only_the_first_eight_chars_of_job_id() -> None:
    short = cr._room_name(
        job_id="ab", attempt_number=1, scenario_key="k", scenario_attempt=1
    )
    assert short == "harness-ab-a1-k-s1"


# =================================================================================================
# Pre-dial validation.
# =================================================================================================


def test_missing_target_provider_secrets_aborts_pre_dial_without_calling_place_call(
    tmp_path: Path,
) -> None:
    called = False

    async def place_call(spec):
        nonlocal called
        called = True
        raise AssertionError("place_call must never be reached")

    _job_obj, context = _context(tmp_path=tmp_path, secrets={})
    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call)
    exc = _run_expect_abort(
        runner,
        _FakeScenario("k1"),
        _runtime(metadata={"livekit_agent_name": "agent-w0"}),
    )
    assert exc.partial is None
    assert str(exc).startswith("voice_capability_unavailable: missing")
    assert LIVEKIT_API_KEY in str(exc)
    assert not called


def test_missing_llm_credential_names_the_either_or_pair(tmp_path: Path) -> None:
    _job_obj, context = _context(
        tmp_path=tmp_path,
        simulator_secrets={"SIMULATOR_DEEPGRAM_API_KEY": "dg-key"},
    )
    runner = cr.CallRunnerImpl(FakeAdapter(), context, environ={})
    exc = _run_expect_abort(
        runner,
        _FakeScenario("k1"),
        _runtime(metadata={"livekit_agent_name": "agent-w0"}),
    )
    assert "GEMINI_API_KEY_or_GOOGLE_API_KEY" in str(exc)


def test_google_api_key_alone_satisfies_the_llm_credential_check(
    tmp_path: Path,
) -> None:
    _job_obj, context = _context(
        tmp_path=tmp_path,
        simulator_secrets={
            "SIMULATOR_DEEPGRAM_API_KEY": "dg-key",
            "SIMULATOR_GOOGLE_API_KEY": "g-key",
        },
    )
    runner = cr.CallRunnerImpl(FakeAdapter(), context)
    assert runner._missing_config is None


def test_platform_barge_in_rate_reaches_call_subprocess(tmp_path: Path) -> None:
    _job_obj, context = _context(
        tmp_path=tmp_path,
        simulator_secrets={"HARNESS_CALLER_BARGE_IN_RATE": "0.2"},
    )
    runner = cr.CallRunnerImpl(FakeAdapter(), context, environ={})
    assert runner._environ["HARNESS_CALLER_BARGE_IN_RATE"] == "0.2"


def test_missing_livekit_url_config_aborts_pre_dial(tmp_path: Path) -> None:
    _job_obj, context = _context(tmp_path=tmp_path, config={})
    runner = cr.CallRunnerImpl(FakeAdapter(), context)
    exc = _run_expect_abort(
        runner,
        _FakeScenario("k1"),
        _runtime(metadata={"livekit_agent_name": "agent-w0"}),
    )
    assert "config=livekit_url" in str(exc)


def test_missing_dispatch_identity_metadata_aborts_pre_dial(tmp_path: Path) -> None:
    """Verified finding: `EnvironmentRuntime.metadata` is always `{}` in this worktree's HEAD --
    this is the realistic default a real hosted run hits today (CONTRACT NOTE 3)."""
    called = False

    async def place_call(spec):
        nonlocal called
        called = True
        raise AssertionError("place_call must never be reached")

    _job_obj, context = _context(tmp_path=tmp_path)
    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call)
    exc = _run_expect_abort(runner, _FakeScenario("k1"), _runtime(metadata={}))
    assert "voice_dispatch_identity_unavailable" in str(exc)
    assert "livekit_agent_name" in str(exc)
    assert not called


def test_missing_scenario_document_aborts_pre_dial(tmp_path: Path) -> None:
    _job_obj, context = _context(tmp_path=tmp_path)
    runner = cr.CallRunnerImpl(FakeAdapter(), context)
    exc = _run_expect_abort(
        runner,
        _FakeScenario("no-such-key"),
        _runtime(metadata={"livekit_agent_name": "agent-w0"}),
    )
    assert "voice_scenario_document_unavailable" in str(exc)


def test_scenario_document_matched_by_scenario_key_field_not_folder_name(
    tmp_path: Path,
) -> None:
    """scenario_source.py's own convention: `scenario_key` is a field INSIDE scenario.json, not
    necessarily the folder name -- this runner must match the same way, not assume they agree."""
    _job_obj, context = _context(tmp_path=tmp_path)
    _write_scenario_doc(
        context.bundle_dir,
        scenario_key="the-real-key",
        folder_name="some-other-folder-name",
    )

    captured: dict[str, Any] = {}

    async def place_call(spec):
        captured["spec"] = spec
        return _report()

    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call)
    _run(
        runner,
        _FakeScenario("the-real-key"),
        _runtime(metadata={"livekit_agent_name": "agent-w0"}),
    )
    assert captured["spec"] is not None


# =================================================================================================
# Happy path: COMPLETED -> real CallOutcome, artifacts uploaded, dispatch/room wiring correct.
# =================================================================================================


def test_completed_call_uploads_transcript_and_returns_populated_outcome(
    tmp_path: Path,
) -> None:
    _job_obj, context = _context(
        tmp_path=tmp_path, evidence_seam=EvidenceSeam.HTTP_TOOL
    )
    _write_scenario_doc(
        context.bundle_dir, scenario_key="k1", instruction="Cancel order #42."
    )

    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ended = started + timedelta(seconds=45)
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]

    async def place_call(spec):
        return _report(
            transcript="hi\nhello",
            messages=messages,
            started_at=started,
            ended_at=ended,
            result_metadata={"stop_reason": "simulator_end_call"},
        )

    adapter = FakeAdapter()
    runner = cr.CallRunnerImpl(adapter, context, place_call=place_call)
    outcome = _run(
        runner,
        _FakeScenario("k1"),
        _runtime(metadata={"livekit_agent_name": "agent-w0"}),
    )

    assert isinstance(outcome, CallOutcome)
    assert outcome.turns == 2
    assert outcome.duration_ms == 45_000
    assert outcome.transcript_artifact is not None
    assert outcome.transcript_artifact.startswith("sha256:")
    assert outcome.calls == ()  # http_tool: STOPPED, always zero -- CONTRACT NOTE 1
    assert outcome.stop_reason == "simulator_end_call"
    assert len(adapter.uploads) == 1


def test_dispatch_agent_name_and_livekit_url_flow_into_the_built_spec(
    tmp_path: Path,
) -> None:
    _job_obj, context = _context(
        tmp_path=tmp_path,
        config={cr.LIVEKIT_URL_CONFIG_KEY: "wss://custom.livekit.cloud"},
    )
    _write_scenario_doc(
        context.bundle_dir, scenario_key="k1", instruction="Do the thing."
    )

    captured: dict[str, Any] = {}

    async def place_call(spec):
        captured["spec"] = spec
        return _report()

    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call)
    _run(
        runner,
        _FakeScenario("k1"),
        _runtime(metadata={"livekit_agent_name": "agent-w0"}),
    )

    spec = captured["spec"]
    agent_definition = spec.environment.config["agent_definition"]
    livekit_runtime = spec.environment.config["livekit_runtime"]
    assert agent_definition["agent_name"] == "agent-w0"
    assert agent_definition["system_prompt"] == "Do the thing."
    # WHY prefix-match, not exact-match: this asserts what THIS runner puts on the spec, which is
    # exactly the pinned deterministic scheme -- but engines/livekit.py::_resolve_room_name appends
    # its own suffix in managed room_mode before the room is actually dialed, so a reader must not
    # take this string as the full on-the-wire room name.
    assert livekit_runtime["room_name"].startswith("harness-job-abcd-a1-k1-s1")
    assert livekit_runtime["url"] == "wss://custom.livekit.cloud/"
    assert (
        spec.environment.config["params"]["agent_first_silence_timeout_seconds"] == 60.0
    )
    assert spec.environment.config["params"]["min_turn_messages"] == 6


def test_the_spec_is_built_off_the_event_loop(tmp_path: Path, monkeypatch) -> None:
    """Choosing the caller's voice may wait on Cartesia; the scheduler's loop keeps running meanwhile."""
    import threading

    _job_obj, context = _context(tmp_path=tmp_path)
    _write_scenario_doc(context.bundle_dir, scenario_key="k1", instruction="Do the thing.")
    built = cr._build_spec
    seen: dict[str, Any] = {}

    def slow_build(**kwargs):
        ran = threading.Event()
        seen["loop"].call_soon_threadsafe(ran.set)
        # On the loop's own thread the callback could not run until this returned.
        seen["loop_ran"] = ran.wait(timeout=2)
        return built(**kwargs)

    async def place_call(spec):
        return _report()

    async def run():
        seen["loop"] = asyncio.get_running_loop()
        await runner.run(_FakeScenario("k1"), _runtime(metadata={"livekit_agent_name": "agent-w0"}))

    monkeypatch.setattr(cr, "_build_spec", slow_build)
    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call)
    asyncio.run(run())
    assert seen["loop_ran"] is True


@pytest.mark.parametrize("call_key", ["call-key", ""])
def test_the_voice_check_uses_the_calls_private_key(tmp_path: Path, monkeypatch, call_key) -> None:
    from fi.alk.harness import simulator_voice

    keys: list[str] = []
    monkeypatch.delenv("CARTESIA_API_KEY", raising=False)
    monkeypatch.setattr(simulator_voice, "_cartesia_voice_served", lambda voice, key: keys.append(key) or True)
    cr._build_spec(
        run_id="r1",
        room_name="room",
        agent_name="agent-w0",
        doc={"instruction": "Book a ride.", "persona": {"name": "Noor", "gender": "female"}},
        livekit_url="wss://example.livekit.cloud",
        call_timeout_seconds=60.0,
        run_seconds=60.0,
        recordings_root=tmp_path,
        simulator_config={"simulator_tts_provider": "cartesia"},
        environ={"CARTESIA_API_KEY": call_key, "DEEPGRAM_API_KEY": "dg"},
    )
    # The private per-call key reaches the check without being exported to this process.
    assert keys[:1] == [call_key]
    assert "CARTESIA_API_KEY" not in os.environ


@pytest.mark.parametrize(
    ("connector", "target_key", "target_id", "provider_key", "transport"),
    [
        ("vapi", "assistant_id", "assistant-123", "VAPI_API_KEY", "vapi_websocket"),
        ("retell", "agent_id", "agent-123", "RETELL_API_KEY", "retell_webcall"),
    ],
)
def test_provider_connect_only_builds_direct_target_spec(
    tmp_path: Path,
    connector: str,
    target_key: str,
    target_id: str,
    provider_key: str,
    transport: str,
) -> None:
    secrets = {**_ALL_SECRETS, provider_key: "provider-key"}
    _job_obj, context = _context(
        tmp_path=tmp_path,
        connector=connector,
        config={
            cr.LIVEKIT_URL_CONFIG_KEY: "wss://custom.livekit.cloud",
            target_key: target_id,
        },
        secrets=secrets,
    )
    _write_scenario_doc(context.bundle_dir, scenario_key="provider-call")
    captured: dict[str, Any] = {}

    async def place_call(spec):
        captured["spec"] = spec
        return _report()

    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call)
    _run(runner, _FakeScenario("provider-call"), _runtime())

    definition = captured["spec"].environment.config["agent_definition"]
    assert definition["target"][target_key] == target_id
    assert definition["target"]["provider"] == connector
    assert definition["transport"]["kind"] == transport
    # Provider-hosted targets own their end-call tool. Five alternating messages are sufficient
    # for a complete agent-first clarification flow; no sixth caller acknowledgement is possible
    # after the provider disconnects.
    assert captured["spec"].environment.config["params"]["min_turn_messages"] == 5


@pytest.mark.parametrize("connector", ["phone", "vapi", "retell"])
def test_phone_connect_only_dials_with_platform_sip_credentials(
    tmp_path: Path, connector: str
) -> None:
    _job_obj, context = _context(
        tmp_path=tmp_path,
        connector=connector,
        mode=ProviderExecutionMode.CONNECT_ONLY,
        config={
            "phone_number": "+14155551234",
            "target_system_prompt": "You help callers book appointments.",
            **(
                {"assistant_id": "existing-agent"}
                if connector == "vapi"
                else {"agent_id": "existing-agent"}
                if connector == "retell"
                else {}
            ),
        },
        secrets={"VAPI_API_KEY": "test-vapi", "RETELL_API_KEY": "test-retell"},
        simulator_secrets={
            "LIVEKIT_URL": "wss://platform-livekit.example",
            "LIVEKIT_API_KEY": "platform-livekit-key",
            "LIVEKIT_API_SECRET": "platform-livekit-secret",
            "DEEPGRAM_API_KEY": "platform-deepgram-key",
            "GEMINI_API_KEY": "platform-gemini-key",
            "SIP_OUTBOUND_TRUNK_ID": "trunk-platform",
            "SIP_OUTBOUND_FROM_NUMBER": "+14155550000",
        },
    )
    _write_scenario_doc(context.bundle_dir, scenario_key="phone-call")
    captured: dict[str, Any] = {}

    async def place_call(spec):
        captured["spec"] = spec
        return _report()

    runner = cr.CallRunnerImpl(
        FakeAdapter(), context, place_call=place_call, environ={}
    )
    _run(runner, _FakeScenario("phone-call"), _runtime())

    definition = captured["spec"].environment.config["agent_definition"]
    transport = definition["transport"]
    assert transport["kind"] == "sip_outbound"
    assert transport["sip_trunk_id"] == "trunk-platform"
    assert transport["sip_number"] == "+14155550000"
    assert transport["sip_call_to"] == "+14155551234"
    assert definition["system_prompt"] == "You help callers book appointments."
    assert captured["spec"].environment.config["params"]["min_turn_messages"] == 5


def test_phone_connect_only_authoring_uses_only_supplied_prompt() -> None:
    job = _job(
        connector="phone",
        mode=ProviderExecutionMode.CONNECT_ONLY,
        config={
            "phone_number": "+14155551234",
            "target_system_prompt": "You help callers book appointments.",
        },
    )
    assert _load_provider_import_profile(job, None) == {
        "provider": "phone",
        "modality": "voice",
        "phone_number": "+14155551234",
        "system_prompt": "You help callers book appointments.",
        "tools": [],
    }


_LIVEKIT_HOSTED_CONFIG = {
    cr.LIVEKIT_URL_CONFIG_KEY: "wss://customer.livekit.cloud",
    "agent_name": "returns-agent",
    "target_system_prompt": "You handle returns.",
}


@pytest.mark.parametrize(
    ("config", "error"),
    [
        (
            {"target_system_prompt": "You handle returns."},
            "livekit_connect_only_requires_agent_name",
        ),
        (
            {"agent_name": "returns-agent"},
            "livekit_connect_only_requires_target_system_prompt",
        ),
        (
            {"agent_name": "returns-agent", "target_system_prompt": "x" * 65537},
            "livekit_connect_only_requires_target_system_prompt",
        ),
    ],
)
def test_livekit_connect_only_requires_agent_name_and_prompt(
    config: dict[str, Any], error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        AgentConnection(
            connector="livekit", mode=ProviderExecutionMode.CONNECT_ONLY, config=config
        )


def test_livekit_connect_only_authoring_uses_the_pasted_prompt_and_drops_secrets(
    tmp_path: Path,
) -> None:
    secrets = tmp_path / "authoring-target-secrets.json"
    secrets.write_text('{"LIVEKIT_API_KEY": "lk-key"}', encoding="utf-8")
    job = _job(
        connector="livekit",
        mode=ProviderExecutionMode.CONNECT_ONLY,
        config=dict(_LIVEKIT_HOSTED_CONFIG),
    )

    assert _load_provider_import_profile(job, secrets) == {
        "provider": "livekit",
        "modality": "voice",
        "agent_name": "returns-agent",
        "system_prompt": "You handle returns.",
        "tools": [],
    }
    assert not secrets.exists()


def test_livekit_connect_only_dispatches_the_configured_agent_name(
    tmp_path: Path,
) -> None:
    _job_obj, context = _context(
        tmp_path=tmp_path,
        mode=ProviderExecutionMode.CONNECT_ONLY,
        config=dict(_LIVEKIT_HOSTED_CONFIG),
    )
    _write_scenario_doc(context.bundle_dir, scenario_key="livekit-call")
    captured: dict[str, Any] = {}

    async def place_call(spec):
        captured["spec"] = spec
        return _report()

    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call)
    _run(
        runner,
        _FakeScenario("livekit-call"),
        _runtime(metadata={"livekit_agent_name": "agent-from-source"}),
    )

    config = captured["spec"].environment.config
    assert config["agent_definition"]["agent_name"] == "returns-agent"
    assert config["livekit_runtime"]["url"] == "wss://customer.livekit.cloud/"


def test_phone_connect_only_rejects_customer_dialer_override() -> None:
    with pytest.raises(ValueError, match="phone_dialer_config_is_platform_owned"):
        AgentConnection(
            connector="phone",
            mode=ProviderExecutionMode.CONNECT_ONLY,
            config={
                "phone_number": "+14155551234",
                "target_system_prompt": "You help callers book appointments.",
                "sip_outbound_from_number": "+14155559999",
            },
        )


def test_phone_connect_only_does_not_launch_customer_source(tmp_path: Path) -> None:
    job = _job(
        connector="phone",
        mode=ProviderExecutionMode.CONNECT_ONLY,
        config={
            "phone_number": "+14155551234",
            "target_system_prompt": "You help callers book appointments.",
        },
    ).model_copy(
        update={
            "source": RepositorySource(
                kind=SourceKind.PROVIDER, visibility=SourceVisibility.PUBLIC
            )
        }
    )
    plan = resolve_environment_plan(tmp_path, job)
    assert plan.packaging == "provider_connect_only"
    assert plan.control_service is None


def test_phone_connect_only_requires_platform_sip_not_target_sip(
    tmp_path: Path,
) -> None:
    _job_obj, context = _context(
        tmp_path=tmp_path,
        connector="phone",
        mode=ProviderExecutionMode.CONNECT_ONLY,
        config={
            "phone_number": "+14155551234",
            "target_system_prompt": "You help callers book appointments.",
        },
        secrets={
            "SIP_OUTBOUND_TRUNK_ID": "customer-trunk",
            "SIP_OUTBOUND_FROM_NUMBER": "+14155550000",
        },
    )
    _write_scenario_doc(context.bundle_dir, scenario_key="phone-call")
    runner = cr.CallRunnerImpl(FakeAdapter(), context, environ={})
    with pytest.raises(CallAborted, match="SIP_OUTBOUND_TRUNK_ID"):
        _run(runner, _FakeScenario("phone-call"), _runtime())


def test_provider_import_calls_runtime_clone_instead_of_source_target(
    tmp_path: Path,
) -> None:
    _job_obj, context = _context(
        tmp_path=tmp_path,
        connector="vapi",
        mode=ProviderExecutionMode.PROVIDER_IMPORT,
        config={
            cr.LIVEKIT_URL_CONFIG_KEY: "wss://custom.livekit.cloud",
            "assistant_id": "source-assistant",
        },
        secrets={**_ALL_SECRETS, "VAPI_API_KEY": "provider-key"},
    )
    _write_scenario_doc(context.bundle_dir, scenario_key="provider-import-call")
    captured: dict[str, Any] = {}

    async def place_call(spec):
        captured["spec"] = spec
        return _report()

    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call)
    _run(
        runner,
        _FakeScenario("provider-import-call"),
        _runtime(metadata={"provider_target_id": "cloned-assistant"}),
    )

    definition = captured["spec"].environment.config["agent_definition"]
    assert definition["target"]["assistant_id"] == "cloned-assistant"


def test_scenario_attempt_counter_increments_per_scenario_key_across_retries(
    tmp_path: Path,
) -> None:
    """The scheduler retries the SAME scenario_key (e.g. after evidence_missing) -- successive
    `run()` calls for one key must get distinct room names, or a LiveKit room collision follows."""
    _job_obj, context = _context(tmp_path=tmp_path)
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")
    _write_scenario_doc(context.bundle_dir, scenario_key="k2")

    rooms: list[str] = []

    async def place_call(spec):
        rooms.append(spec.environment.config["livekit_runtime"]["room_name"])
        return _report()

    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call)
    runtime = _runtime(metadata={"livekit_agent_name": "agent-w0"})
    _run(runner, _FakeScenario("k1"), runtime)
    _run(runner, _FakeScenario("k2"), runtime)
    _run(runner, _FakeScenario("k1"), runtime)

    assert rooms[0].endswith("-k1-s1")
    assert rooms[1].endswith("-k2-s1")
    assert rooms[2].endswith("-k1-s2")


# =================================================================================================
# Failure semantics -- the three cases the brief pins.
# =================================================================================================


def test_agent_unavailable_status_raises_world_unavailable(tmp_path: Path) -> None:
    """Verified against engines/livekit.py: AGENT_UNAVAILABLE fires ONLY on a readiness-stage
    timeout with a session started but no target dispatched -- exactly "dispatch fails, agent
    never joins," matching the brief's explicit WorldUnavailable case."""
    _job_obj, context = _context(tmp_path=tmp_path)
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")

    async def place_call(spec):
        failure = SimulationFailure(
            stage=FailureStage.READINESS,
            code="agent_unavailable",
            message="Target agent did not become ready",
        )
        return _report(case_status=CaseStatus.AGENT_UNAVAILABLE, failure=failure)

    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call)
    exc = _run_expect_world_unavailable(
        runner,
        _FakeScenario("k1"),
        _runtime(metadata={"livekit_agent_name": "agent-w0"}),
    )
    assert "Target agent did not become ready" in str(exc)


def test_non_completed_status_raises_call_aborted_with_partial(tmp_path: Path) -> None:
    _job_obj, context = _context(tmp_path=tmp_path)
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ended = started + timedelta(seconds=12)

    async def place_call(spec):
        failure = SimulationFailure(
            stage=FailureStage.RUNNING, code="case_execution_error", message="boom"
        )
        return _report(
            case_status=CaseStatus.FAILED,
            failure=failure,
            started_at=started,
            ended_at=ended,
            transcript="",
            messages=[],
        )

    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call)
    exc = _run_expect_abort(
        runner,
        _FakeScenario("k1"),
        _runtime(metadata={"livekit_agent_name": "agent-w0"}),
    )
    assert exc.partial is not None
    assert exc.partial.duration_ms == 12_000
    assert exc.partial.calls == ()
    assert "boom" in str(exc)


def test_non_completed_tool_trace_call_preserves_evidence_and_uploads_trace(
    tmp_path: Path, monkeypatch
) -> None:
    """A failed voice call must not discard tool evidence collected before the failure."""
    _job_obj, context = _context(
        tmp_path=tmp_path, evidence_seam=EvidenceSeam.TOOL_TRACE
    )
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")
    captured = Call(
        name="get_ride_options",
        arguments={"pickup": "SFO"},
        result={"options": ["economy"]},
        ok=True,
        error=None,
        refused=False,
        at="2026-01-01T00:00:01.000Z",
    )
    monkeypatch.setattr(
        cr, "_collect_tool_trace_calls", lambda runtime, **_: (captured,)
    )

    async def place_call(spec):
        failure = SimulationFailure(
            stage=FailureStage.RUNNING,
            code="call_timeout",
            message="conversation exceeded its limit",
        )
        return _report(
            case_status=CaseStatus.FAILED,
            failure=failure,
            transcript="user: hello",
            messages=[{"role": "user", "content": "hello"}],
        )

    adapter = FakeAdapter()
    runner = cr.CallRunnerImpl(adapter, context, place_call=place_call)
    exc = _run_expect_abort(
        runner,
        _FakeScenario("k1"),
        _runtime(metadata={"livekit_agent_name": "agent-w0"}),
    )

    assert exc.partial is not None
    assert exc.partial.calls == (captured,)
    trace_uploads = [
        item for item in adapter.uploads if item[0] is cr.ArtifactKind.TOOL_TRACE
    ]
    assert len(trace_uploads) == 1
    assert (
        json.loads(trace_uploads[0][2].decode().splitlines()[0])["name"]
        == "get_ride_options"
    )


def test_no_test_cases_in_report_raises_call_aborted_with_timing_only_partial(
    tmp_path: Path,
) -> None:
    _job_obj, context = _context(tmp_path=tmp_path)
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")

    async def place_call(spec):
        return _report(status=RunStatus.TIMED_OUT, no_cases=True)

    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call)
    exc = _run_expect_abort(
        runner,
        _FakeScenario("k1"),
        _runtime(metadata={"livekit_agent_name": "agent-w0"}),
    )
    assert exc.partial is not None
    assert exc.partial.turns == 0
    assert exc.partial.calls == ()


def test_place_call_exception_raises_call_aborted_with_timing_partial_never_raw(
    tmp_path: Path,
) -> None:
    """world-handle-interface.md's partial-call rule: a generic exception must never lose timing
    (the brief: "never let a raw exception escape post-dial")."""
    _job_obj, context = _context(tmp_path=tmp_path)
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")

    async def place_call(spec):
        raise RuntimeError("engine exploded")

    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call)
    exc = _run_expect_abort(
        runner,
        _FakeScenario("k1"),
        _runtime(metadata={"livekit_agent_name": "agent-w0"}),
    )
    assert exc.partial is not None
    assert exc.partial.started_at is not None
    assert exc.partial.ended_at is not None
    assert "engine exploded" in str(exc)


def test_translate_report_upload_failure_raises_call_aborted_with_timing_partial_never_raw(
    tmp_path: Path,
) -> None:
    """The same partial-call rule as `place_call` failing above, but for a failure INSIDE
    `_translate_report` itself (a transcript/recording read or an `upload_artifact` surprise) --
    this must also never escape `run()` raw and lose the timing the call already measured."""
    _job_obj, context = _context(tmp_path=tmp_path)
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")

    class RaisingAdapter:
        async def upload_artifact(
            self, data, *, kind, scenario_key=None, deadline=None
        ):
            raise RuntimeError("upload exploded")

    async def place_call(spec):
        return _report(
            transcript="hi",
            messages=[
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "yo"},
            ],
        )

    runner = cr.CallRunnerImpl(RaisingAdapter(), context, place_call=place_call)
    exc = _run_expect_abort(
        runner,
        _FakeScenario("k1"),
        _runtime(metadata={"livekit_agent_name": "agent-w0"}),
    )
    assert exc.partial is not None
    assert exc.partial.started_at is not None
    assert exc.partial.ended_at is not None
    assert exc.partial.calls == ()
    assert "upload exploded" in str(exc)


def test_place_call_outer_timeout_raises_call_aborted_with_timing_partial(
    tmp_path: Path, monkeypatch
) -> None:
    """Forces the runner-owned `asyncio.wait_for` to actually fire (not just the SDK's own
    internal one) by shrinking every phase-overhead constant to a few milliseconds -- avoids a
    multi-minute real sleep in the test suite while still exercising the real timeout code path."""
    monkeypatch.setattr(cr, "CONNECT_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(cr, "READINESS_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(cr, "CLEANUP_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(cr, "_RUN_SECONDS_PAD_SECONDS", 0.01)
    monkeypatch.setattr(cr, "_OUTER_WAIT_FOR_PAD_SECONDS", 0.01)

    _job_obj, context = _context(
        tmp_path=tmp_path,
        config={cr.LIVEKIT_URL_CONFIG_KEY: "wss://x", cr.CALL_TIMEOUT_CONFIG_KEY: 0.01},
    )
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")

    async def place_call(spec):
        await asyncio.Event().wait()  # never completes on its own

    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call)
    exc = _run_expect_abort(
        runner,
        _FakeScenario("k1"),
        _runtime(metadata={"livekit_agent_name": "agent-w0"}),
    )
    assert "voice_call_runner_timeout" in str(exc)
    assert exc.partial is not None
    assert exc.partial.calls == ()


def test_silent_agent_real_engine_shape_returns_normal_outcome_with_empty_calls(
    tmp_path: Path,
) -> None:
    """Pins the shape the real engine actually produces for a silent agent-first call
    (engines/livekit.py::_conversation_outcome): status FAILED, code "no_conversation", zero
    messages -- never a COMPLETED case with zero turns (COMPLETED requires >= min_turn_messages
    and role alternation, a shape the engine cannot produce for a silent call). Must still surface
    as a NORMAL CallOutcome with the real (zero) turn count, never a WorldUnavailable/CallAborted,
    letting the scheduler's own coverage guarantee turn it into evidence_missing."""
    _job_obj, context = _context(tmp_path=tmp_path)
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")

    async def place_call(spec):
        failure = SimulationFailure(
            stage=FailureStage.RUNNING,
            code="no_conversation",
            message="No conversation turns were committed before the inactivity deadline",
            retryable=True,
        )
        return _report(
            case_status=CaseStatus.FAILED, failure=failure, transcript="", messages=[]
        )

    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call)
    outcome = _run(
        runner,
        _FakeScenario("k1"),
        _runtime(metadata={"livekit_agent_name": "agent-w0"}),
    )
    assert isinstance(outcome, CallOutcome)
    assert outcome.turns == 0
    assert outcome.calls == ()
    assert outcome.transcript_artifact is None  # empty transcript -- never uploaded


def test_silent_agent_conversation_silence_timeout_code_also_returns_normal_outcome(
    tmp_path: Path,
) -> None:
    """The engine's other zero-turn silent code (the agent-first silence watchdog firing before
    any turn ever lands) must map the same way as "no_conversation" above."""
    _job_obj, context = _context(tmp_path=tmp_path)
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")

    async def place_call(spec):
        failure = SimulationFailure(
            stage=FailureStage.RUNNING,
            code="conversation_silence_timeout",
            message="Agent-first conversation stalled after it began",
            retryable=True,
        )
        return _report(
            case_status=CaseStatus.FAILED, failure=failure, transcript="", messages=[]
        )

    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call)
    outcome = _run(
        runner,
        _FakeScenario("k1"),
        _runtime(metadata={"livekit_agent_name": "agent-w0"}),
    )
    assert isinstance(outcome, CallOutcome)
    assert outcome.turns == 0
    assert outcome.calls == ()


def test_silent_agent_mapping_is_scoped_to_zero_turns_only(tmp_path: Path) -> None:
    """A short-but-nonzero conversation carrying the same failure code must NOT be laundered into
    a normal outcome -- only a genuinely zero-turn silent call qualifies; this stays a CallAborted
    exactly like any other non-completed status."""
    _job_obj, context = _context(tmp_path=tmp_path)
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]

    async def place_call(spec):
        failure = SimulationFailure(
            stage=FailureStage.RUNNING,
            code="conversation_silence_timeout",
            message="Agent-first conversation stalled after it began",
            retryable=True,
        )
        return _report(
            case_status=CaseStatus.FAILED,
            failure=failure,
            transcript="hi\nhello",
            messages=messages,
        )

    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call)
    exc = _run_expect_abort(
        runner,
        _FakeScenario("k1"),
        _runtime(metadata={"livekit_agent_name": "agent-w0"}),
    )
    assert exc.partial is not None
    assert exc.partial.turns == 2
    assert exc.code == "target_agent_stalled"


def test_silence_after_caller_turn_is_attributed_to_target_agent(
    tmp_path: Path,
) -> None:
    _job_obj, context = _context(tmp_path=tmp_path)
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")

    async def place_call(spec):
        return _report(
            case_status=CaseStatus.FAILED,
            failure=SimulationFailure(
                stage=FailureStage.RUNNING,
                code="conversation_silence_timeout",
                message="Conversation produced no new speech for the stall deadline",
                retryable=True,
            ),
            transcript="assistant: Is that correct?\nuser: Yes, that is correct.",
            messages=[
                {"role": "assistant", "content": "Is that correct?"},
                {"role": "user", "content": "Yes, that is correct."},
            ],
        )

    exc = _run_expect_abort(
        cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call),
        _FakeScenario("k1"),
        _runtime(metadata={"livekit_agent_name": "agent-w0"}),
    )
    assert exc.code == "target_agent_stalled"
    assert "Target agent produced no response" in str(exc)


def test_provider_tool_failure_is_attributed_to_target_agent(tmp_path: Path) -> None:
    _job_obj, context = _context(tmp_path=tmp_path)
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")

    async def place_call(spec):
        return _report(
            case_status=CaseStatus.FAILED,
            failure=SimulationFailure(
                stage=FailureStage.RUNNING,
                code="target_agent_tool_failed",
                message="Target agent tool 'lookup_account' failed: ENOTFOUND api.example.com",
                retryable=False,
            ),
            result_metadata={
                "evidence": [
                    {
                        "source_id": "retell-call",
                        "adapter": "retell",
                        "evidence_class": "provider_reported",
                        "metadata": {
                            "tool_calls": [
                                {
                                    "name": "lookup_account",
                                    "ok": False,
                                    "error": "ENOTFOUND api.example.com",
                                }
                            ]
                        },
                    }
                ]
            },
        )

    exc = _run_expect_abort(
        cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call),
        _FakeScenario("k1"),
        _runtime(metadata={"livekit_agent_name": "agent-w0"}),
    )
    assert exc.code == "target_agent_tool_failed"
    assert "lookup_account" in str(exc)


def test_silence_after_complete_target_turn_is_attributed_to_simulator(
    tmp_path: Path,
) -> None:
    _job_obj, context = _context(tmp_path=tmp_path)
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")

    async def place_call(spec):
        return _report(
            case_status=CaseStatus.FAILED,
            failure=SimulationFailure(
                stage=FailureStage.RUNNING,
                code="conversation_stalled",
                message="Conversation produced no new speech for the stall deadline",
                retryable=True,
            ),
            transcript="user: Continue.\nassistant: Is that correct?",
            messages=[
                {"role": "user", "content": "Continue."},
                {"role": "assistant", "content": "Is that correct?"},
            ],
        )

    exc = _run_expect_abort(
        cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call),
        _FakeScenario("k1"),
        _runtime(metadata={"livekit_agent_name": "agent-w0"}),
    )
    assert exc.code == "simulator_stalled"
    assert "Simulated caller produced no response" in str(exc)


# =================================================================================================
# Evidence collection.
# =================================================================================================


def test_http_tool_seam_always_returns_no_calls() -> None:
    """CONTRACT NOTE 1: STOPPED, verified -- no invented capture proxy."""
    runtime = _runtime(endpoints={"database": _postgres_endpoint()})
    assert cr._collect_http_tool_calls(runtime) == ()


def test_find_postgres_endpoint_matches_by_protocol_not_a_fixed_slug_name() -> None:
    """Corrects the brief's literal `runtime.endpoints["database"]` wording: capability slugs are
    bundle-author-chosen (verified against `build_endpoints`/`_find_postgres_endpoint` in
    hosted_entrypoint.py) -- a bundle naming its slug anything else must still resolve."""
    endpoint = _postgres_endpoint()
    runtime = _runtime(endpoints={"orders_store": endpoint})
    found = cr._find_postgres_endpoint(runtime)
    assert found is endpoint


def test_find_postgres_endpoint_returns_none_when_absent() -> None:
    runtime = _runtime(
        endpoints={
            "queue": RuntimeEndpoint(
                capability="queue", protocol="amqp", address="amqp://x"
            )
        }
    )
    assert cr._find_postgres_endpoint(runtime) is None


class _FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]], columns: list[str]) -> None:
        self._rows = rows
        self.description = [(name,) for name in columns]

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class _FakeConnection:
    def __init__(self, rows: list[tuple[Any, ...]], columns: list[str]) -> None:
        self._rows = rows
        self._columns = columns
        self.executed: list[str] = []

    def execute(self, statement: str, params: Any = None) -> _FakeCursor:
        self.executed.append(statement)
        return _FakeCursor(self._rows, self._columns)

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *exc_info: Any) -> bool:
        return False


class _FakePsycopg:
    def __init__(self, rows: list[tuple[Any, ...]], columns: list[str]) -> None:
        self._rows = rows
        self._columns = columns
        self.connections: list[_FakeConnection] = []

    def connect(self, dsn: str, **kwargs: Any) -> _FakeConnection:
        connection = _FakeConnection(self._rows, self._columns)
        self.connections.append(connection)
        return connection


class _RaisingPsycopg:
    class Error(Exception):
        pass

    def connect(self, dsn: str, **kwargs: Any) -> Any:
        raise self.Error("connection refused")


def test_tool_trace_keeps_the_door_s_outcome_apart_from_a_failure(monkeypatch) -> None:
    """A row the door wrote says whether the world refused the call or it broke before running."""
    columns = ["name", "arguments", "result", "ok", "error", "at", "outcome", "refusal_code"]
    rows = [
        ("cancel_order", {}, None, False, "already shipped", 1.0, "refused", "world_rule"),
        ("refund", {}, None, False, "refund is not permitted", 2.0, "refused", "not_permitted"),
        ("lookup", {}, None, False, "TypeError: unavailable", 3.0, "faulted", None),
        ("legacy", {}, None, False, "not found", 4.0, None, None),
    ]
    monkeypatch.setitem(sys.modules, "psycopg", _FakePsycopg(rows, columns))
    calls = cr._collect_tool_trace_calls(_runtime(endpoints={"database": _postgres_endpoint()}))
    assert [(c.name, c.refused, c.refusal_code) for c in calls] == [
        ("cancel_order", True, "world_rule"),
        ("refund", True, "not_permitted"),
        ("lookup", False, ""),
        ("legacy", True, ""),
    ]


def test_tool_trace_translates_rows_and_applies_v1_refused_rule(monkeypatch) -> None:
    """world-handle-interface.md V1 rule: `refused = not ok` (a trace cannot distinguish refusal
    from crash)."""
    columns = ["name", "arguments", "result", "ok", "error", "at"]
    rows = [
        ("lookup_order", {"id": "1"}, {"status": "shipped"}, True, "", 100.5),
        ("cancel_order", {"id": "2"}, None, False, "not found", 101.0),
    ]
    fake = _FakePsycopg(rows, columns)
    monkeypatch.setitem(sys.modules, "psycopg", fake)

    runtime = _runtime(endpoints={"database": _postgres_endpoint()})
    calls = cr._collect_tool_trace_calls(runtime)

    assert calls == (
        Call(
            name="lookup_order",
            arguments={"id": "1"},
            result={"status": "shipped"},
            ok=True,
            error="",
            refused=False,
            at=100.5,
        ),
        Call(
            name="cancel_order",
            arguments={"id": "2"},
            result=None,
            ok=False,
            error="not found",
            refused=True,
            at=101.0,
        ),
    )


def test_tool_trace_reads_only_the_agent_s_own_actions_as_its_calls(
    monkeypatch,
) -> None:
    old = ["name", "arguments", "result", "ok", "error", "at"]
    agent_rows = [
        ("lookup_order", {"id": "1"}, {"status": "shipped"}, True, "", 100.5),
        ("cancel_order", {"id": "2"}, None, False, "not found", 101.0),
    ]
    runtime = _runtime(endpoints={"database": _postgres_endpoint()})
    monkeypatch.setitem(sys.modules, "psycopg", _FakePsycopg(agent_rows, old))
    before = cr._collect_tool_trace_calls(runtime)

    columns = [*old, "action_id", "actor_id", "worker_id", "outcome"]
    rows = [
        (*agent_rows[0], "a1", "agent", None, "committed"),
        (
            "approve",
            {"item": "1"},
            {"ok": True},
            True,
            "",
            100.7,
            "a2",
            "reviewer",
            None,
            "committed",
        ),
        (*agent_rows[1], "a3", "agent", None, "refused"),
    ]
    monkeypatch.setitem(sys.modules, "psycopg", _FakePsycopg(rows, columns))
    assert cr._collect_tool_trace_calls(runtime) == before
    assert [one.name for one in before] == ["lookup_order", "cancel_order"]


def test_tool_trace_read_failure_degrades_to_no_calls_never_crashes(
    monkeypatch,
) -> None:
    """No producer exists yet (CONTRACT NOTE 2) -- a missing table / connection failure must
    degrade to `()`, never raise past this function."""
    monkeypatch.setitem(sys.modules, "psycopg", _RaisingPsycopg())
    runtime = _runtime(endpoints={"database": _postgres_endpoint()})
    assert cr._collect_tool_trace_calls(runtime) == ()


def test_tool_trace_missing_endpoint_degrades_to_no_calls() -> None:
    runtime = _runtime(endpoints={})
    assert cr._collect_tool_trace_calls(runtime) == ()


def test_a_strict_tool_trace_read_says_the_capture_failed(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "psycopg", _RaisingPsycopg())
    with pytest.raises(cr.ToolCaptureFailed, match="tool_trace read failed"):
        cr._collect_tool_trace_calls(
            _runtime(endpoints={"database": _postgres_endpoint()}), strict=True
        )
    with pytest.raises(cr.ToolCaptureFailed, match="no world database"):
        cr._collect_tool_trace_calls(_runtime(endpoints={}), strict=True)


def test_a_strict_trace_file_read_fails_only_on_a_file_it_cannot_read(
    tmp_path: Path, monkeypatch
) -> None:
    trace = tmp_path / "agent-tool-calls.jsonl"
    runtime = _runtime(metadata={"tool_trace_path": str(trace)})
    # No file: the agent called no tool, or nothing wrote one. Neither is a failed read.
    assert cr._collect_file_tool_calls(runtime, strict=True) == ()
    _trace_lines(trace, {"name": "lookup", "arguments": {}})

    def refuse(*_args: Any, **_kwargs: Any) -> str:
        raise PermissionError("denied")

    monkeypatch.setattr(type(trace), "read_text", refuse)
    assert cr._collect_file_tool_calls(runtime) == ()
    with pytest.raises(cr.ToolCaptureFailed, match="tool_trace file unreadable"):
        cr._collect_file_tool_calls(runtime, strict=True)


@pytest.mark.parametrize(
    "lines,failed,names",
    [
        pytest.param(['{"name": "lookup", "argu'], True, [], id="cut-json-line"),
        pytest.param(['["lookup"]'], True, [], id="not-an-object"),
        pytest.param(['{"arguments": {}}'], True, [], id="no-name"),
        pytest.param(
            ['{"name": "lookup", "arguments": {}}', '{"name": "book", "argu'],
            False,
            ["lookup"],
            id="one-bad-line-among-good",
        ),
        pytest.param(["", "   "], False, [], id="blank-lines-only"),
    ],
)
def test_a_trace_file_with_no_readable_record_is_a_failed_capture(
    tmp_path: Path, lines: list[str], failed: bool, names: list[str]
) -> None:
    trace = tmp_path / "agent-tool-calls.jsonl"
    trace.write_text("\n".join(lines) + "\n", encoding="utf-8")
    runtime = _runtime(metadata={"tool_trace_path": str(trace)})

    # Lenient reads drop what they cannot parse, as before.
    assert [call.name for call in cr._collect_file_tool_calls(runtime)] == names
    if failed:
        with pytest.raises(cr.ToolCaptureFailed, match="no readable record"):
            cr._collect_file_tool_calls(runtime, strict=True)
    else:
        calls = cr._collect_file_tool_calls(runtime, strict=True)
        assert [call.name for call in calls] == names


class _NoTablePsycopg:
    """Connects, then finds no `_alk_tool_trace` table, as a world whose seed never ran."""

    class UndefinedTable(Exception):
        sqlstate = "42P01"

    def connect(self, dsn: str, **kwargs: Any) -> Any:
        missing = self.UndefinedTable('relation "_alk_tool_trace" does not exist')

        class Connection:
            def execute(self, *_args: Any, **_kwargs: Any) -> Any:
                raise missing

            def __enter__(self) -> Any:
                return self

            def __exit__(self, *exc_info: Any) -> bool:
                return False

        return Connection()


@pytest.mark.parametrize(
    "seam,trace,database,failed,captured",
    [
        pytest.param(
            EvidenceSeam.HTTP_TOOL, "none", None, False, [], id="http-tool-empty"
        ),
        pytest.param(
            EvidenceSeam.HTTP_TOOL,
            "unreadable",
            None,
            True,
            [],
            id="trace-file-unreadable",
        ),
        pytest.param(
            EvidenceSeam.TOOL_TRACE, "none", "empty", False, [], id="tool-trace-empty"
        ),
        pytest.param(
            EvidenceSeam.TOOL_TRACE,
            "none",
            "down",
            True,
            [],
            id="tool-trace-connection-refused",
        ),
        pytest.param(
            EvidenceSeam.TOOL_TRACE,
            "none",
            "no-table",
            True,
            [],
            id="tool-trace-table-missing",
        ),
        pytest.param(
            EvidenceSeam.TOOL_TRACE,
            "unreadable",
            "rows",
            False,
            ["lookup"],
            id="trace-file-unreadable-but-the-seam-captured",
        ),
        pytest.param(
            EvidenceSeam.TOOL_TRACE,
            "spans",
            "down",
            False,
            ["ask_writer", "draft_note"],
            id="span-lines-with-the-database-down",
        ),
        # A hand-off is kept for the record but is not a call checks grade, so it does not
        # stand in for the database the seam could not read.
        pytest.param(
            EvidenceSeam.TOOL_TRACE,
            "handoff",
            "down",
            True,
            ["Draft checker"],
            id="handoff-only-with-the-database-down",
        ),
        pytest.param(
            EvidenceSeam.TOOL_TRACE,
            "handoff",
            "empty",
            False,
            ["Draft checker"],
            id="handoff-only-with-the-database-read",
        ),
        pytest.param(
            EvidenceSeam.HTTP_TOOL, "cut", None, True, [], id="trace-file-of-cut-lines"
        ),
    ],
)
def test_a_completed_call_says_whether_its_tool_capture_failed(
    tmp_path: Path,
    monkeypatch,
    seam: EvidenceSeam,
    trace: str,
    database: str | None,
    failed: bool,
    captured: list[str],
) -> None:
    _job_obj, context = _context(tmp_path=tmp_path, evidence_seam=seam)
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")
    trace_path = tmp_path / "agent-tool-calls.jsonl"
    columns = ["name", "arguments", "result", "ok", "error", "at"]
    if database == "down":
        monkeypatch.setitem(sys.modules, "psycopg", _RaisingPsycopg())
    elif database == "no-table":
        monkeypatch.setitem(sys.modules, "psycopg", _NoTablePsycopg())
    elif database is not None:
        rows = [("lookup", {}, "ok", True, "", 1.0)] if database == "rows" else []
        monkeypatch.setitem(sys.modules, "psycopg", _FakePsycopg(rows, columns))
    read_text = Path.read_text

    def read_unless_refused(self: Path, *args: Any, **kwargs: Any) -> str:
        if self == trace_path:
            raise PermissionError("denied")
        return read_text(self, *args, **kwargs)

    async def place_call(spec):
        # Written during the call, after the runner cleared the previous attempt's file.
        if trace == "unreadable":
            _trace_lines(trace_path, {"name": "lookup", "arguments": {}})
            monkeypatch.setattr(type(trace_path), "read_text", read_unless_refused)
        elif trace == "spans":
            _trace_lines(trace_path, *_SPAN_LINES)
        elif trace == "handoff":
            _trace_lines(trace_path, _HANDOFF_LINE)
        elif trace == "cut":
            trace_path.write_text('{"name": "lookup", "argu\n', encoding="utf-8")
        return _report()

    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call)
    outcome = _run(
        runner,
        _FakeScenario("k1"),
        _runtime(
            metadata={
                "livekit_agent_name": "agent-w0",
                "tool_trace_path": str(trace_path),
            },
            endpoints={"database": _postgres_endpoint()} if database else None,
        ),
    )

    assert outcome.tool_capture_failed is failed
    assert [call.name for call in outcome.calls] == captured


def test_tool_trace_result_string_form_is_truncated_at_2000_chars(monkeypatch) -> None:
    long_string = "x" * 3000
    columns = ["name", "arguments", "result", "ok", "error", "at"]
    rows = [("t", {}, long_string, True, "", 1.0)]
    monkeypatch.setitem(sys.modules, "psycopg", _FakePsycopg(rows, columns))
    runtime = _runtime(endpoints={"database": _postgres_endpoint()})
    calls = cr._collect_tool_trace_calls(runtime)
    assert len(calls) == 1
    assert len(calls[0].result) == 2000


def test_tool_trace_parsed_json_result_is_not_truncated(monkeypatch) -> None:
    """Per world-handle-interface.md: "result — parsed JSON where the source captured JSON, else
    a string; both result (string form) and error truncated at 2,000 chars" -- truncation applies
    to the STRING form only."""
    big_list = list(range(3000))
    columns = ["name", "arguments", "result", "ok", "error", "at"]
    rows = [("t", {}, big_list, True, "", 1.0)]
    monkeypatch.setitem(sys.modules, "psycopg", _FakePsycopg(rows, columns))
    runtime = _runtime(endpoints={"database": _postgres_endpoint()})
    calls = cr._collect_tool_trace_calls(runtime)
    assert calls[0].result == big_list


def test_tool_trace_holds_only_the_agents_calls(monkeypatch) -> None:
    """A world actor's action shares the trace table with the agent's calls; it is never graded as the agent's,
    and never fires an actor waiting on one of the agent's tools."""
    # The voice call actor imports LiveKit, which the newest-dependencies CI row doesn't install.
    pytest.importorskip("livekit")
    from fi.simulate.simulation.engines.call_actor_voice import world_access

    columns = [
        "name",
        "arguments",
        "result",
        "ok",
        "error",
        "at",
        "actor_id",
        "outcome",
    ]
    rows = [
        ("read_record", {"id": "d1"}, {"stage": "open"}, True, "", 1.0, "agent", "ok"),
        ("approve", {"id": "d1"}, None, False, "refused", 2.0, "reviewer", "refused"),
        ("update", {"id": "d1"}, {"ok": True}, True, "", 3.0, None, None),
    ]
    monkeypatch.setitem(sys.modules, "psycopg", _FakePsycopg(rows, columns))
    runtime = _runtime(endpoints={"database": _postgres_endpoint()})
    assert [one.name for one in cr._collect_tool_trace_calls(runtime)] == [
        "read_record",
        "update",
    ]
    assert world_access("postgresql://x/y")() == ["read_record", "update"]


def test_tool_trace_clear_is_best_effort_and_never_raises(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "psycopg", _RaisingPsycopg())
    cr._clear_tool_trace_calls("postgresql://x/y")  # must not raise


def test_completed_call_with_tool_trace_seam_collects_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    columns = ["name", "arguments", "result", "ok", "error", "at"]
    rows = [("do_thing", {}, "done", True, "", 5.0)]
    monkeypatch.setitem(sys.modules, "psycopg", _FakePsycopg(rows, columns))

    _job_obj, context = _context(
        tmp_path=tmp_path, evidence_seam=EvidenceSeam.TOOL_TRACE
    )
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")

    async def place_call(spec):
        return _report()

    runtime = _runtime(
        metadata={"livekit_agent_name": "agent-w0"},
        endpoints={"database": _postgres_endpoint()},
    )
    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call)
    outcome = _run(runner, _FakeScenario("k1"), runtime)
    assert len(outcome.calls) == 1
    assert outcome.calls[0].name == "do_thing"


# =================================================================================================
# Credential export (WHY: the LiveKit engine reads these via ambient os.environ, not spec fields).
# =================================================================================================


def test_construction_exports_target_provider_secrets_to_environ_once(
    tmp_path: Path,
) -> None:
    fake_environ: dict[str, str] = {}
    _job_obj, context = _context(tmp_path=tmp_path)
    runner = cr.CallRunnerImpl(FakeAdapter(), context, environ=fake_environ)
    assert runner._environ[LIVEKIT_API_KEY] == "lk-key"
    assert runner._environ[LIVEKIT_API_SECRET] == "lk-secret"
    assert runner._environ[DEEPGRAM_API_KEY] == "dg-key"
    assert runner._environ[GEMINI_API_KEY] == "gm-key"


def test_vapi_control_key_is_only_exported_to_private_call_environment(tmp_path):
    _job_obj, context = _context(
        tmp_path=tmp_path,
        secrets={**_ALL_SECRETS, "VAPI_PUBLIC_API_KEY": "public-control-key"},
    )
    parent_environment = {}
    runner = cr.CallRunnerImpl(FakeAdapter(), context, environ=parent_environment)
    assert runner._environ["VAPI_PUBLIC_API_KEY"] == "public-control-key"
    assert "VAPI_PUBLIC_API_KEY" not in parent_environment


def test_construction_never_exports_secrets_outside_the_target_provider_map(
    tmp_path: Path,
) -> None:
    fake_environ: dict[str, str] = {}
    secrets = dict(_ALL_SECRETS)
    secrets["UNRELATED_ALIAS"] = "should-not-export"
    _job_obj, context = _context(tmp_path=tmp_path, secrets=secrets)
    runner = cr.CallRunnerImpl(FakeAdapter(), context, environ=fake_environ)
    assert "UNRELATED_ALIAS" not in runner._environ


def test_construction_uses_platform_simulator_key_without_exposing_agent_model_key(
    tmp_path: Path,
) -> None:
    fake_environ: dict[str, str] = {}
    target = {
        LIVEKIT_API_KEY: "lk-key",
        LIVEKIT_API_SECRET: "lk-secret",
        "ANTHROPIC_API_KEY": "customer-agent-key",
    }
    simulator = {
        "SIMULATOR_DEEPGRAM_API_KEY": "platform-deepgram-key",
        "SIMULATOR_GEMINI_API_KEY": "platform-gemini-key",
    }
    _job_obj, context = _context(
        tmp_path=tmp_path,
        secrets=target,
        simulator_secrets=simulator,
    )
    runner = cr.CallRunnerImpl(FakeAdapter(), context, environ=fake_environ)
    assert runner._environ[DEEPGRAM_API_KEY] == "platform-deepgram-key"
    assert runner._environ[GEMINI_API_KEY] == "platform-gemini-key"
    assert "ANTHROPIC_API_KEY" not in runner._environ


def test_hosted_run_never_falls_back_to_customer_simulator_keys(tmp_path: Path) -> None:
    fake_environ: dict[str, str] = {}
    target = dict(_ALL_SECRETS)
    _job_obj, context = _context(
        tmp_path=tmp_path,
        secrets=target,
        simulator_secrets={},
    )
    runner = cr.CallRunnerImpl(FakeAdapter(), context, environ=fake_environ)
    assert DEEPGRAM_API_KEY not in runner._environ
    assert GEMINI_API_KEY not in runner._environ
    assert runner._missing_config is not None


def test_local_sdk_remains_byok_for_simulator_credentials(tmp_path: Path) -> None:
    fake_environ: dict[str, str] = {}
    _job_obj, context = _context(
        tmp_path=tmp_path,
        execution=ExecutionMode.LOCAL,
        simulator_secrets={},
    )
    runner = cr.CallRunnerImpl(FakeAdapter(), context, environ=fake_environ)
    assert runner._environ[DEEPGRAM_API_KEY] == "dg-key"
    assert runner._environ[GEMINI_API_KEY] == "gm-key"


def test_platform_simulator_credentials_win_without_replacing_target_livekit(
    tmp_path: Path,
) -> None:
    fake_environ = {
        DEEPGRAM_API_KEY: "platform-deepgram",
        GEMINI_API_KEY: "platform-gemini",
        "GOOGLE_APPLICATION_CREDENTIALS": "/run/futureagi/platform-vertex.json",
        "GOOGLE_CLOUD_PROJECT": "platform-project",
    }
    _job_obj, context = _context(tmp_path=tmp_path)

    runner = cr.CallRunnerImpl(FakeAdapter(), context, environ=fake_environ)

    assert runner._environ[LIVEKIT_API_KEY] == "lk-key"
    assert runner._environ[LIVEKIT_API_SECRET] == "lk-secret"
    assert runner._environ[DEEPGRAM_API_KEY] == "platform-deepgram"
    assert runner._environ[GEMINI_API_KEY] == "platform-gemini"
    assert (
        runner._environ["GOOGLE_APPLICATION_CREDENTIALS"]
        == "/run/futureagi/platform-vertex.json"
    )
    assert runner._missing_config is None


def test_close_quiesces_livekit_objects_before_event_loop_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_environ: dict[str, str] = {}
    _job_obj, context = _context(tmp_path=tmp_path)
    runner = cr.CallRunnerImpl(FakeAdapter(), context, environ=fake_environ)
    collections: list[None] = []
    monkeypatch.setattr(cr.gc, "collect", lambda: collections.append(None))

    async def close_twice() -> None:
        await runner.close()
        await runner.close()

    asyncio.run(close_twice())

    assert collections == [None, None]
    assert runner._closed is True


def test_provider_voice_uses_platform_livekit_without_exposing_customer_livekit(
    tmp_path: Path,
) -> None:
    fake_environ: dict[str, str] = {}
    target = {
        RETELL_API_KEY: "customer-retell-key",
        LIVEKIT_API_KEY: "customer-livekit-must-not-export",
        LIVEKIT_API_SECRET: "customer-livekit-secret-must-not-export",
    }
    simulator = {
        "SIMULATOR_LIVEKIT_URL": "wss://platform-livekit.example",
        "SIMULATOR_LIVEKIT_API_KEY": "platform-livekit-key",
        "SIMULATOR_LIVEKIT_API_SECRET": "platform-livekit-secret",
        "SIMULATOR_DEEPGRAM_API_KEY": "platform-deepgram",
        "SIMULATOR_GEMINI_API_KEY": "platform-gemini",
    }
    _job_obj, context = _context(
        tmp_path=tmp_path,
        connector="retell",
        mode=ProviderExecutionMode.PROVIDER_IMPORT,
        config={"agent_id": "source-agent"},
        secrets=target,
        simulator_secrets=simulator,
    )

    runner = cr.CallRunnerImpl(FakeAdapter(), context, environ=fake_environ)

    assert runner._environ[LIVEKIT_API_KEY] == "platform-livekit-key"
    assert runner._environ[LIVEKIT_API_SECRET] == "platform-livekit-secret"
    assert runner._environ[RETELL_API_KEY] == "customer-retell-key"
    assert runner._livekit_url == "wss://platform-livekit.example"
    assert runner._missing_config is None


def test_auto_connector_resolves_to_livekit_from_target_secrets() -> None:
    job = _job(connector="auto")
    assert (
        cr._resolve_connector(job, {cr.LIVEKIT_URL_ALIAS: "wss://x.livekit.cloud"})
        == "livekit"
    )


def test_auto_connector_resolves_to_livekit_from_config() -> None:
    job = _job(connector="auto", config=dict(_ALL_CONFIG))
    assert cr._resolve_connector(job, {}) == "livekit"


def test_auto_connector_resolves_to_vapi_and_retell_from_target_secrets() -> None:
    job = _job(connector="auto")
    assert cr._resolve_connector(job, {cr.VAPI_API_KEY_ALIAS: "k"}) == "vapi"
    assert cr._resolve_connector(job, {cr.RETELL_API_KEY_ALIAS: "k"}) == "retell"


def test_explicit_connector_is_never_overridden() -> None:
    job = _job(connector="retell")
    assert cr._resolve_connector(job, {cr.LIVEKIT_URL_ALIAS: "wss://x"}) == "retell"


def _timed_out_runner(tmp_path: Path, turns: int):
    """A runner whose call comes back TIMED_OUT after `turns` messages."""
    _job_obj, context = _context(tmp_path=tmp_path)
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")
    messages = [{"role": "user", "content": f"turn {i}"} for i in range(turns)]

    async def place_call(spec):
        return _report(
            status=RunStatus.TIMED_OUT,
            case_status=CaseStatus.TIMED_OUT,
            messages=messages,
            failure=SimulationFailure(
                stage=FailureStage.RUNNING,
                code="conversation_deadline",
                message="Conversation exceeded its deadline",
            ),
        )

    return cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call)


def test_completed_voice_call_reports_measured_duration(tmp_path: Path) -> None:
    class Reporter:
        def __init__(self) -> None:
            self.checked: list[str] = []
            self.recorded: list[dict[str, Any]] = []

        def check(self, action: str) -> None:
            self.checked.append(action)

        def record(self, **facts: Any) -> None:
            self.recorded.append(facts)

    _job_obj, context = _context(tmp_path=tmp_path)
    reporter = Reporter()
    context = replace(context, usage_reporter=reporter)
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")

    async def place_call(spec):
        return _report(run_id="measured-run")

    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call)
    _run(
        runner,
        _FakeScenario("k1"),
        _runtime(metadata={"livekit_agent_name": "a-w0"}),
    )

    assert reporter.checked == ["voice_call"]
    assert len(reporter.recorded) == 1
    recorded = reporter.recorded[0]
    assert isinstance(recorded["occurred_at"], datetime)
    assert {key: value for key, value in recorded.items() if key != "occurred_at"} == {
        "action": "voice_call",
        "scenario_key": "k1",
        "amount": 0.5,
        "funding": "platform",
        "outcome": "completed",
        "failure_domain": None,
    }


def test_failed_text_call_reports_partial_tokens_and_failure_domain() -> None:
    class Reporter:
        def __init__(self) -> None:
            self.recorded: list[dict[str, Any]] = []

        def record(self, **facts: Any) -> None:
            self.recorded.append(facts)

    reporter = Reporter()
    partial = chat.Transcript(simulator_input_tokens=11, simulator_output_tokens=7)
    failure = CallAborted("simulator stalled", code="simulator_stalled")

    async def record() -> None:
        setattr(failure, "partial_transcript", partial)
        await chat._record_text_usage(
            reporter,
            scenario_key="k1",
            started=datetime.now(timezone.utc),
            funding="platform",
            transcript=None,
            failure=failure,
        )

    asyncio.run(record())

    assert reporter.recorded[0]["amount"] == 18
    assert reporter.recorded[0]["outcome"] == "failed"
    assert reporter.recorded[0]["failure_domain"] == "simulator"


def test_a_timed_out_call_with_a_real_conversation_is_graded_not_aborted(
    tmp_path: Path,
) -> None:
    """An intake agent may ask thirty to fifty questions, so reaching the deadline is an ordinary
    outcome. A measured 51-turn call lost all three sub-goals to `held: null` because the timeout was
    treated as infrastructure."""
    outcome = _run(
        _timed_out_runner(tmp_path, 8),
        _FakeScenario("k1"),
        _runtime(metadata={"livekit_agent_name": "a-w0"}),
    )
    assert outcome is not None, (
        "a timed-out call that held a conversation must still be graded"
    )


def test_a_timed_out_call_that_never_got_going_still_aborts(tmp_path: Path) -> None:
    """The carve-out salvages evidence; it does not excuse a call that produced none."""
    aborted = _run_expect_abort(
        _timed_out_runner(tmp_path, 1),
        _FakeScenario("k1"),
        _runtime(metadata={"livekit_agent_name": "a-w0"}),
    )
    assert "voice_call_not_completed" in str(aborted)


def test_the_returned_outcome_keeps_the_transcript_the_judge_needs():
    """The final outcome used to be rebuilt field by field, which dropped `messages` in silence.

    A judged sub-goal about what was said then had no transcript, and the judge could only answer
    that it could not tell -- observed on a real localhost run before this was fixed.
    """
    from dataclasses import replace

    from fi.alk.harness.hosted_scheduler import CallOutcome

    said = ({"role": "user", "content": "call me back tomorrow"},)
    base = CallOutcome(
        calls=(),
        turns=12,
        started_at="2026-09-10T00:00:00.000Z",
        ended_at="2026-09-10T00:01:24.000Z",
        duration_ms=84000,
        transcript_artifact="sha256:" + "0" * 64,
        messages=said,
    )
    assert replace(base, calls=()).messages == said


def test_a_tool_result_sent_as_text_is_read_back_into_its_shape():
    from fi.alk.harness.call_runner import _structured

    assert _structured('{"id": 7}') == {"id": 7}
    assert _structured("{'id': 7}") == {"id": 7}
    assert _structured("[1, 2]") == [1, 2]
    assert _structured("[INFO] booked") == "[INFO] booked"
    assert _structured({"id": 7}) == {"id": 7}
    assert len(_structured("x" * 5000)) == 2000


# --- TH-9550, amendment 4.1 (1b): a local job's Google settings go with the job's own file ---

_JOB_ADC = json.dumps({"type": "service_account", "project_id": "job-canary"})
_JOB_GOOGLE = {
    "GOOGLE_APPLICATION_CREDENTIALS_JSON": _JOB_ADC,
    "GOOGLE_CLOUD_PROJECT": "job-project",
    "GOOGLE_CLOUD_LOCATION": "job-location",
    "GOOGLE_GENAI_USE_VERTEXAI": "true",
    "GOOGLE_API_KEY": "job-google-key-canary",
    "GEMINI_API_KEY": "job-gemini-key-canary",
    "OPENAI_API_KEY": "job-openai-key-canary",
}
_GOOGLE_SETTINGS = ("GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION", "GOOGLE_GENAI_USE_VERTEXAI")


def _local_call_environment(tmp_path, *, secrets, environ, simulator_secrets=None):
    _job_obj, context = _context(
        tmp_path=tmp_path,
        execution=ExecutionMode.LOCAL,
        secrets=secrets,
        simulator_secrets=simulator_secrets if simulator_secrets is not None else {},
    )
    return cr.CallRunnerImpl(FakeAdapter(), context, environ=environ)


def _assert_customer_keys_kept(call_environment):
    # The gate is about the Google settings only: the customer's keys stay (1b).
    assert call_environment["GOOGLE_API_KEY"] == "job-google-key-canary"
    assert call_environment["GEMINI_API_KEY"] == "job-gemini-key-canary"
    assert call_environment["OPENAI_API_KEY"] == "job-openai-key-canary"


def test_the_jobs_google_settings_replace_the_hosts_when_the_jobs_own_file_is_in_effect(tmp_path):
    runner = _local_call_environment(
        tmp_path,
        secrets=dict(_JOB_GOOGLE),
        environ={"GOOGLE_CLOUD_PROJECT": "host-project"},
    )

    call_environment = runner._environ

    assert runner._adc_path is not None
    assert call_environment["GOOGLE_APPLICATION_CREDENTIALS"] == str(runner._adc_path)
    assert Path(call_environment["GOOGLE_APPLICATION_CREDENTIALS"]).read_text() == _JOB_ADC
    assert call_environment["GOOGLE_CLOUD_PROJECT"] == "job-project"
    assert call_environment["GOOGLE_CLOUD_LOCATION"] == "job-location"
    assert call_environment["GOOGLE_GENAI_USE_VERTEXAI"] == "true"
    assert "host-project" not in call_environment.values()
    _assert_customer_keys_kept(call_environment)


def test_the_jobs_google_settings_are_dropped_when_the_host_has_a_credentials_file(tmp_path):
    runner = _local_call_environment(
        tmp_path,
        secrets=dict(_JOB_GOOGLE),
        environ={
            "GOOGLE_APPLICATION_CREDENTIALS": "/host/google.json",
            "GOOGLE_CLOUD_PROJECT": "host-project",
        },
    )

    call_environment = runner._environ

    assert runner._adc_path is None
    assert call_environment["GOOGLE_APPLICATION_CREDENTIALS"] == "/host/google.json"
    assert call_environment["GOOGLE_CLOUD_PROJECT"] == "host-project"
    assert "GOOGLE_CLOUD_LOCATION" not in call_environment
    assert "GOOGLE_GENAI_USE_VERTEXAI" not in call_environment
    assert "job-project" not in call_environment.values()
    _assert_customer_keys_kept(call_environment)


def test_the_jobs_google_settings_are_dropped_without_the_jobs_own_file(tmp_path):
    secrets = {name: value for name, value in _JOB_GOOGLE.items() if "JSON" not in name}

    runner = _local_call_environment(
        tmp_path, secrets=secrets, environ={"GOOGLE_CLOUD_PROJECT": "host-project"}
    )

    call_environment = runner._environ
    assert runner._adc_path is None
    assert call_environment["GOOGLE_CLOUD_PROJECT"] == "host-project"
    assert "GOOGLE_CLOUD_LOCATION" not in call_environment
    assert "GOOGLE_GENAI_USE_VERTEXAI" not in call_environment
    _assert_customer_keys_kept(call_environment)


def test_the_jobs_google_settings_are_dropped_when_the_file_in_effect_is_the_platforms(tmp_path):
    platform_adc = json.dumps({"type": "service_account", "project_id": "platform-canary"})
    runner = _local_call_environment(
        tmp_path,
        secrets=dict(_JOB_GOOGLE),
        environ={},
        simulator_secrets={"SIMULATOR_GOOGLE_APPLICATION_CREDENTIALS_JSON": platform_adc},
    )

    call_environment = runner._environ

    assert Path(call_environment["GOOGLE_APPLICATION_CREDENTIALS"]).read_text() == platform_adc
    for name in _GOOGLE_SETTINGS:
        assert name not in call_environment


def test_the_hosts_google_settings_are_removed_when_the_jobs_own_file_is_in_effect_and_it_sends_none(
    tmp_path,
):
    # The job sends its credentials JSON and no project, location or switch; the host has all
    # three. The host's must not join the job's file.
    secrets = {"GOOGLE_APPLICATION_CREDENTIALS_JSON": _JOB_ADC}

    runner = _local_call_environment(
        tmp_path,
        secrets=secrets,
        environ={
            "GOOGLE_CLOUD_PROJECT": "host-project",
            "GOOGLE_CLOUD_LOCATION": "host-location",
            "GOOGLE_GENAI_USE_VERTEXAI": "host-switch",
        },
    )

    call_environment = runner._environ
    assert runner._adc_path is not None
    assert Path(call_environment["GOOGLE_APPLICATION_CREDENTIALS"]).read_text() == _JOB_ADC
    for name in _GOOGLE_SETTINGS:
        assert name not in call_environment
    assert not {"host-project", "host-location", "host-switch"} & set(call_environment.values())


_VERTEX_ONLY_JOB = {
    "LIVEKIT_API_KEY": "lk-key",
    "LIVEKIT_API_SECRET": "lk-secret",
    "DEEPGRAM_API_KEY": "dg-key",
    "GOOGLE_APPLICATION_CREDENTIALS_JSON": _JOB_ADC,
    "GOOGLE_CLOUD_PROJECT": "job-project",
}


def test_the_config_check_does_not_count_the_jobs_project_the_gate_drops(tmp_path):
    # The host has a credentials file and no project, so the gate drops the job's project and
    # the call runs with none. The check must say Vertex is not ready, not pass.
    runner = _local_call_environment(
        tmp_path,
        secrets=dict(_VERTEX_ONLY_JOB),
        environ={"GOOGLE_APPLICATION_CREDENTIALS": "/host/google.json"},
    )

    assert "GOOGLE_CLOUD_PROJECT" not in runner._environ
    assert runner._missing_config is not None
    assert any("VERTEX_ADC" in alias for alias in runner._missing_config.aliases)


def test_the_config_check_counts_the_jobs_project_when_the_gate_keeps_it(tmp_path):
    runner = _local_call_environment(
        tmp_path, secrets=dict(_VERTEX_ONLY_JOB), environ={}
    )

    assert runner._environ["GOOGLE_CLOUD_PROJECT"] == "job-project"
    assert runner._missing_config is None


def test_the_platforms_google_settings_do_not_pair_with_the_jobs_own_file(tmp_path):
    # The platform holds a project but no credentials file, so the job's file is the one in
    # effect: its project goes with it, and the platform's never sits beside it.
    runner = _local_call_environment(
        tmp_path,
        secrets=dict(_VERTEX_ONLY_JOB),
        environ={},
        simulator_secrets={"GOOGLE_CLOUD_PROJECT": "platform-project"},
    )

    assert runner._environ["GOOGLE_CLOUD_PROJECT"] == "job-project"
    assert "platform-project" not in runner._environ.values()


def test_the_platforms_google_settings_are_dropped_when_the_jobs_own_file_sends_none(tmp_path):
    secrets = dict(_VERTEX_ONLY_JOB)
    del secrets["GOOGLE_CLOUD_PROJECT"]
    runner = _local_call_environment(
        tmp_path,
        secrets=secrets,
        environ={},
        simulator_secrets={
            "GOOGLE_CLOUD_PROJECT": "platform-project",
            "GOOGLE_CLOUD_LOCATION": "platform-location",
        },
    )

    assert not {"platform-project", "platform-location"} & set(runner._environ.values())




def test_a_call_keeps_who_its_call_actors_were_and_what_happened_to_each(
    tmp_path: Path, monkeypatch
) -> None:
    from fi.alk.harness.call_actor import CallActor, CallActorList, save_call_actors
    from fi.simulate.simulation.call_actors import LOUDNESS_RANGE

    _job_obj, context = _context(tmp_path=tmp_path)
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")
    document = context.bundle_dir / "scenarios" / "k1" / "scenario.json"
    body = json.loads(document.read_text())
    body["actors"] = [
        {
            "id": "near",
            "instruction": "You want them to hurry.",
            "trigger": {"kind": "always"},
        },
        {"id": "other", "instruction": "x", "trigger": {"kind": "always"}},
    ]
    document.write_text(json.dumps(body))
    save_call_actors(
        context.bundle_dir,
        CallActorList(
            call_actors=[
                CallActor(
                    id="near",
                    name="Rowan",
                    role="someone with the person",
                    channel="audio_mix",
                ),
                CallActor(
                    id="other", name="Kai", role="a neighbour", channel="audio_mix"
                ),
            ]
        ),
    )
    summary = [
        {
            "actor": "near",
            "name": "Rowan",
            "triggered": True,
            "delivered": 1,
            "dropped": [],
        }
    ]
    events = [{"actor": "near", "kind": "delivered", "text": "Hurry up"}]

    placed = []
    # The provider has no voice left for Kai that no one else on the call has.
    chosen = cr.persona_voice
    monkeypatch.setattr(
        cr,
        "persona_voice",
        lambda persona, *args, **kwargs: (
            "" if persona.get("name") == "Kai" else chosen(persona, *args, **kwargs)
        ),
    )

    async def place_call(spec):
        placed.append(spec.model_dump_json())
        return _report(
            result_metadata={"call_actors": summary, "call_actor_events": events}
        )

    adapter = FakeAdapter()
    runner = cr.CallRunnerImpl(adapter, context, place_call=place_call)
    outcome = _run(
        runner,
        _FakeScenario("k1"),
        _runtime(metadata={"livekit_agent_name": "agent-w0"}),
    )
    # The grader reads every actor, the one that could not enter as never triggered.
    assert outcome.call_actors == (
        *({**row, "acted": 0} for row in summary),
        {"actor": "other", "name": "Kai", "triggered": False, "acted": 0},
    )
    [transcript] = [
        json.loads(data)
        for kind, _key, data in adapter.uploads
        if kind is cr.ArtifactKind.TRANSCRIPT
    ]
    record = transcript["call_actors"]
    assert record["summary"] == [{**row, "acted": 0} for row in summary] and record["events"] == events
    resolved, other = record["resolved"]
    assert (resolved["id"], resolved["instruction"]) == (
        "near",
        "You want them to hurry.",
    )
    # Kept on the record with why it never entered, and never put on the call.
    assert (other["id"], other["entered"], other["reason"]) == (
        "other",
        False,
        "no_distinct_voice",
    )
    assert "Rowan" in placed[0] and "Kai" not in placed[0]
    low, high = LOUDNESS_RANGE
    assert low <= resolved["loudness_ratio"] <= high


def _with_actors(tmp_path: Path) -> Path:
    from fi.alk.harness.actors import Actor, ActorList, save_actors

    tmp_path.mkdir(parents=True, exist_ok=True)
    save_actors(
        tmp_path,
        ActorList(
            actors=[
                Actor(
                    id="reviewer",
                    name="Robin",
                    role="reviewer",
                    permitted_actions=["records.approve"],
                )
            ]
        ),
    )
    return tmp_path


def test_actors_act_through_a_door_in_the_runner_and_no_token_is_ever_written(tmp_path, monkeypatch) -> None:
    from fi.alk.harness.actor_dispatch import DoorActs
    from fi.alk.harness.world_door import HttpExecutor

    bundle = _with_actors(tmp_path / "bundle")
    doc = {"actors": [{"id": "reviewer", "actions": [{"action": "records.approve"}]}]}
    proxy = RuntimeEndpoint(capability="tool_proxy", protocol="http", address="http://127.0.0.1:9")
    tools = RuntimeEndpoint(capability="tools_api", protocol="http", address="http://127.0.0.1:8")
    ready = _runtime(endpoints={"tool_proxy": proxy, "tools_api": tools, "database": _postgres_endpoint()})
    assert cr._actor_dispatcher({}, bundle, ready, "t1", 60.0) is None
    no_tools = _runtime(endpoints={"tool_proxy": proxy, "database": _postgres_endpoint()})
    assert cr._actor_dispatcher(doc, bundle, no_tools, "t1", 60.0) is None
    dispatcher = cr._actor_dispatcher(doc, bundle, ready, "t1", 60.0)
    assert isinstance(dispatcher.act, DoorActs)
    tools_executor = dispatcher.act.door.executor.tools
    assert isinstance(tools_executor, HttpExecutor) and tools_executor.upstream == "http://127.0.0.1:8"
    # Nothing an agent's process could read lets it act as an actor.
    assert not list(tmp_path.rglob("actor-token"))

def test_actor_rows_are_sealed_named_and_with_their_outcome_and_the_agent_s_are_left_to_its_calls(
    tmp_path, monkeypatch
) -> None:
    bundle = _with_actors(tmp_path)
    columns = [
        "name",
        "arguments",
        "result",
        "ok",
        "error",
        "at",
        "action_id",
        "actor_id",
        "outcome",
        "refusal_code",
        "parent_event_id",
        "on_behalf_of",
    ]
    rows = [
        (
            "request_approval",
            {"item": "d1"},
            {"ok": True},
            True,
            "",
            1.0,
            "a1",
            "agent",
            "committed",
            None,
            None,
            None,
        ),
        (
            "approve",
            {"item": "d1"},
            '{"ok": true}',
            True,
            "",
            2.0,
            "a2",
            "reviewer",
            "committed",
            None,
            "a1",
            None,
        ),
    ]
    monkeypatch.setitem(sys.modules, "psycopg", _FakePsycopg(rows, columns))
    runtime = _runtime(endpoints={"database": _postgres_endpoint()})
    assert cr._actor_rows(runtime, bundle) == [
        {
            "name": "records.approve",
            "arguments": {"item": "d1"},
            "result": {"ok": True},
            "ok": True,
            "error": "",
            "refused": False,
            "at": 2.0,
            "action_id": "a2",
            "actor_id": "reviewer",
            "actor_name": "Robin",
            "actor_role": "reviewer",
            "outcome": "committed",
            "refusal_code": None,
            "parent_event_id": "a1",
            "on_behalf_of": None,
        }
    ]
    assert [call.name for call in cr._collect_tool_trace_calls(runtime)] == [
        "request_approval"
    ]
    assert cr._actor_rows(runtime, tmp_path / "no-actors") == []


def test_with_actors_the_agent_s_sealed_calls_carry_their_own_action_ids_to_link_a_cause(
    tmp_path, monkeypatch
) -> None:
    bundle = _with_actors(tmp_path)
    columns = [
        "name",
        "arguments",
        "result",
        "ok",
        "error",
        "at",
        "action_id",
        "actor_id",
        "outcome",
    ]
    rows = [
        ("request_approval", {}, None, True, "", 1.0, "a1", "agent", "committed"),
        ("approve", {}, None, True, "", 2.0, "a2", "reviewer", "committed"),
        ("update", {}, None, False, "no", 3.0, "a3", "agent", "refused"),
    ]
    monkeypatch.setitem(sys.modules, "psycopg", _FakePsycopg(rows, columns))
    runtime = _runtime(endpoints={"database": _postgres_endpoint()})
    calls = cr._collect_tool_trace_calls(runtime)
    assert cr._agent_marks(runtime, bundle, calls) == [
        {"action_id": "a1", "outcome": "committed"},
        {"action_id": "a3", "outcome": "refused"},
    ]
    assert cr._agent_marks(runtime, bundle, calls[1:]) == [
        {"action_id": "a3", "outcome": "refused"}
    ]
    assert cr._agent_marks(runtime, bundle, [*calls, calls[0]]) == []


def test_a_world_actor_s_actions_are_sealed_with_the_transcript_never_as_the_agent_s_calls(
    tmp_path, monkeypatch
) -> None:
    _job_obj, context = _context(
        tmp_path=tmp_path, evidence_seam=EvidenceSeam.TOOL_TRACE
    )
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")
    _with_actors(context.bundle_dir)
    columns = [
        "name",
        "arguments",
        "result",
        "ok",
        "error",
        "at",
        "action_id",
        "actor_id",
        "outcome",
    ]
    rows = [
        ("request_approval", {}, None, True, "", 1.0, "a1", "agent", "committed"),
        ("approve", {}, None, True, "", 2.0, "a2", "reviewer", "committed"),
    ]
    monkeypatch.setitem(sys.modules, "psycopg", _FakePsycopg(rows, columns))

    async def place_call(spec):
        return _report(
            transcript="user: hello", messages=[{"role": "user", "content": "hello"}]
        )

    adapter = FakeAdapter()
    runner = cr.CallRunnerImpl(adapter, context, place_call=place_call)
    _run(
        runner,
        _FakeScenario("k1"),
        _runtime(
            endpoints={"database": _postgres_endpoint()},
            metadata={"livekit_agent_name": "agent-w0"},
        ),
    )
    sealed = {kind: data for kind, _key, data in adapter.uploads}
    trace = [
        json.loads(line)
        for line in sealed[cr.ArtifactKind.TOOL_TRACE].decode().splitlines()
    ]
    assert [(one["name"], one["action_id"]) for one in trace] == [
        ("request_approval", "a1")
    ]
    transcript = json.loads(sealed[cr.ArtifactKind.TRANSCRIPT])
    assert [
        (one["name"], one["actor_id"]) for one in transcript["world_actors"]["actions"]
    ] == [("records.approve", "reviewer")]


def test_a_hand_off_record_among_the_agent_s_calls_takes_no_action_id_and_moves_none(
    tmp_path, monkeypatch
) -> None:
    _job_obj, context = _context(
        tmp_path=tmp_path, evidence_seam=EvidenceSeam.TOOL_TRACE
    )
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")
    _with_actors(context.bundle_dir)
    columns = ["name", "arguments", "result", "ok", "error", "at", "action_id", "actor_id", "outcome"]
    rows = [
        ("request_approval", {}, None, True, "", 1.0, "a1", "agent", "committed"),
        ("approve", {}, None, True, "", 2.0, "a2", "reviewer", "committed"),
    ]
    monkeypatch.setitem(sys.modules, "psycopg", _FakePsycopg(rows, columns))

    async def place_call(spec):
        return _report(
            transcript="user: hello", messages=[{"role": "user", "content": "hello"}]
        )

    adapter = FakeAdapter()
    runner = cr.CallRunnerImpl(adapter, context, place_call=place_call)
    # The worker's run comes first in the record; it is not one of the world's actions.
    calls = (
        Call(name="Approvals worker", arguments={}, delegated_to="Approvals worker", handoff=True),
        Call(name="request_approval", arguments={}, ok=True, at=1.0),
    )
    monkeypatch.setattr(runner, "_collect_calls", lambda _runtime: calls)
    _run(
        runner,
        _FakeScenario("k1"),
        _runtime(
            endpoints={"database": _postgres_endpoint()},
            metadata={"livekit_agent_name": "agent-w0"},
        ),
    )
    sealed = {kind: data for kind, _key, data in adapter.uploads}
    trace = [json.loads(line) for line in sealed[cr.ArtifactKind.TOOL_TRACE].decode().splitlines()]
    assert [(one["name"], one.get("action_id")) for one in trace] == [
        ("Approvals worker", None),
        ("request_approval", "a1"),
    ]


def test_a_call_with_no_words_still_seals_what_an_actor_did_in_the_world(tmp_path, monkeypatch) -> None:
    _job_obj, context = _context(tmp_path=tmp_path, evidence_seam=EvidenceSeam.TOOL_TRACE)
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")
    _with_actors(context.bundle_dir)
    columns = ["name", "arguments", "result", "ok", "error", "at", "action_id", "actor_id", "outcome"]
    rows = [("approve", {}, None, True, "", 2.0, "a2", "reviewer", "committed")]
    monkeypatch.setitem(sys.modules, "psycopg", _FakePsycopg(rows, columns))

    async def place_call(spec):
        return _report(transcript="", messages=[])

    adapter = FakeAdapter()
    runner = cr.CallRunnerImpl(adapter, context, place_call=place_call)
    _run(
        runner,
        _FakeScenario("k1"),
        _runtime(endpoints={"database": _postgres_endpoint()}, metadata={"livekit_agent_name": "agent-w0"}),
    )
    sealed = {kind: data for kind, _key, data in adapter.uploads}
    transcript = json.loads(sealed[cr.ArtifactKind.TRANSCRIPT])
    assert [(one["name"], one["actor_id"]) for one in transcript["world_actors"]["actions"]] == [
        ("records.approve", "reviewer")
    ]


def test_a_call_where_only_a_world_actor_took_part_lists_it_in_the_call_s_actor_record(
    tmp_path, monkeypatch
) -> None:
    _job_obj, context = _context(
        tmp_path=tmp_path, evidence_seam=EvidenceSeam.TOOL_TRACE
    )
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")
    _with_actors(context.bundle_dir)
    columns = ["name", "arguments", "result", "ok", "error", "at", "action_id", "actor_id", "outcome"]
    rows = [("approve", {}, None, True, "", 2.0, "a2", "reviewer", "committed")]
    monkeypatch.setitem(sys.modules, "psycopg", _FakePsycopg(rows, columns))
    adapter = FakeAdapter()
    runner = cr.CallRunnerImpl(adapter, context, place_call=None)

    outcome = asyncio.run(
        runner._translate_report(
            _report(transcript="user: hello", messages=[{"role": "user", "content": "hello"}]),
            runtime=_runtime(endpoints={"database": _postgres_endpoint()}),
            scenario_key="k1",
            started_at=datetime.now(timezone.utc),
            actor_decisions=[
                {"actor": "reviewer", "play": "mocked", "action": "records.approve", "outcome": "committed"}
            ],
        )
    )

    row = {"actor": "reviewer", "name": "Robin", "triggered": True, "delivered": 1, "dropped": [],
           "acts_in_world": True, "acted": 1}
    assert row in outcome.call_actors
    [transcript] = [
        json.loads(data) for kind, _key, data in adapter.uploads if kind is cr.ArtifactKind.TRANSCRIPT
    ]
    # The run's actor filter reads this record, so a call where only the world actor took part is found by it.
    assert transcript["call_actors"]["summary"] == [row]


def test_a_trial_s_faults_go_to_the_world_database_and_are_cleared_after_the_call(tmp_path, monkeypatch) -> None:
    _job_obj, context = _context(tmp_path=tmp_path, evidence_seam=EvidenceSeam.TOOL_TRACE)
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")
    document = context.bundle_dir / "scenarios" / "k1" / "scenario.json"
    body = json.loads(document.read_text())
    body["faults"] = [{"action": "approve", "mode": "timeout_before_commit"}]
    document.write_text(json.dumps(body))
    written = []
    monkeypatch.setattr(cr, "_write_trial_state", lambda runtime, faults: written.append(list(faults)))

    async def place_call(spec):
        return _report(transcript="user: hello", messages=[{"role": "user", "content": "hello"}])

    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call)
    _run(runner, _FakeScenario("k1"), _runtime(endpoints={"database": _postgres_endpoint()},
                                                metadata={"livekit_agent_name": "agent-w0"}))
    assert written == [[{"action": "approve", "mode": "timeout_before_commit"}], []]
    assert not list(tmp_path.rglob("trial.json")) and not list(tmp_path.rglob("actor-token"))

def test_a_trial_with_no_faults_behind_a_tool_proxy_still_writes_its_own_none(tmp_path, monkeypatch) -> None:
    _job_obj, context = _context(tmp_path=tmp_path, evidence_seam=EvidenceSeam.TOOL_TRACE)
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")
    written = []
    monkeypatch.setattr(cr, "_write_trial_state", lambda runtime, faults: written.append(list(faults)))

    async def place_call(spec):
        return _report(transcript="user: hello", messages=[{"role": "user", "content": "hello"}])

    endpoints = {"database": _postgres_endpoint(), "tool_proxy": RuntimeEndpoint(capability="tool_proxy", protocol="http", address="http://p")}
    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call)
    _run(runner, _FakeScenario("k1"), _runtime(endpoints=endpoints, metadata={"livekit_agent_name": "agent-w0"}))
    # An earlier trial's faults left in the world can never apply to this one.
    assert written == [[]]


def test_a_world_database_out_of_reach_fails_only_a_trial_with_faults_to_set(monkeypatch) -> None:
    def unreachable(runtime, faults):
        raise OSError("connection to postgresql://world:secret@db refused")

    monkeypatch.setattr(cr, "_write_trial_state", unreachable)
    # Nothing to set: the trial runs as dev would have run it.
    cr._set_trial_faults(None, [])
    with pytest.raises(RuntimeError) as failed:
        cr._set_trial_faults(None, [{"action": "approve", "mode": "timeout_before_commit"}])
    # The failure names the error's type only: a database error's text can carry the world's password.
    assert "secret" not in str(failed.value) and "OSError" in str(failed.value)


def test_a_trial_s_action_log_is_read_over_one_connection_and_reopened_after_a_failed_read(
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    opened = []

    class _Connection:
        def __init__(self) -> None:
            self.fail = False
            self.closed = False

        def execute(self, _sql):
            if self.fail:
                raise RuntimeError("connection lost")
            return SimpleNamespace(
                description=[("name",)], fetchall=lambda: [("approve",)]
            )

        def close(self) -> None:
            self.closed = True

    def connect(_dsn, _timeout_ms=None):
        opened.append(_Connection())
        return opened[-1]

    monkeypatch.setattr(cr, "_read_only", connect)
    log = cr._ActionLog("postgresql://world")
    assert log() == log() == [{"name": "approve"}] and len(opened) == 1
    opened[0].fail = True
    with pytest.raises(RuntimeError):
        log()
    assert opened[0].closed and log() == [{"name": "approve"}] and len(opened) == 2


def test_an_actor_on_the_agent_s_tool_waits_for_the_route_the_log_records_for_it(
    tmp_path, monkeypatch
) -> None:
    bundle = _with_actors(tmp_path / "bundle")
    (bundle / "contract.json").write_text(
        json.dumps(
            {
                "tool_entrypoints": [
                    {
                        "tool": "ask_for_approval",
                        "endpoint": "/records/request_approval",
                    }
                ]
            }
        )
    )
    doc = {
        "actors": [
            {
                "id": "reviewer",
                "actions": [
                    {
                        "action": "records.approve",
                        "trigger": {"kind": "on_tool", "tool": "ask_for_approval"},
                    }
                ],
            }
        ]
    }
    proxy = RuntimeEndpoint(
        capability="tool_proxy", protocol="http", address="http://127.0.0.1:9"
    )
    tools = RuntimeEndpoint(capability="tools_api", protocol="http", address="http://127.0.0.1:8")
    ready = _runtime(endpoints={"tool_proxy": proxy, "tools_api": tools, "database": _postgres_endpoint()})
    dispatcher = cr._actor_dispatcher(doc, bundle, ready, "t1", 60.0)
    log = [{"name": "request_approval", "actor_id": "agent", "action_id": "a1"}]
    [(_actor, action)] = [(one[0], one[2]) for one in dispatcher.pending]
    assert dispatcher._cause(_actor, action.trigger, 0.0, log, 0) == "a1"


def test_an_actor_that_cannot_act_is_recorded_with_the_real_reason(tmp_path, monkeypatch):
    import fi.alk.harness.call_runner as cr

    bundle = _with_actors(tmp_path / "bundle")
    doc = {"actors": [{"id": "reviewer", "actions": [{"action": "records.approve"}]}]}
    why: list = []
    assert cr._actor_dispatcher(doc, bundle, _runtime(endpoints={"database": _postgres_endpoint()}), "t1", 60.0,
                                why_not=why) is None
    assert why[0][0] == "no_world_door"

def test_the_speaking_actors_actions_file_is_the_run_s_alone(tmp_path):
    import stat

    import fi.alk.harness.call_runner as cr

    path = cr._private_actions_file(tmp_path / "call-workers" / "actor-actions-r1.jsonl")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert path.read_text() == ""


def test_a_voice_trial_whose_fault_could_not_apply_reaches_the_scheduler_as_unmeasured(tmp_path, monkeypatch) -> None:
    _job_obj, context = _context(tmp_path=tmp_path, evidence_seam=EvidenceSeam.TOOL_TRACE)
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")
    document = context.bundle_dir / "scenarios" / "k1" / "scenario.json"
    body = json.loads(document.read_text())
    body["faults"] = [{"action": "approve", "mode": "timeout_before_commit"}]
    document.write_text(json.dumps(body))
    monkeypatch.setattr(cr, "_write_trial_state", lambda runtime, faults: None)

    async def place_call(spec):
        return _report(transcript="hello", messages=[])

    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call)
    outcome = _run(runner, _FakeScenario("k1"), _runtime(metadata={"livekit_agent_name": "agent-w0"}))
    # No world database held the fault, so the trial never ran under it.
    assert outcome.unmeasured and "could not be applied" in outcome.unmeasured[0]


def test_a_voice_trial_whose_log_or_faults_cannot_be_read_says_its_faults_are_unreadable(tmp_path, monkeypatch):
    from types import SimpleNamespace

    _job_obj, context = _context(tmp_path=tmp_path, evidence_seam=EvidenceSeam.TOOL_TRACE)
    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=None)
    monkeypatch.setattr(cr, "_find_postgres_endpoint", lambda runtime: SimpleNamespace(address="dsn"))
    fault = [{"action": "approve", "mode": "unavailable"}]

    def unreadable(dsn, **_):
        raise RuntimeError("down")

    monkeypatch.setattr(cr, "trial_faults_readable", lambda dsn: False)
    [notice] = runner._fault_notices(object(), fault, [])
    assert notice["code"] == "fault_unreadable" and cr.unmeasured_reasons([notice])
    monkeypatch.setattr(cr, "trial_faults_readable", lambda dsn: True)
    monkeypatch.setattr(cr, "_read_action_log", unreadable)
    [notice] = runner._fault_notices(object(), fault, [])
    assert notice["code"] == "fault_unreadable" and "action log could not be read" in notice["message"]


def test_a_voice_call_with_no_words_still_seals_the_notice_for_a_fault_it_never_reached(tmp_path, monkeypatch) -> None:
    _job_obj, context = _context(tmp_path=tmp_path, evidence_seam=EvidenceSeam.TOOL_TRACE)
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")
    document = context.bundle_dir / "scenarios" / "k1" / "scenario.json"
    body = json.loads(document.read_text())
    body["faults"] = [{"action": "approve", "mode": "timeout_before_commit"}]
    document.write_text(json.dumps(body))
    monkeypatch.setattr(cr, "_write_trial_state", lambda runtime, faults: None)

    async def place_call(spec):
        return _report(transcript="", messages=[])

    adapter = FakeAdapter()
    runner = cr.CallRunnerImpl(adapter, context, place_call=place_call)
    _run(runner, _FakeScenario("k1"), _runtime(metadata={"livekit_agent_name": "agent-w0"}))
    sealed = {kind: data for kind, _key, data in adapter.uploads}
    # The trial ran without its fault; with no words, that is the only place the person learns it.
    assert json.loads(sealed[cr.ArtifactKind.TRANSCRIPT])["notices"][0]["code"] in ("fault_not_applied", "fault_not_triggered")


def _logged(name, arguments, *, error="", outcome="committed", effect="write", actor="agent", action_id=""):
    return {
        "name": name, "action": f"orders.{name}", "arguments": arguments, "outcome": outcome, "error": error,
        "effect": effect, "actor_id": actor, "action_id": action_id or f"{name}-{len(str(arguments))}-{error}",
        "ok": outcome == "committed",
    }


def test_a_retry_after_a_lost_answer_that_commits_again_is_a_duplicate():
    from fi.alk.harness.call_runner import duplicate_commits

    log = [
        _logged("issue_refund", {"order": 7}, error="response_lost", action_id="a1"),
        _logged("issue_refund", {"order": 7}, action_id="a2"),
    ]
    [found] = duplicate_commits(log)
    assert (found["action"], found["first_action_id"], found["repeat_action_id"]) == ("orders.issue_refund", "a1", "a2")


def test_a_retry_with_other_arguments_is_not_a_duplicate():
    from fi.alk.harness.call_runner import duplicate_commits

    log = [
        _logged("issue_refund", {"order": 7}, error="response_lost"),
        _logged("issue_refund", {"order": 8}),
    ]
    assert duplicate_commits(log) == []


def test_a_read_retried_is_never_a_duplicate():
    from fi.alk.harness.call_runner import duplicate_commits

    log = [
        _logged("get_order", {"order": 7}, error="response_lost", effect="read"),
        _logged("get_order", {"order": 7}, effect="read"),
    ]
    assert duplicate_commits(log) == []


def test_a_keyed_retry_the_door_answered_and_a_retry_without_a_lost_answer_are_not_duplicates():
    from fi.alk.harness.call_runner import duplicate_commits

    answered = [
        _logged("issue_refund", {"order": 7}, error="response_lost"),
        _logged("issue_refund", {"order": 7}, outcome="duplicate"),
    ]
    twice_on_purpose = [_logged("issue_refund", {"order": 7}), _logged("issue_refund", {"order": 7})]
    assert duplicate_commits(answered) == [] and duplicate_commits(twice_on_purpose) == []


def test_arguments_read_back_as_json_text_still_match():
    from fi.alk.harness.call_runner import duplicate_commits

    log = [
        _logged("issue_refund", '{"order": 7}', error="response_lost"),
        _logged("issue_refund", {"order": 7}),
    ]
    assert len(duplicate_commits(log)) == 1


def test_a_retry_that_rewords_free_text_but_writes_the_same_record_is_a_duplicate():
    from fi.alk.harness.call_runner import duplicate_commits

    log = [
        _logged("issue_refund", {"order": 7, "amount": 500, "note": "first try"}, error="response_lost"),
        _logged("issue_refund", {"order": 7, "amount": 500, "note": "retrying after the timeout"}),
    ]
    # The reference fixes the record and the value; the note is the agent's own words.
    solution = [{"tool": "issue_refund", "arguments": {"order": 7, "amount": 500}, "fixed": {"order": "the person", "amount": "the person"}}]
    [found] = duplicate_commits(log, solution)
    assert found["action"] == "orders.issue_refund"
    # Another record is another write, whatever the free text says.
    other = [log[0], _logged("issue_refund", {"order": 8, "amount": 500, "note": "first try"})]
    assert duplicate_commits(other, solution) == []
    # With nothing fixed for the action, every argument still has to match.
    assert duplicate_commits(log) == []


def test_an_identical_retry_is_a_duplicate_even_when_the_solution_fixes_a_field_the_call_does_not_carry():
    from fi.alk.harness.call_runner import duplicate_commits

    log = [
        _logged("issue_refund", {"order": 7}, error="response_lost"),
        _logged("issue_refund", {"order": 7}),
    ]
    solution = [{"tool": "issue_refund", "arguments": {"reason": "late"}, "fixed": {"reason": "the person"}}]
    assert len(duplicate_commits(log, solution)) == 1


def test_writes_to_two_records_are_not_a_duplicate_when_only_their_amount_is_fixed():
    from fi.alk.harness.call_runner import duplicate_commits

    log = [
        _logged("issue_refund", {"order": 7, "amount": 500}, error="response_lost"),
        _logged("issue_refund", {"order": 8, "amount": 500}),
    ]
    solution = [{"tool": "issue_refund", "arguments": {"amount": 500}, "fixed": {"amount": "the person"}}]
    assert duplicate_commits(log, solution) == []


def test_writes_to_two_records_named_by_text_ids_are_not_a_duplicate():
    """A record named by a text id (order-A, order-B) is another record, however the solution fixes the amount;
    the same id with reworded free text is still one write."""
    from fi.alk.harness.call_runner import duplicate_commits

    solution = [{"tool": "issue_refund", "arguments": {"amount": 500}, "fixed": {"amount": "the person"}}]
    two_records = [
        _logged("issue_refund", {"order_id": "order-A", "amount": 500}, error="response_lost"),
        _logged("issue_refund", {"order_id": "order-B", "amount": 500}),
    ]
    reworded = [
        _logged("issue_refund", {"order_id": "order-A", "amount": 500, "note": "first try"}, error="response_lost"),
        _logged("issue_refund", {"order_id": "order-A", "amount": 500, "note": "retrying"}),
    ]

    assert duplicate_commits(two_records, solution) == []
    assert len(duplicate_commits(reworded, solution)) == 1


def test_an_unreadable_action_log_leaves_duplicates_unknown_never_cleared(monkeypatch):
    from types import SimpleNamespace

    from fi.alk.harness import call_runner as cr

    monkeypatch.setattr(cr, "_find_postgres_endpoint", lambda runtime: SimpleNamespace(address="dsn"))

    def unreadable(dsn, **_):
        raise RuntimeError("down")

    monkeypatch.setattr(cr, "_read_action_log", unreadable)
    assert cr._duplicates(object()) is None
    # Missing evidence for every check that reads the agent's calls or the log, never an empty log.
    assert cr.missing_evidence([dict(cr.DUPLICATES_UNREADABLE)], {}) == {"tool_calls": cr.DUPLICATES_UNREADABLE["message"]}
    monkeypatch.setattr(cr, "_read_action_log", lambda dsn, **_: [])
    assert cr._duplicates(object()) == []


def test_egress_refusals_are_never_the_agent_s_tool_calls_and_show_once_per_host_and_method(monkeypatch):
    from fi.alk.harness import call_runner as cr
    from fi.alk.harness.egress_proxy import REFUSAL_CODE

    def blocked(host, method):
        return {"name": host, "actor_id": "agent", "outcome": "refused", "ok": False, "refusal_code": REFUSAL_CODE,
                "arguments": {"host": host, "method": method}, "at": 1.0, "action_id": f"{host}-{method}"}

    log = [
        blocked("telemetry.example.net", "POST"),
        blocked("telemetry.example.net", "POST"),
        blocked("telemetry.example.net", "GET"),
        _logged("issue_refund", {"order": 7}),
    ]
    monkeypatch.setattr(cr, "_read_action_log", lambda dsn, **_: log)
    assert [row["name"] for row in cr.read_tool_trace("dsn")] == ["issue_refund"]
    [agent] = cr.trial_state_changes(log, {})
    assert agent["attempted"] == 1 and agent["refused"] == 0
    assert agent["blocked"] == [
        {"host": "telemetry.example.net", "method": "POST", "count": 2},
        {"host": "telemetry.example.net", "method": "GET", "count": 1},
    ]


def test_voice_trial_without_actors_seals_world_changes(tmp_path, monkeypatch) -> None:
    _job_obj, context = _context(tmp_path=tmp_path, evidence_seam=EvidenceSeam.TOOL_TRACE)
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")
    columns = ["name", "arguments", "result", "ok", "error", "at", "action_id", "actor_id", "outcome"]
    rows = [("issue_refund", {"order": 7}, None, True, "", 2.0, "a1", "agent", "committed")]
    monkeypatch.setitem(sys.modules, "psycopg", _FakePsycopg(rows, columns))

    async def place_call(spec):
        return _report()

    adapter = FakeAdapter()
    runner = cr.CallRunnerImpl(adapter, context, place_call=place_call)
    _run(
        runner,
        _FakeScenario("k1"),
        _runtime(endpoints={"database": _postgres_endpoint()}, metadata={"livekit_agent_name": "agent-w0"}),
    )
    sealed = {kind: data for kind, _key, data in adapter.uploads}
    world = json.loads(sealed[cr.ArtifactKind.TRANSCRIPT])["world_actors"]
    assert world["actions"] == [] and world["state_changes"][0]["committed_actions"] == ["issue_refund"]
    assert "records" in world and "start" in world
    # Counts a door stood for say so, so a reader never takes a record from before the door for one.
    assert world["door"] is True


def test_a_voice_trial_whose_tools_the_door_cannot_see_says_world_changes_were_not_recorded(tmp_path) -> None:
    _job_obj, context = _context(tmp_path=tmp_path, evidence_seam=None)
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")

    async def place_call(spec):
        return _report()

    adapter = FakeAdapter()
    runner = cr.CallRunnerImpl(adapter, context, place_call=place_call)
    _run(runner, _FakeScenario("k1"), _runtime(metadata={"livekit_agent_name": "agent-w0"}))
    sealed = {kind: data for kind, _key, data in adapter.uploads}
    world = json.loads(sealed[cr.ArtifactKind.TRANSCRIPT])["world_actors"]
    assert world == {"recorded": False, "why": "in_process_tools"}


def test_a_voice_trial_reads_its_start_before_its_pre_run_sessions_write(tmp_path, monkeypatch) -> None:
    _job_obj, context = _context(tmp_path=tmp_path, evidence_seam=EvidenceSeam.TOOL_TRACE)
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")
    monkeypatch.setitem(sys.modules, "psycopg", _FakePsycopg([], ["name"]))
    order: list[str] = []

    class _PreRunActors:
        pre_run = ["session"]
        read_log = None
        briefs = None

        async def replay(self) -> None:
            order.append("replay")

        async def run(self) -> None:
            await asyncio.Event().wait()

        async def settle(self) -> None:
            return None

        async def send_reported(self) -> None:
            return None

        def record(self) -> list:
            return []

    monkeypatch.setattr(cr, "_actor_dispatcher", lambda *args, **kwargs: _PreRunActors())
    monkeypatch.setattr(cr.TrialRecords, "start", lambda self, key, dsn: order.append("start"))
    monkeypatch.setattr(cr.TrialRecords, "rebase", lambda self, key, dsn: order.append("rebase"))

    async def place_call(spec):
        order.append("call")
        return _report()

    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call)
    _run(
        runner,
        _FakeScenario("k1"),
        _runtime(endpoints={"database": _postgres_endpoint()}, metadata={"livekit_agent_name": "agent-w0"}),
    )
    assert order == ["start", "replay", "rebase", "call"]


def test_a_voice_trial_on_a_seal_without_a_world_door_says_its_world_actions_were_not_seen() -> None:
    """The voice runner reads the same rule as chat: no door and no proxy in the seal, the agent's 8 calls in its own
    trace and none of them in the action log. Unknown, never "0 attempted"."""
    from fi.alk.harness.world.runtime import Call

    calls = [Call(name="find_items", arguments={"query": "kayak"}, ok=True)] * 8
    sealed_without = {"target_http": object(), "mcp_toolbox": object(), "world_db": object()}

    assert cr.sealed_without_door(sealed_without, calls, []) is True
    # A seal with a door or a tool proxy saw the agent's calls, or would have: its counts stand.
    assert cr.sealed_without_door({**sealed_without, "world_door": object()}, calls, []) is False
    assert cr.sealed_without_door({**sealed_without, "tool_proxy": object()}, calls, []) is False
    assert cr.sealed_without_door(sealed_without, calls, None) is False
    assert cr.SEALED_WITHOUT_DOOR == {"recorded": False, "why": "sealed_without_door"}


@pytest.mark.parametrize("field", ["order", "orderId", "reference", "account"])
def test_writes_to_two_records_are_not_a_duplicate_whatever_the_field_naming_the_record_is_called(field):
    """Any argument that is not the agent's own words may name the record, so another value is another write;
    a reworded note on the same record is still one write."""
    from fi.alk.harness.call_runner import duplicate_commits

    solution = [{"tool": "issue_refund", "arguments": {"amount": 500}, "fixed": {"amount": "the person"}}]
    two_records = [
        _logged("issue_refund", {field: "order-A", "amount": 500}, error="response_lost"),
        _logged("issue_refund", {field: "order-B", "amount": 500}),
    ]
    reworded = [
        _logged("issue_refund", {field: "order-A", "amount": 500, "customerNote": "first try"}, error="response_lost"),
        _logged("issue_refund", {field: "order-A", "amount": 500, "customerNote": "retrying"}),
    ]

    assert duplicate_commits(two_records, solution) == []
    assert len(duplicate_commits(reworded, solution)) == 1


def test_a_voice_trial_whose_in_process_tool_ran_past_a_door_that_went_down_misses_its_call_evidence(tmp_path, monkeypatch) -> None:
    """The agent's own record holds two refunds and the door logged one: the second ran with nothing recording what
    it did to the world, so the voice trial's evidence of its calls is missing, as a chat trial's is."""
    _job_obj, context = _context(tmp_path=tmp_path, evidence_seam=EvidenceSeam.TOOL_TRACE)
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")
    (context.bundle_dir / "contract.json").write_text(
        json.dumps({"tool_entrypoints": [{"tool": "issue_refund", "mode": "import", "module": "tools"}]})
    )
    trace = tmp_path / "trace.jsonl"
    columns = ["name", "arguments", "result", "ok", "error", "at", "action_id", "actor_id", "outcome"]
    rows = [("issue_refund", {"order": 7}, None, True, "", 2.0, "a1", "agent", "committed")]
    monkeypatch.setitem(sys.modules, "psycopg", _FakePsycopg(rows, columns))

    async def place_call(spec):
        # The agent records both of its refunds during the call.
        trace.write_text(
            "".join(json.dumps({"name": "issue_refund", "arguments": {"order": n}, "output": "ok"}) + "\n" for n in (7, 8)),
            encoding="utf-8",
        )
        return _report()

    adapter = FakeAdapter()
    runner = cr.CallRunnerImpl(adapter, context, place_call=place_call)
    outcome = _run(
        runner,
        _FakeScenario("k1"),
        _runtime(
            endpoints={"database": _postgres_endpoint()},
            metadata={"livekit_agent_name": "agent-w0", "tool_trace_path": str(trace)},
        ),
    )
    assert "1 of the agent's calls ran without the world's door recording them" in outcome.missing_evidence["tool_calls"]


def test_each_call_is_paired_with_its_own_row_past_a_door_mark_and_a_row_from_before_the_run(monkeypatch):
    from types import SimpleNamespace

    from fi.alk.harness.world_door import EVIDENCE_INCOMPLETE

    log = [
        {"name": "issue_refund", "actor_id": "agent", "action_id": "seed", "idempotency_key": "t:agent:h0",
         "outcome": "committed"},
        {"name": "issue_refund", "actor_id": "agent", "action_id": "mark", "refusal_code": EVIDENCE_INCOMPLETE,
         "outcome": "unrecorded"},
        {"name": "issue_refund", "actor_id": "agent", "action_id": "a1", "outcome": "committed"},
        {"name": "issue_refund", "actor_id": "agent", "action_id": "a2", "outcome": "refused"},
    ]
    monkeypatch.setattr(cr, "_find_postgres_endpoint", lambda runtime: SimpleNamespace(address="dsn"))
    monkeypatch.setattr(cr, "_read_action_log", lambda dsn, **_: log)
    calls = [Call(name="issue_refund", arguments={}, ok=True), Call(name="issue_refund", arguments={}, ok=False)]

    marks = cr._agent_marks(object(), Path("."), calls)

    assert [(one["action_id"], one["outcome"]) for one in marks] == [("a1", "committed"), ("a2", "refused")]


def test_a_retry_whose_flag_is_true_where_the_first_had_1_is_another_write():
    from fi.alk.harness.call_runner import duplicate_commits

    log = [
        _logged("issue_refund", {"order": 7, "notify": 1}, error="response_lost"),
        _logged("issue_refund", {"order": 7, "notify": True}),
    ]
    assert duplicate_commits(log) == []


def test_a_voice_trial_writes_a_fault_named_by_its_tool_as_that_tool_s_route_action(tmp_path, monkeypatch) -> None:
    _job_obj, context = _context(tmp_path=tmp_path, evidence_seam=EvidenceSeam.TOOL_TRACE)
    _write_scenario_doc(context.bundle_dir, scenario_key="k1")
    (context.bundle_dir / "contract.json").write_text(
        json.dumps({"tool_entrypoints": [{"tool": "issue_refund", "endpoint": "/orders/refund/"}]})
    )
    document = context.bundle_dir / "scenarios" / "k1" / "scenario.json"
    body = json.loads(document.read_text())
    body["faults"] = [{"action": "issue_refund", "mode": "unavailable"}]
    document.write_text(json.dumps(body))
    written: list[list] = []
    monkeypatch.setattr(cr, "_write_trial_state", lambda runtime, faults: written.append(list(faults)))

    async def place_call(spec):
        return _report()

    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=place_call)
    _run(runner, _FakeScenario("k1"), _runtime(metadata={"livekit_agent_name": "agent-w0"}))

    assert written and written[0] == [{"action": "orders.refund", "mode": "unavailable"}]


def test_a_langchain_span_whose_input_is_the_arguments_text_pairs_with_the_agent_s_own_call():
    """A LangGraph agent reports its calls, and the framework's spans record the same calls each as one `input`
    holding the arguments' repr and its answer as the framework's message object, ended before the agent's report
    is timed: each call is counted once, so a door
    that recorded every call leaves no notice. Timings and shapes are a real chat trial's."""
    import tempfile

    from fi.alk.harness.call_runner import _with_span_calls, evidence_notices

    lookup, cancel = {"order_id": "BK-2002"}, {"order_id": "BK-2002", "reason": "bought twice"}

    def span(name, arguments, at, ended, event):
        # LangChain's tool span: the arguments' repr as one input, the answer as its ToolMessage object.
        message = f"content='{json.dumps(answers[name])}' name='{name}' tool_call_id='call_{event}'"
        return {"name": name, "arguments": {"input": repr(arguments)}, "output": repr({"output": message}),
                "is_error": False, "source": "otel", "at": at, "ended_at": ended, "event_id": event}

    answers = {"get_order_status": {"found": True, "order_id": "BK-2002"}, "cancel_order": {"cancelled": True}}

    spans = [
        span("get_order_status", lookup, 349.100, 349.243, "c7e48f222416d22c"),
        span("get_order_status", lookup, 355.054, 355.071, "9eb48efbaf238416"),
        span("cancel_order", cancel, 356.852, 356.868, "a77e326651cc93e4"),
    ]
    reported = [
        Call(name="get_order_status", arguments=dict(lookup), ok=True, at=350.748, result=answers["get_order_status"]),
        Call(name="get_order_status", arguments=dict(lookup), ok=True, at=358.97095,
             result=answers["get_order_status"]),
        Call(name="cancel_order", arguments=dict(cancel), ok=True, at=358.97097, result=answers["cancel_order"]),
    ]
    with tempfile.TemporaryDirectory() as folder:
        trace = Path(folder) / "agent-tool-calls.jsonl"
        trace.write_text("".join(json.dumps(one) + "\n" for one in spans))
        calls = _with_span_calls(reported, _runtime(metadata={"tool_trace_path": str(trace)}))

    assert sorted(call.name for call in calls) == ["cancel_order", "get_order_status", "get_order_status"]
    # Each keeps the answer the agent's own report gave, never the framework's message text.
    assert all(call.result == answers[call.name] for call in calls)
    log = [{"name": one, "actor_id": "agent", "outcome": "committed"}
           for one in ("get_order_status", "get_order_status", "cancel_order")]
    assert evidence_notices(log, calls, {"get_order_status", "cancel_order"}) == []


def _ride_contract():
    """The ride voice agent's tools as its contract binds them: each a method of the agent, most calling the world over
    a route (one under another name), some agent-only steps that never reach the world."""
    routes = {
        "create_guest_rider": "create_guest_rider", "geocode_address": "geocode_address",
        "confirm_address": "", "get_ride_options": "get_ride_options", "select_ride_option": "",
        "get_payment_methods": "get_payment_methods", "select_payment_method": "",
        "send_payment_link_sms": "send_payment_link_sms", "check_payment_link_status": "get_payment_link_status",
        "transfer_to_human": "transfer_to_human",
    }
    return {
        "tool_entrypoints": [
            {"tool": tool, "mode": "construct", "endpoint": route, "method": "POST",
             "module": "uber_voice_agent.ride_agent", "callable": f"RideBookingAgent.{tool}"}
            for tool, route in routes.items()
        ]
    }


def test_a_tool_proxy_trial_counts_each_tool_by_its_route_and_never_an_agent_only_step():
    """A real ride call: 22 calls the agent reported against 19 rows the tool proxy logged under world actions. Every
    call is accounted for, so the trial is measured."""
    reported = (
        ["create_guest_rider"] + ["geocode_address"] * 2 + ["confirm_address"] * 2 + ["get_ride_options"]
        + ["select_ride_option"] + ["get_payment_methods"] * 3 + ["select_payment_method"]
        + ["send_payment_link_sms"] * 2 + ["check_payment_link_status"] * 8 + ["transfer_to_human"]
    )
    calls = [Call(name=name, arguments={}, ok=True) for name in reported]
    logged = (
        ["lookup_rider_by_phone", "create_guest_rider"] + ["geocode_address"] * 2 + ["get_ride_options"]
        + ["get_payment_link_status"] * 8 + ["get_payment_methods"] * 3 + ["send_payment_link_sms"] * 2
        + ["transfer_to_human"]
    )
    log = [{"name": name, "action": name, "actor_id": "agent", "outcome": "committed"} for name in logged]

    held = cr.in_process_tools(_ride_contract(), {"tool_proxy": object()})

    assert (len(calls), len(log)) == (22, 19)
    assert held["check_payment_link_status"]["action"] == "get_payment_link_status" and "confirm_address" not in held
    assert cr.evidence_notices(log, calls, held) == []
    # A routed call the proxy did not log is still one it missed.
    assert cr.evidence_notices(log[:-1], calls, held)[0]["count"] == 1


def test_behind_the_in_process_door_every_bound_tool_is_held_by_its_own_name():
    """The in-process door wraps every bound tool and logs it by its name: an agent-only step it kept no row for is
    a call that ran past it."""
    held = cr.in_process_tools(_ride_contract(), {"world_door": object()})
    calls = [Call(name="confirm_address", arguments={}, ok=True)]

    assert held["confirm_address"] == {"action": "confirm_address"}
    assert cr.evidence_notices([], calls, held)[0]["count"] == 1
    assert cr.evidence_notices([{"name": "confirm_address", "actor_id": "agent"}], calls, held) == []


def test_a_langgraph_trial_behind_the_in_process_door_is_measured_when_the_door_logged_every_call():
    """A real in-process LangGraph call: a lookup, the cancel, a second lookup. Each is a span (LangChain's input
    repr and ToolMessage answer) and the agent's own report, timed after its span; the door logged the three. The
    calls are counted once each and none ran past the door, so the trial's tool evidence stands."""
    import tempfile

    from fi.alk.harness.call_runner import _with_span_calls

    lookup, cancel = {"order_id": "BK-2002"}, {"order_id": "BK-2002", "reason": "requested while processing"}
    answers = {"get_order_status": {"found": True, "status": "processing"}, "cancel_order": {"cancelled": True}}

    def span(name, arguments, at, ended, event):
        message = f"content='{json.dumps(answers[name])}' name='{name}' tool_call_id='call_{event}'"
        return {"name": name, "arguments": {"input": repr(arguments)}, "output": repr({"output": message}),
                "is_error": False, "source": "otel", "at": at, "ended_at": ended, "event_id": event}

    spans = [
        span("get_order_status", lookup, 12.141, 12.311, "7c4bc65c239c139f"),
        span("cancel_order", cancel, 14.125, 14.143, "5a41ab9cbff8a791"),
        span("get_order_status", lookup, 22.893, 22.910, "04f55d259eca13b4"),
    ]
    reported = [
        Call(name="get_order_status", arguments=dict(lookup), ok=True, at=16.98242, result=answers["get_order_status"]),
        Call(name="cancel_order", arguments=dict(cancel), ok=True, at=16.98245, result=answers["cancel_order"]),
        Call(name="get_order_status", arguments=dict(lookup), ok=True, at=27.72677, result=answers["get_order_status"]),
    ]
    contract = {"tool_entrypoints": [
        {"tool": tool, "mode": "import", "module": "agent.tools", "callable": tool}
        for tool in ("get_order_status", "cancel_order")
    ]}
    log = [{"name": name, "action": name, "actor_id": "agent", "outcome": outcome}
           for name, outcome in (("get_order_status", "committed"), ("cancel_order", "committed"),
                                 ("get_order_status", "committed"))]
    with tempfile.TemporaryDirectory() as folder:
        trace = Path(folder) / "agent-tool-calls.jsonl"
        trace.write_text("".join(json.dumps(one) + "\n" for one in spans))
        calls = _with_span_calls(reported, _runtime(metadata={"tool_trace_path": str(trace)}))

    held = cr.in_process_tools(contract, {"world_door": object()})

    assert len(calls) == 3
    assert cr.evidence_notices(log, calls, held) == []


def _order_routes(*tools):
    entries = {
        "get_order": {"endpoint": "/orders/{order_id}", "method": "GET"},
        "cancel_order": {"endpoint": "/orders/{order_id}", "method": "DELETE"},
        "confirm_order": {"endpoint": ""},
    }
    return {"tool_entrypoints": [
        {"tool": tool, "mode": "construct", "module": "agent", "callable": f"Agent.{tool}", **entries[tool]}
        for tool in tools
    ]}


def test_calls_on_a_route_with_a_part_pair_with_the_proxy_s_rows_for_each_part():
    held = cr.in_process_tools(_order_routes("get_order"), {"tool_proxy": object()})
    calls = [Call(name="get_order", arguments={"order_id": one}, ok=True) for one in ("1043", "1044")]
    log = [{"name": one, "action": f"orders.{one}", "method": "GET", "actor_id": "agent"} for one in ("1043", "1044")]

    assert cr.evidence_notices(log, calls, held) == []


def test_a_dropped_delete_is_never_hidden_by_the_get_of_the_same_route():
    held = cr.in_process_tools(_order_routes("get_order", "cancel_order"), {"tool_proxy": object()})
    calls = [Call(name="get_order", arguments={}, ok=True), Call(name="cancel_order", arguments={}, ok=True)]
    # The proxy logged the read; the cancel's row never came.
    log = [{"name": "1043", "action": "orders.1043", "method": "GET", "actor_id": "agent"}]

    assert cr.evidence_notices(log, calls, held)[0]["count"] == 1


def _proxied(**routes):
    contract = {"tool_entrypoints": [
        {"tool": tool, "mode": "construct", "module": "agent", "callable": f"Agent.{tool}", "endpoint": endpoint,
         "method": method}
        for tool, (method, endpoint) in routes.items()
    ]}
    return cr.in_process_tools(contract, {"tool_proxy": object()})


def test_a_proxy_row_named_like_the_tool_never_stands_for_a_call_made_with_another_method():
    """The proxy names its row by the route's last part: a POST row named `get_order` is not the GET's record."""
    held = _proxied(get_order=("GET", "/orders/get_order"), place_order=("POST", "/orders/get_order"))
    calls = [Call(name="get_order", arguments={}, ok=True), Call(name="place_order", arguments={}, ok=True)]
    log = [{"name": "get_order", "action": "orders.get_order", "method": "POST", "actor_id": "agent"}]

    assert cr.evidence_notices(log, calls, held)[0]["count"] == 1
    # Nor when the GET is the only call held: the POST row is still not its record.
    assert cr.evidence_notices(log, calls[:1], _proxied(get_order=("GET", "/orders/get_order")))[0]["count"] == 1


def test_a_proxy_row_for_another_route_with_the_same_last_part_is_not_the_tool_s_record():
    """`/ticket/update` is logged with the name `update`; a tool named `update` bound to `/crm/update` never made it."""
    held = _proxied(update=("POST", "/crm/update"))
    calls = [Call(name="update", arguments={}, ok=True)]
    log = [{"name": "update", "action": "ticket.update", "method": "POST", "actor_id": "agent"}]

    assert cr.evidence_notices(log, calls, held)[0]["count"] == 1


def test_one_proxy_row_settles_one_call_when_two_tools_share_its_route():
    held = _proxied(update_address=("POST", "/orders/update"), update_delivery=("POST", "/orders/update"))
    calls = [Call(name="update_address", arguments={}, ok=True), Call(name="update_delivery", arguments={}, ok=True)]
    row = {"name": "update", "action": "orders.update", "method": "POST", "actor_id": "agent"}

    assert cr.evidence_notices([row], calls, held)[0]["count"] == 1
    assert cr.evidence_notices([row, dict(row)], calls, held) == []


def test_a_row_goes_to_its_own_route_before_one_with_a_part_that_also_matches_it():
    held = _proxied(cancel_order=("POST", "/orders/{order_id}"), refresh_orders=("POST", "/orders/refresh"))
    calls = [Call(name="cancel_order", arguments={}, ok=True), Call(name="refresh_orders", arguments={}, ok=True)]
    log = [{"name": "refresh", "action": "orders.refresh", "method": "POST", "actor_id": "agent"},
           {"name": "1043", "action": "orders.1043", "method": "POST", "actor_id": "agent"}]

    assert cr.evidence_notices(log, calls, held) == []
    assert cr.evidence_notices(log[:1], calls, held)[0]["count"] == 1


def test_were_the_in_process_door_beside_the_proxy_it_would_decide_and_hold_every_bound_tool():
    """The bundle never installs both (the in-process door is left out when there is a tool proxy); were both there,
    the in-process door wraps every bound tool, so a step with no route is still held by its name."""
    both = cr.in_process_tools(_order_routes("get_order", "confirm_order"), {"tool_proxy": object(), "world_door": object()})

    assert both["confirm_order"] == {"action": "confirm_order"} and both["get_order"] == {"action": "get_order"}


# -- a voice trial reads its calls as before sources were named -----------------------------------
#
# The voice runner keeps its precedence: the trace file's records whenever the file holds a graded call, the
# world's table only when it holds none, spans joined as before. Naming each record's source must change nothing
# else: the same calls, in the same order, with the same fields and the same grading. Each case is read twice, with
# the span pairing as it was and as it is now, and compared field for field.


def _lend_span_as_it_was(span: Call, other: Call) -> None:
    if not (other.worker_id or other.worker_label):
        other.worker_id, other.worker_label = span.worker_id, span.worker_label
    other.delegated_to = other.delegated_to or span.delegated_to
    other.transferred = other.transferred or span.transferred
    other.ended_at = other.ended_at or span.ended_at
    if not other.runtime_ids:
        other.runtime_ids = span.runtime_ids
        other.parent_span_id, other.relationship = (
            span.parent_span_id,
            span.relationship,
        )
    other.at = span.at or other.at


_NAMED = {"source", "seen_by", "trace_mark"}

_SPOKEN_LINES = (
    {"name": "look_up_order", "arguments": {"order_id": "A1001"}, "output": "shipped", "at": 1.0},
    {"name": "start_return", "arguments": {"order_id": "A1001"}, "output": "started", "at": 2.0},
)
_AGREEING_SPANS = (
    {"name": "look_up_order", "arguments": "{\"order_id\": \"A1001\"}", "output": "shipped", "source": "otel",
     "agent_key": "Support", "event_id": "e1", "at": 0.9, "ended_at": 1.1},
    {"name": "start_return", "arguments": "{\"order_id\": \"A1001\"}", "output": "started", "source": "otel",
     "agent_key": "Support", "event_id": "e2", "at": 1.9, "ended_at": 2.1},
)
_HANDOFF_ONLY = (
    {"name": "Returns desk", "arguments": {}, "output": "done", "source": "otel", "kind": "handoff",
     "delegated_to": "Returns desk", "at": 1.0},
)


def _table_rows() -> list[Call]:
    return [
        Call(name="look_up_order", arguments={"order_id": "A1001"}, result="shipped", at=5.0),
        Call(name="look_up_order", arguments={"order_id": "A1001"}, result="shipped", at=6.0),
    ]


def _voice_reading(tmp_path, monkeypatch, *, lines, seam, table=None, table_fails=False, lend):
    folder = tmp_path / lend.__name__
    folder.mkdir()
    trace = folder / "agent-tool-calls.jsonl"
    if lines is not None:
        trace.write_text("".join((json.dumps(line) if isinstance(line, dict) else line) + "\n" for line in lines))

    def read_table(runtime, **_):
        if table_fails:
            raise cr.ToolCaptureFailed("tool_trace table unreadable")
        return tuple(dataclasses.replace(call) for call in (table or []))

    monkeypatch.setattr(cr, "_collect_tool_trace_calls", read_table)
    monkeypatch.setattr(cr, "_collect_http_tool_calls", lambda runtime, **_: tuple(read_table(runtime)))
    monkeypatch.setattr(cr, "_lend_span", lend)
    _job_obj, context = _context(tmp_path=folder, evidence_seam=seam)
    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=None)
    try:
        calls = runner._collect_calls(_runtime(metadata={"tool_trace_path": str(trace)}))
    except cr.ToolCaptureFailed as failed:
        return ("failed", str(failed), [_unnamed(call) for call in failed.calls])
    return (
        [_unnamed(call) for call in calls],
        [call.name for call in graded_calls(calls)],
    )


def _unnamed(call: Call) -> dict:
    return {key: value for key, value in dataclasses.asdict(call).items() if key not in _NAMED}


_VOICE_CASES = {
    "file with graded calls": dict(lines=_SPOKEN_LINES, seam=EvidenceSeam.TOOL_TRACE, table=_table_rows()),
    "file with graded calls and agreeing spans": dict(
        lines=(*_SPOKEN_LINES, *_AGREEING_SPANS), seam=EvidenceSeam.TOOL_TRACE, table=_table_rows()
    ),
    "file with graded calls and spans that recorded nothing": dict(
        lines=(*_SPOKEN_LINES, *(
            {**span, "arguments": "", "output": ""} for span in _AGREEING_SPANS
        )), seam=EvidenceSeam.TOOL_TRACE,
    ),
    "the same tool twice with the same arguments": dict(
        lines=(_SPOKEN_LINES[0], {**_SPOKEN_LINES[0], "at": 3.0}), seam=EvidenceSeam.TOOL_TRACE,
    ),
    "no file, the table's rows": dict(lines=None, seam=EvidenceSeam.TOOL_TRACE, table=_table_rows()),
    "no file, the table's rows and spans": dict(
        lines=_AGREEING_SPANS, seam=EvidenceSeam.TOOL_TRACE, table=_table_rows()
    ),
    "neither": dict(lines=None, seam=EvidenceSeam.TOOL_TRACE),
    "an unreadable file": dict(lines=("{cut", "[]"), seam=EvidenceSeam.TOOL_TRACE, table=_table_rows()),
    "an unreadable table": dict(lines=None, seam=EvidenceSeam.TOOL_TRACE, table_fails=True),
    "an unreadable table under a file with graded calls": dict(
        lines=_SPOKEN_LINES, seam=EvidenceSeam.TOOL_TRACE, table_fails=True
    ),
    "hand-offs alone": dict(lines=_HANDOFF_ONLY, seam=EvidenceSeam.TOOL_TRACE, table=_table_rows()),
    "the http_tool seam": dict(lines=None, seam=EvidenceSeam.HTTP_TOOL, table=_table_rows()),
    "the http_tool seam with nothing": dict(lines=None, seam=EvidenceSeam.HTTP_TOOL),
    "the hook's line timing the agent's own line": dict(
        lines=(
            {"name": "look_up_order", "arguments": {"order_id": "A1001"}, "output": "shipped"},
            {"name": "look_up_order", "arguments": {"order_id": "A1001"}, "output": "shipped",
             "source": "livekit", "at": 4.0},
        ),
        seam=EvidenceSeam.TOOL_TRACE,
    ),
}


@pytest.mark.parametrize("case", list(_VOICE_CASES), ids=lambda case: case.replace(" ", "-"))
def test_a_voice_trial_reads_its_calls_as_before_sources_were_named(tmp_path, monkeypatch, case) -> None:
    before = _voice_reading(tmp_path, monkeypatch, lend=_lend_span_as_it_was, **_VOICE_CASES[case])
    now = _voice_reading(tmp_path, monkeypatch, lend=_lend_span_now, **_VOICE_CASES[case])

    assert now == before


def test_every_voice_record_names_its_source(tmp_path, monkeypatch) -> None:
    trace = _trace_lines(tmp_path / "agent-tool-calls.jsonl", *_SPOKEN_LINES)
    monkeypatch.setattr(cr, "_collect_tool_trace_calls", lambda runtime, **_: tuple(_table_rows()))
    _job_obj, context = _context(tmp_path=tmp_path, evidence_seam=EvidenceSeam.TOOL_TRACE)
    runner = cr.CallRunnerImpl(FakeAdapter(), context, place_call=None)

    from_file = runner._collect_calls(_runtime(metadata={"tool_trace_path": str(trace)}))
    trace.unlink()
    from_table = runner._collect_calls(_runtime(metadata={"tool_trace_path": str(trace)}))

    assert {call.source for call in from_file} == {"tracer"}
    assert {call.source for call in from_table} == {"world"}


_lend_span_now = cr._lend_span
