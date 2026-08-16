"""The tools that run a scenario against the real agent, and record what happened.

Placing a call was a command before this existed, which made the last stage the only one you
could not simply ask for. Nothing about it needed to be a command: wiring the world to the
assistant and grading afterwards is already code, and choosing which scenario to run and reading
what came back is the part worth having judgement on.

So the same shape as every other stage. The tools do what must be exact — restore the world,
repoint the assistant's own tools, place the call through ALK, run the checks — and the stage
decides what to run and says what it means.

A run takes minutes, not seconds. The tool blocks for that long, and says so, because a stage
that fires a call and returns immediately would report on a conversation that has not happened.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from ..environment import load_catalogue
from ..scenario_tools import load_scenarios
from ..tools import schema
from .call import place_the_call
from .live import LiveRun, grade, wire

RUN_SERVER = "runs"
RESULTS = "runs.json"

# What a hosted agent needs before a call can be placed at all. Checked up front rather than
# three minutes in, because the failure otherwise arrives after the expensive part.
REQUIRED = ("VAPI_API_KEY", "VAPI_ASSISTANT_ID")


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def missing_prerequisites() -> list[str]:
    """What would stop a live call, in the words of what to do about it."""
    problems: list[str] = []
    absent = [name for name in REQUIRED if not os.environ.get(name)]
    if absent:
        problems.append(
            f"{', '.join(absent)} not set. The assistant already exists with the agent's own "
            "tools; without these there is no way to reach it. Load the env file first:\n"
            "    set -a; . ./.env.acceptance; set +a"
        )
    if not os.environ.get("HARNESS_WEBHOOK_URL") and not shutil.which("cloudflared"):
        problems.append(
            "no way to expose the webhook publicly. A hosted agent cannot reach loopback, so "
            "either install cloudflared (brew install cloudflared) or set HARNESS_WEBHOOK_URL "
            "to a tunnel that is already running."
        )
    return problems


def save_results(results: list[dict[str, Any]], destination: Path) -> Path:
    """Keep every run, so a suite can be read after the fact rather than scrolled back to."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / RESULTS
    path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_results(destination: Path) -> list[dict[str, Any]]:
    path = Path(destination) / RESULTS
    if not path.exists():
        return []
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, list) else []
    except json.JSONDecodeError:
        return []


def as_record(run: LiveRun) -> dict[str, Any]:
    return {
        "scenario": run.scenario,
        "passed": bool(run.settled) and run.met == len(run.settled) and not run.problems,
        "met": run.met,
        "of": len(run.settled),
        "settled": [
            {"name": one.name, "held": one.held, "said": one.said, "broken": one.broken}
            for one in run.settled
        ],
        "judged": list(run.judged),
        "calls": list(run.calls),
        "problems": list(run.problems),
    }


