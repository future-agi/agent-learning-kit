from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

from voice_cases import CASES, build_inputs, missing_env

from fi.alk import simulate
from fi.simulate.evaluation import evaluate_agent_report
from fi.simulate.runtime import new_run_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one voice acceptance matrix cell")
    parser.add_argument("case_id", choices=sorted(CASES))
    parser.add_argument("--output-root", default="artifacts/simulation-acceptance")
    parser.add_argument("--dry-run", action="store_true")
    # A generated scenario brings its own caller, its own mocked tools and its own checks.
    # Supplying one turns this into a graded test rather than a transport check.
    parser.add_argument(
        "--scenario",
        default=os.environ.get("ALK_SCENARIO", ""),
        help="path to a generated scenario; drives the caller, the mocks and the checks",
    )
    parser.add_argument(
        "--agent",
        default=os.environ.get("ALK_AGENT", "drive_thru"),
        help="registered agent whose assistant serves the scenario's tools",
    )
    parser.add_argument(
        "--no-mock-tools",
        action="store_true",
        help="do not serve the scenario's tools; the agent's own tools answer instead",
    )
    parser.add_argument(
        "--no-grade", action="store_true", help="skip the scenario's checkpoints"
    )
    parser.add_argument(
        "--no-trace", action="store_true", help="skip writing the run trace"
    )
    args = parser.parse_args()
    if args.scenario:
        os.environ["ALK_SCENARIO"] = args.scenario

    case = CASES[args.case_id]
    missing = missing_env(case)
    if missing:
        print(
            json.dumps(
                {
                    "case_id": case.case_id,
                    "description": case.description,
                    "status": "missing_setup",
                    "missing_env": missing,
                    "setup": case.setup,
                },
                indent=2,
            )
        )
        return 2

    run_id = new_run_id()
    inputs = build_inputs(case.case_id, run_id)
    output_dir = Path(args.output_root).expanduser().resolve() / run_id / case.case_id
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = simulate.build_voice_run_manifest(
        name=f"acceptance-{case.case_id}",
        agent_definition=inputs.agent_definition,
        livekit_runtime=inputs.livekit_runtime,
        scenario=inputs.scenario,
        simulator=inputs.simulator,
        required_env=case.required_env,
        simulation_run_id=run_id,
        record_audio=True,
        recording_root=output_dir / "recordings",
        recording_case_directory=output_dir / "recordings",
        min_turn_messages=6,
        max_seconds=inputs.max_seconds,
        connect_timeout=60,
        readiness_timeout=120,
        cleanup_timeout=30,
        conversation_direction=inputs.conversation_direction,
        agent_first_silence_timeout_seconds=30,
    )
    manifest_path = simulate.write_manifest_file(
        manifest,
        output_dir / "manifest.json",
    )
    if args.dry_run:
        print(
            json.dumps(
                {
                    "case_id": case.case_id,
                    "description": case.description,
                    "known_status": case.status,
                    "status": "dry_run_passed",
                    "manifest": str(manifest_path),
                    "setup": case.setup,
                },
                indent=2,
            )
        )
        return 0

    record = None
    if args.scenario:
        with open(args.scenario, encoding="utf-8") as fh:
            record = json.load(fh)

    trigger = _start_livekit_outbound_trigger(case.case_id)
    tools = contextlib.nullcontext(None)
    if record is not None and not args.no_mock_tools:
        from fi.alk.generation.live_run import tool_session

        tools = tool_session(record, agent=args.agent)
    try:
        with tools as tool_state:
            report = asyncio.run(
                simulate.run_voice_simulation(
                    agent_definition=inputs.agent_definition,
                    livekit_runtime=inputs.livekit_runtime,
                    scenario=inputs.scenario,
                    simulator=inputs.simulator,
                    simulation_run_id=run_id,
                    record_audio=True,
                    recording_root=output_dir / "recordings",
                    recording_case_directory=output_dir / "recordings",
                    min_turn_messages=6,
                    max_seconds=inputs.max_seconds,
                    connect_timeout=60,
                    readiness_timeout=120,
                    cleanup_timeout=30,
                    conversation_direction=inputs.conversation_direction,
                    agent_first_silence_timeout_seconds=30,
                )
            )
            evaluation = evaluate_agent_report(report, attach=True)
            recorded_calls = tool_state.calls() if tool_state is not None else []
            final_state = tool_state.final_state if tool_state is not None else {}
    finally:
        _finish_livekit_outbound_trigger(trigger)
    report_path = output_dir / "report.json"
    report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    result = report.results[0]

    grading = None
    trace_path = None
    if record is not None:
        messages = [
            m if isinstance(m, dict) else m.model_dump()
            for m in (result.messages or [])
        ]
        # Provider evidence keeps only a count, so the mock server's record is the tool truth.
        calls = recorded_calls or _provider_tool_calls(result)
        if not args.no_grade:
            from fi.alk.generation.live_run import grade

            grading = grade(
                record,
                messages=messages,
                tool_calls=calls,
                final_state=final_state,
            )
            (output_dir / "checks.json").write_text(
                json.dumps(grading, indent=2), encoding="utf-8"
            )
        if not args.no_trace:
            from fi.alk.generation.live_run import write_trace

            trace_path = write_trace(
                str(output_dir),
                record=record,
                messages=messages,
                tool_calls=calls,
                final_state=final_state,
                grading=grading,
                metadata={
                    "case_id": case.case_id,
                    "run_id": run_id,
                    "stop_reason": result.metadata.get("stop_reason"),
                    "status": result.metadata.get("status"),
                    "message_count": len(messages),
                },
            )
    status = str(result.metadata.get("status") or "unknown")
    print(
        json.dumps(
            {
                "case_id": case.case_id,
                "description": case.description,
                "known_status": case.status,
                "status": status,
                "failure": result.metadata.get("failure"),
                "evaluation_passed": evaluation.passed,
                "evaluation_score": evaluation.score,
                **(
                    {
                        "scenario": grading.get("scenario_id"),
                        "checks_passed": grading.get("passed"),
                        "checks_failed": grading.get("failed"),
                        "checks_skipped": grading.get("skipped"),
                        "scenario_verdict": grading.get("verdict"),
                    }
                    if grading
                    else {}
                ),
                **({"trace": trace_path} if trace_path else {}),
                "manifest": str(manifest_path),
                "report": str(report_path),
            },
            indent=2,
        )
    )
    if grading and grading.get("verdict") == "fail":
        return 1
    return _result_exit_code(
        status=status,
        evaluation_passed=evaluation.passed,
    )


def _provider_tool_calls(result) -> list[dict]:
    raw = getattr(result, "tool_calls", None) or []
    return [r if isinstance(r, dict) else r.model_dump() for r in raw]


def _result_exit_code(*, status: str, evaluation_passed: bool) -> int:
    return 0 if status == "completed" and evaluation_passed else 1


def _start_livekit_outbound_trigger(case_id: str) -> subprocess.Popen | None:
    if case_id != "1.2.1":
        return None
    return subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("trigger_livekit_outbound.py"))]
    )


def _finish_livekit_outbound_trigger(process: subprocess.Popen | None) -> None:
    if process is None:
        return
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