def transcript_since(started: float) -> str:
    """What was said on the call that just happened, from the voice runner's own report.

    The voice case owns the call and writes its report where it always has; reaching into that
    report is how the transcript gets onto the run record without the harness re-implementing
    any of the call. Only a report written after this run started counts — the newest file on
    disk is otherwise last week's call wearing today's verdict.
    """
    root = Path("artifacts/simulation-acceptance")
    if not root.exists():
        return ""
    newest: tuple[float, Path] | None = None
    for report in root.glob("run_*/*/report.json"):
        written = report.stat().st_mtime
        if written >= started and (newest is None or written > newest[0]):
            newest = (written, report)
    if newest is None:
        return ""
    try:
        loaded = json.loads(newest[1].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    for result in loaded.get("results") or []:
        spoken = result.get("transcript")
        if isinstance(spoken, str) and spoken.strip():
            return spoken
    return ""


def report(run: LiveRun) -> str:
    """One run, as something worth reading rather than a score."""
    lines = [run.line()]
    lines += [one.line() for one in run.settled]
    lines += [f"  [?] {name} — judged, not settled by code" for name in run.judged]
    if run.problems:
        lines += [f"  !!  {problem}" for problem in run.problems]
    lines.append("")
    lines.append("what the agent actually did:")
    lines += [f"  {call}" for call in run.calls or ["(no tool calls reached the world)"]]
    return "\n".join(lines)


def run_tools(world_root: Path, destination: Path, *, case: str = "") -> Any:
    """A server for running one agent's scenarios against the real thing."""
    written = load_scenarios(destination)
    catalogue = load_catalogue(destination)
    results = load_results(destination)
    voice_case = case or os.environ.get("HARNESS_VOICE_CASE", "2.1.2")

    @tool(
        "list_scenarios",
        "The scenarios that can be run, what each one tests, and which of its sub-goals are "
        "settled by code rather than left to a judge.",
        schema({}, []),
    )
    async def list_scenarios(_args: dict[str, Any]) -> dict[str, Any]:
        if not written:
            return _err("no scenarios have been written for this agent yet")
        lines: list[str] = []
        for one in written:
            settled = [
                name
                for name in one.sub_goals
                if (found := catalogue.named(name)) and found.deterministic()
            ]
            judged = [name for name in one.sub_goals if name not in settled]
            ran = next((r for r in results if r["scenario"] == one.name), None)
            mark = "" if ran is None else ("  [last run: PASS]" if ran["passed"] else "  [last run: FAIL]")
            lines.append(
                f"{one.name}{mark}\n  tests: {one.tests or one.use_case or '—'}\n"
                f"  settled by code: {', '.join(settled) or 'none'}\n"
                f"  judged: {', '.join(judged) or 'none'}"
            )
        return _ok("\n".join(lines))

    @tool(
        "preflight",
        "Check everything a live call needs before spending one: the assistant's credentials "
        "and a way to expose the webhook publicly. Run this before the first call.",
        schema({}, []),
    )
    async def preflight(_args: dict[str, Any]) -> dict[str, Any]:
        problems = missing_prerequisites()
        if problems:
            return _err("Not ready:\n  - " + "\n  - ".join(problems))
        return _ok(
            "Ready. Credentials are set and the webhook can be exposed. "
            f"{len(written)} scenarios are available."
        )

    @tool(
        "run_scenario",
        "Run one scenario against the real agent and grade it.\n\n"
        "This restores the world, applies the scenario's setup, stands up the webhook, points "
        "the assistant's OWN tools at it, places the call, and runs the sub-goals' checks "
        "against what the world holds afterwards plus the calls that were made.\n\n"
        "It takes several minutes and blocks until the call is over. Run one at a time and read "
        "what comes back before running the next.",
        # Both spellings accepted: every model that has driven this stage has guessed
        # `scenario` at least once, and a retry on an argument name is a wasted turn.
        schema({"name": str, "scenario": str}, []),
    )
    async def run_scenario(args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("name") or args.get("scenario") or "")
        scenario = next((one for one in written if one.name == name), None)
        if scenario is None:
            return _err(
                f"no scenario called {name!r}. There is: "
                + ", ".join(one.name for one in written)
            )
        problems = missing_prerequisites()
        if problems:
            return _err(
                "Cannot place a call:\n  - "
                + "\n  - ".join(problems)
                + "\nThis is the environment this harness is running in, not something to fix "
                "in the scenario."
            )

        def placed() -> tuple[LiveRun, str, list[str], str]:
            """The whole call, off the event loop.

            Wiring reads a subprocess's stdout and placing the call blocks for minutes; run
            inline they freeze whatever loop is hosting this tool, which for the web UI means
            the stream, the status endpoint and the stop button all die for the duration.
            """
            world, instruction, webhook, tunnel, url, moved = wire(scenario, world_root)
            started = time.time()
            try:
                # The caller's instruction reaches the voice case through the environment, so
                # how a simulated caller behaves is not decided in two places.
                os.environ["HARNESS_INSTRUCTION"] = instruction
                os.environ["HARNESS_SCENARIO"] = scenario.name
                os.environ["HARNESS_OUTCOME"] = scenario.tests
                code = place_the_call(voice_case)
                run = grade(scenario, world, world_root)
                if code != 0 and not run.calls:
                    run.problems.append(
                        f"the voice runner exited {code} and no tool call reached the world, "
                        "so this says nothing about the agent"
                    )
            finally:
                webhook.stop()
                if tunnel is not None:
                    tunnel.terminate()
                world.close()
            return run, url, moved, transcript_since(started)

        run, url, moved, spoken = await asyncio.to_thread(placed)

        record = as_record(run)
        record["instruction"] = scenario.instruction
        record["transcript"] = spoken
        # Re-read before writing: the local suite writes the same file, and a list loaded when
        # this stage opened would silently roll back anything recorded since.
        results[:] = [
            r for r in load_results(destination) if r.get("scenario") != scenario.name
        ]
        results.append(record)
        save_results(results, destination)
        answer = f"webhook: {url}/tool\nrepointed: {', '.join(moved)}\n\n{report(run)}"
        return _ok(answer) if not run.problems else _err(answer)

    @tool(
        "read_results",
        "What every scenario did the last time it was run, without running anything.",
        schema({}, []),
    )
    async def read_results(_args: dict[str, Any]) -> dict[str, Any]:
        if not results:
            return _ok("nothing has been run yet")
        lines = []
        for record in results:
            mark = "PASS" if record["passed"] else "FAIL"
            failed = [
                f"{one['name']}: {one['said']}"
                for one in record["settled"]
                if not one["held"]
            ]
            lines.append(
                f"{mark}  {record['scenario']}  {record['met']}/{record['of']}"
                + ("\n  - " + "\n  - ".join(failed) if failed else "")
            )
        passed = sum(1 for record in results if record["passed"])
        return _ok("\n".join(lines) + f"\n\n{passed} of {len(results)} passed")

    server = create_sdk_mcp_server(
        name=RUN_SERVER,
        version="0.1.0",
        tools=[list_scenarios, preflight, run_scenario, read_results],
    )
    return server


TOOL_NAMES = (
    "list_scenarios",
    "preflight",
    "run_scenario",
    "read_results",
)
