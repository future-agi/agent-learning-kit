"""Run a stage from a terminal.

This is one renderer over the stage loop, not the product. It prints events as lines and reads
follow-ups from stdin; a browser front end subscribes to the same events and draws them as a
transcript beside the artifact. Keeping the terminal a renderer rather than the interface is what
makes the second one cheap.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any

from .build import open_stage as build_stage
from .build import opening as build_opening
from .build import require_buildable
from .chat import open_conversation
from .config import (
    DEFAULT_MODEL,
    artifact_dir,
    chosen_model,
    credentials_hint,
    permission_gate,
)
from .run.targets import supported as target_kinds
from .scenarios import load as load_written
from .scenarios import open_stage as scenario_stage
from .scenarios import opening as scenario_opening
from .session import TEXT, Event
from .sessions import Session, new_id, save as save_session
from .sources import resolve, supported
from .understand import load, open_stage, opening
from .world.snapshot import saved as world_saved


def _source_root(destination: Path, explicit: str = "") -> str:
    """Recover the source path for commands resumed from a session folder."""
    if explicit.strip():
        return str(Path(explicit).expanduser().resolve())
    metadata = destination / "session.json"
    if metadata.exists():
        try:
            import json

            return str(
                json.loads(metadata.read_text(encoding="utf-8")).get("source") or ""
            )
        except (OSError, ValueError):
            pass
    return ""


def _render(event: Event) -> None:
    line = event.line()
    if event.kind == TEXT:
        print(line, end="", flush=True)
    else:
        print(f"\n{line}", flush=True)


async def _prompt(question: str) -> str:
    return (await asyncio.to_thread(input, question)).strip()


async def _ask_operator(_tool_name: str, payload: dict[str, Any], _context: Any) -> Any:
    """Render the model's clarifying questions and return the operator's answers."""
    from claude_agent_sdk.types import PermissionResultAllow

    answers: dict[str, Any] = {}
    for question in payload.get("questions", []):
        print(f"\n\n  {question.get('header', '?')}: {question.get('question', '')}")
        options = question.get("options", []) or []
        for index, option in enumerate(options, start=1):
            print(
                f"    {index}. {option.get('label')} - {option.get('description', '')}"
            )
        raw = await _prompt("  > ")
        chosen = raw
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            chosen = options[int(raw) - 1].get("label", raw)
        answers[question.get("question", "")] = chosen
    print()
    return PermissionResultAllow(
        updated_input={"questions": payload.get("questions", []), "answers": answers}
    )


async def _understand(args: argparse.Namespace) -> int:
    source = resolve(args.kind, name=args.name, root=args.path)
    stage, destination = open_stage(
        source,
        out=Path(args.out) if args.out else None,
        # Unattended, there is nobody to answer, so the model records what it could not
        # resolve in open_questions rather than blocking on a prompt nobody will see.
        ask=permission_gate(_ask_operator) if args.interactive else None,
    )

    print(f"agent: {source.name}  ({source.kind})")
    print(f"model: {chosen_model()}")
    print(f"out:   {destination}\n")

    await _converse(
        stage,
        opening(source),
        interactive=args.interactive,
        until=lambda: load(destination) is not None,
        nudge=(
            "Nothing was saved: you finished without calling submit_contract. Call it now "
            "with the contract you worked out."
        ),
    )

    contract = load(destination)
    if contract is None:
        print("\nNo contract was submitted.", file=sys.stderr)
        return 1
    print(
        f"\ncontract: {len(contract.tools)} tools, "
        f"{len(contract.hard_constraints)} rules, "
        f"{len(contract.real_use_cases)} use cases, "
        f"{len(contract.open_questions)} open questions"
    )
    print(f"spent:    ${stage.spent_usd:.4f}")
    return 0


async def _converse(
    stage,
    opening_message: str,
    *,
    interactive: bool,
    until=None,
    nudge: str = "",
) -> None:
    """Say the opening, then keep the stage open for corrections.

    The same shape for every stage. A world is usually right on the second look, and the point
    of holding the session open is that correcting it is the next thing said rather than a
    rebuild from nothing.

    ``until``/``nudge`` guard the unattended case. The commonest way an unattended stage fails
    is finishing all the work and never calling the tool that saves it — the whole contract
    written out as prose, submitted to nobody. One mechanical reminder costs a turn; rerunning
    the stage costs everything it just did.
    """
    async with stage:
        await stage.say(opening_message, on_event=_render)
        if not interactive and until is not None and nudge and not until():
            await stage.say(nudge, on_event=_render)
        while interactive:
            try:
                said = await _prompt("\nyou  ")
            except (EOFError, KeyboardInterrupt):
                break
            if not said or said in {"q", "quit", "exit"}:
                break
            await stage.say(said, on_event=_render)


async def _build(args: argparse.Namespace) -> int:
    destination = Path(args.out) if args.out else artifact_dir(args.name)
    contract = load(destination)
    if contract is None:
        print(f"No contract at {destination}. Run `understand` first.", file=sys.stderr)
        return 1

    print(f"agent: {contract.agent}  ({len(contract.tools)} tools)")
    print(f"model: {chosen_model()}")
    print(f"out:   {destination}\n")

    source_root = _source_root(destination, args.path or "")
    try:
        require_buildable(contract, source_root)
    except RuntimeError as failed:
        print(str(failed), file=sys.stderr)
        return 1

    from .provision import ProvisionError, provision_if_present

    try:
        environment = await asyncio.to_thread(
            provision_if_present, source_root, destination, contract
        )
    except ProvisionError as failed:
        print(f"Cannot create the source environment: {failed}", file=sys.stderr)
        return 1
    if environment is not None:
        print(f"environment: {environment.project} ({', '.join(environment.services)})")
        for name, value in sorted(environment.overrides.items()):
            print(f"override:    {name}={value}")

    stage, _ = build_stage(
        contract,
        out=destination,
        ask=permission_gate(_ask_operator) if args.interactive else None,
        source_root=source_root,
    )
    await _converse(
        stage,
        build_opening(contract, provisioned=environment is not None),
        interactive=args.interactive,
        until=lambda: world_saved(destination),
        nudge=(
            "Nothing was saved: you finished without calling save_world. Call check_world, "
            "fix what it names, then save_world."
        ),
    )

    if not world_saved(destination):
        print("\nNo world was saved.", file=sys.stderr)
        return 1
    # Seal the exact environment now that its generated world exists. Local and hosted
    # execution consume this same internal bundle; the source repository is never a special
    # runtime path after this boundary.
    from .bundle import BundleError, export_session_bundle

    try:
        bundle_path, bundle = await asyncio.to_thread(
            export_session_bundle,
            source_root,
            destination,
            name=f"{contract.agent}-environment",
        )
    except BundleError as failed:
        print(f"Cannot seal the environment bundle: {failed}", file=sys.stderr)
        return 1
    print(f"\nworld: {destination}")
    print(f"bundle: {bundle_path} ({bundle.digest})")
    print(f"spent: ${stage.spent_usd:.4f}")
    return 0


async def _environment(args: argparse.Namespace) -> int:
    """Provision or tear down the runtime shipped by the agent repository."""
    from .provision import (
        ProvisionedEnvironment,
        ProvisionError,
        provision,
        reset,
        stop,
    )

    destination = Path(args.out)
    try:
        if args.action == "down":
            if not stop(destination):
                print(f"No environment recorded at {destination}.", file=sys.stderr)
                return 1
            print(f"environment stopped: {destination}")
            return 0
        if args.action == "status":
            environment = ProvisionedEnvironment.load(destination)
            if environment is None:
                print(f"No environment recorded at {destination}.", file=sys.stderr)
                return 1
        elif args.action == "reset":
            environment = reset(destination)
        else:
            environment = provision(args.path, destination)
    except ProvisionError as failed:
        print(f"Environment failed: {failed}", file=sys.stderr)
        return 1

    print(f"environment: {environment.project}")
    print(f"services:    {', '.join(environment.services)}")
    print(f"ready in:    {environment.provision_seconds:.3f}s")
    for name, value in sorted(environment.overrides.items()):
        print(f"set:         {name}={value}")
    return 0


async def _scenarios(args: argparse.Namespace) -> int:
    destination = Path(args.out) if args.out else artifact_dir(args.name)
    contract = load(destination)
    if contract is None:
        print(f"No contract at {destination}. Run `understand` first.", file=sys.stderr)
        return 1
    if not world_saved(destination):
        print(f"No world at {destination}. Run `build` first.", file=sys.stderr)
        return 1

    # With a suite already written, the target is what is there. Somebody who comes back to
    # change one scenario is not asking for a different number of them.
    existing = len(load_written(destination))
    wanted = args.count or existing or 10

    print(
        f"agent: {contract.agent}  "
        + (f"({existing} scenarios, loaded)" if existing else f"(writing {wanted})")
    )
    print(f"model: {chosen_model()}")
    print(f"out:   {destination}\n")

    stage, _ = scenario_stage(
        contract,
        out=destination,
        wanted=wanted,
        ask=permission_gate(_ask_operator) if args.interactive else None,
    )
    await _converse(
        stage,
        scenario_opening(contract, wanted, existing),
        interactive=args.interactive,
        until=lambda: bool(load_written(destination)),
        nudge=(
            "Nothing was saved: you finished without calling save_scenarios. Submit anything "
            "still unsubmitted, then call save_scenarios."
        ),
    )

    written = load_written(destination)
    if not written:
        print("\nNo scenarios were saved.", file=sys.stderr)
        return 1
    print(f"\nscenarios: {len(written)} in {destination / 'scenarios.json'}")
    print(f"spent:     ${stage.spent_usd:.4f}")
    return 0


async def _live(args: argparse.Namespace) -> int:
    """The run stage as a conversation: it decides what to run and reads what came back."""
    from .run.stage import load as load_results
    from .run.stage import open_stage as run_stage
    from .run.stage import opening as run_opening

    destination = Path(args.out) if args.out else artifact_dir(args.name)
    contract = load(destination)
    written = load_written(destination)
    if contract is None or not written:
        print(
            f"Need a contract and scenarios at {destination}. Run `understand`, `build` and "
            "`scenarios` first.",
            file=sys.stderr,
        )
        return 1
    source_root = _source_root(destination)
    if source_root:
        _load_connection_env(Path(source_root))

    print(f"agent: {contract.agent}  ({len(written)} scenarios)")
    print(f"model: {chosen_model()}")
    print(f"out:   {destination}\n")

    stage, _ = run_stage(
        contract,
        out=destination,
        ask=permission_gate(_ask_operator) if args.interactive else None,
    )
    await _converse(
        stage, run_opening(contract, destination), interactive=args.interactive
    )

    results = load_results(destination)
    passed = sum(1 for record in results if record["passed"])
    print(f"\nruns:  {passed} of {len(results)} passed, in {destination / 'runs.json'}")
    print(f"spent: ${stage.spent_usd:.4f}")
    return 0


async def _run(args: argparse.Namespace) -> int:
    from .run import run_suite
    from .run.grade import summarise
    from .world.snapshot import require_source_implementation

    destination = Path(args.out) if args.out else artifact_dir(args.name)
    contract = load(destination)
    written = load_written(destination)
    if contract is None or not written:
        print(
            f"Need a contract and scenarios at {destination}. Run `understand`, `build` "
            "and `scenarios` first.",
            file=sys.stderr,
        )
        return 1
    try:
        require_source_implementation(destination)
    except (FileNotFoundError, RuntimeError) as failed:
        print(str(failed), file=sys.stderr)
        return 1

    chosen = [s for s in written if s.name in args.only] if args.only else written
    if not chosen:
        print(f"No scenario matching {args.only}.", file=sys.stderr)
        return 1

    print(f"agent: {contract.agent}  ({len(chosen)} scenarios, target {args.target})")
    print(f"model: {chosen_model()}")
    print(f"out:   {destination}\n")

    def overheard(exchange: Any) -> None:
        if args.quiet:
            return
        print(f"    {exchange.speaker:8} {exchange.text}", flush=True)

    def show(result: Any) -> None:
        # Just the verdict as it lands. The detail is in the summary at the end, and printing
        # it in both places means every failure is read twice.
        print(result.line(), flush=True)

    results = await run_suite(
        chosen,
        contract,
        destination,
        target=args.target,
        model=args.model,
        on_result=show,
        on_exchange=overheard,
    )
    print("\n" + summarise(results))
    print(f"\nspent: ${sum(result.spent_usd for result in results):.4f}")
    return 0 if all(result.passed for result in results) else 2


async def _simulate(args: argparse.Namespace) -> int:
    """Run the suite through the modality/runtime inferred from the saved contract."""
    from . import platform
    from .run.simulation import simulate
    from .world.snapshot import require_source_implementation

    destination = Path(args.out) if args.out else artifact_dir(args.name)
    contract = load(destination)
    written = load_written(destination)
    if contract is None or not written:
        print(
            f"Need a contract and scenarios at {destination}.",
            file=sys.stderr,
        )
        return 1
    try:
        require_source_implementation(destination)
    except (FileNotFoundError, RuntimeError) as failed:
        print(str(failed), file=sys.stderr)
        return 1
    chosen = [s for s in written if s.name in args.only] if args.only else written
    if not chosen:
        print(f"No scenario matching {args.only}.", file=sys.stderr)
        return 1

    # A resumed simulation is a first-class execution path, not merely an internal stage of
    # ``auto``. Load the submitted repository's connection settings here as well so restarting a
    # completed build does not require the operator to rediscover and export its LiveKit/model
    # credentials by hand. Existing worker/host values continue to win in _load_connection_env.
    source_root = _source_root(destination)
    if source_root:
        _load_connection_env(Path(source_root))

    reported = None
    call_ids: dict[str, str] = {}
    blocked = platform.configured()
    if not blocked:
        try:
            reported, allocated = platform.begin(
                chosen,
                name=destination.name,
                run_test_id=platform.remembered(destination),
                modality=contract.modality or "text",
            )
            call_ids = {
                scenario.name: call_id
                for scenario, call_id in zip(chosen, allocated, strict=False)
            }
            # Persist the destination as soon as the platform execution exists. The list view
            # can now show an in-progress run, and a process restart reuses the same RunTest.
            platform.remember(destination, reported)
            print(f"platform run: {reported.url}", flush=True)
        except platform.PlatformError as failed:
            print(f"platform reporting could not start: {failed}", file=sys.stderr)
    else:
        print(f"not reported to the platform: {blocked}", flush=True)

    def show(result: Any) -> None:
        print(result.line(), flush=True)
        if reported is None:
            return
        call_id = call_ids.get(result.scenario)
        if not call_id:
            reported.problems.append(
                f"the platform allocated no call for {result.scenario}"
            )
            return
        platform.send_result(reported, call_id, result)

    summary = await simulate(
        chosen,
        contract,
        destination,
        destination=destination,
        model=args.model,
        on_case_done=show,
    )
    print(
        f"\n{summary['passed']}/{summary['scenarios']} scenarios passed "
        f"in {summary['seconds']}s"
    )
    print(f"run: {destination / 'runs' / summary['run_id']}")
    if reported is not None:
        for problem in reported.problems:
            print(f"platform reporting problem: {problem}", file=sys.stderr)
        print(f"reported to the platform: {reported.url}")
    return 0


def _load_connection_env(source: Path) -> list[str]:
    """Fill missing connection variables from the submitted repository.

    A dotenv file is data, not a shell program.  Sourcing a customer's file executes command
    substitutions and also breaks on perfectly valid unquoted values containing spaces.  Values
    already supplied by the workspace win: in particular, a worker's container-only credential
    path must never replace the host's model-provider credential path.
    """
    loaded: list[str] = []
    for candidate in (source / ".env.local", source / ".env"):
        if not candidate.is_file():
            continue
        for raw in candidate.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.removeprefix("export ").strip()
            if not name.replace("_", "").isalnum() or not name[0].isalpha():
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if name not in os.environ:
                os.environ[name] = value
                loaded.append(name)
    return loaded


async def _auto(args: argparse.Namespace) -> int:
    """Take one source connection through every stage without operator messages.

    This is the product acceptance path.  The individual stage commands remain useful while
    developing the harness, but a submitted agent must not depend on somebody knowing their
    order, nudging a model, or repairing an artifact between stages.
    """
    source = Path(args.path).expanduser().resolve()
    loaded_connection = _load_connection_env(source)
    name = (args.name or source.name).strip()
    destination = (
        Path(args.out).expanduser().resolve()
        if args.out
        else artifact_dir(new_id(name))
    )
    destination.mkdir(parents=True, exist_ok=False)
    from fi.simulate.runtime.events import CanonicalEvent

    from .events import BufferedEventSink, EventOutbox
    from .job import (
        AgentConnection,
        ExecutionMode,
        HarnessJob,
        RepositorySource,
        SourceKind,
    )

    job = getattr(args, "job", None) or HarnessJob(
        job_id=destination.name,
        run_id=destination.name,
        execution=ExecutionMode.LOCAL,
        source=RepositorySource(
            kind=SourceKind.LOCAL_REPOSITORY, local_path=str(source)
        ),
        agent=AgentConnection(connector="auto"),
        scenario_count=args.count,
        metadata={"agent_name": name, "source_kind": args.kind},
    )
    (destination / "job.json").write_text(
        job.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    events = BufferedEventSink(EventOutbox(destination.parent, destination.name))
    event_sequence = 0

    def emit(event_type: str, stage: str, **payload: Any) -> None:
        nonlocal event_sequence
        events.write(
            CanonicalEvent.create(
                run_id=job.run_id,
                test_case_id="harness",
                event_type=event_type,
                source="fi.alk.harness",
                sequence=event_sequence,
                payload={"stage": stage, **payload},
            )
        )
        event_sequence += 1

    now = time.time()
    save_session(
        Session(
            id=destination.name,
            path=destination,
            agent=name,
            source=str(source),
            kind=args.kind,
            created=now,
            updated=now,
            stage="understand",
            title=name,
        )
    )

    print("automatic acceptance run")
    print(f"agent:  {name}")
    print(f"source: {source}")
    print(f"out:    {destination}")
    print("operator input: disabled\n")
    if loaded_connection:
        print(
            "connection: loaded missing variables from the submitted repository "
            f"({len(loaded_connection)} names; values hidden)\n"
        )

    stages = (
        (
            "understand",
            _understand,
            argparse.Namespace(
                name=name,
                path=str(source),
                kind=args.kind,
                out=str(destination),
                interactive=False,
                model=args.model,
            ),
        ),
        (
            "environment",
            _build,
            argparse.Namespace(
                name=name,
                path=str(source),
                out=str(destination),
                interactive=False,
            ),
        ),
        (
            "scenarios",
            _scenarios,
            argparse.Namespace(
                name=name,
                out=str(destination),
                count=args.count,
                interactive=False,
            ),
        ),
        (
            "calls",
            _simulate,
            argparse.Namespace(
                name=name,
                out=str(destination),
                only=None,
                model=args.run_model,
            ),
        ),
    )
    for label, operation, stage_args in stages:
        print(f"\n=== {label} ===", flush=True)
        emit("harness.stage.started", label)
        status = await operation(stage_args)
        # A completed call suite returns 2 when the submitted agent fails one or more checks.
        # That is a valid RL result. Earlier stages returning non-zero are harness failures.
        if status and label != "calls":
            emit("harness.stage.failed", label, status=status)
            print(f"\nautomatic run stopped: {label} failed", file=sys.stderr)
            return status
        if label == "calls" and status not in (0, 2):
            emit("harness.stage.failed", label, status=status)
            return status
        emit("harness.stage.completed", label, status=status)

    emit("harness.run.completed", "completed")
    print(f"\nautomatic run complete: {destination}")
    return 0


async def _chat(args: argparse.Namespace) -> int:
    """One conversation for the whole thing: point at an agent and keep talking."""
    conversation = open_conversation(
        name=args.name or "",
        path=args.path or "",
        kind=args.kind,
        out=Path(args.out) if args.out else None,
        ask=permission_gate(_ask_operator),
    )
    print(f"model:       {chosen_model()}")
    print(credentials_hint())
    print("\nSay what you want. Enter on its own moves to the next stage; 'q' ends.\n")

    await conversation.start(on_event=_render)
    while True:
        try:
            said = await _prompt(f"\nyou ({conversation.stage_name})  ")
        except (EOFError, KeyboardInterrupt):
            break
        if said in {"q", "quit", "exit"}:
            break
        if not said:
            entered = await conversation.advance(on_event=_render)
            if entered is None:
                print(
                    "\n  [nothing to move on to yet; this stage has not produced its artifact]"
                )
            continue
        await conversation.say(said, on_event=_render)
    await conversation.close()
    print(f"\nspent: ${conversation.spent_usd:.4f}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-harness", description=__doc__)
    # Talking to it is the way in, so that is what happens when you just start it.
    sub = parser.add_subparsers(dest="stage", required=False)

    understand = sub.add_parser(
        "understand", help="read an agent and produce its contract"
    )
    understand.add_argument("--name", required=True, help="what to call this agent")
    understand.add_argument("--path", required=True, help="where the agent is")
    understand.add_argument(
        "--kind", default="repo", choices=supported(), help="how the agent is supplied"
    )
    understand.add_argument("--out", default=None, help="artifact directory")
    understand.add_argument(
        "--once",
        dest="interactive",
        action="store_false",
        help="run unattended instead of staying open for corrections",
    )
    understand.add_argument("--model", default=DEFAULT_MODEL, help=argparse.SUPPRESS)
    understand.set_defaults(run=_understand, interactive=True)

    world = sub.add_parser("build", help="build the world from an agent's contract")
    world.add_argument("--name", required=True, help="which agent")
    world.add_argument("--out", default=None, help="artifact directory")
    world.add_argument(
        "--path",
        default=None,
        help="agent source path (normally recovered from the session automatically)",
    )
    world.add_argument(
        "--once",
        dest="interactive",
        action="store_false",
        help="run unattended instead of staying open for corrections",
    )
    world.set_defaults(run=_build, interactive=True)

    environment = sub.add_parser(
        "environment", help="start, inspect, or stop the runtime shipped by an agent"
    )
    environment.add_argument("action", choices=("up", "status", "reset", "down"))
    environment.add_argument(
        "--path", default="", help="agent repository (required for up)"
    )
    environment.add_argument("--out", required=True, help="session artifact directory")
    environment.set_defaults(run=_environment)

    scenarios = sub.add_parser(
        "scenarios", help="write the scenarios to test the agent with"
    )
    scenarios.add_argument("--name", required=True, help="which agent")
    scenarios.add_argument("--out", default=None, help="artifact directory")
    scenarios.add_argument(
        "--count",
        type=int,
        default=None,
        help="how many scenarios to write (defaults to however many already exist)",
    )
    scenarios.add_argument(
        "--once",
        dest="interactive",
        action="store_false",
        help="run unattended instead of staying open for corrections",
    )
    scenarios.set_defaults(run=_scenarios, interactive=True)

    live = sub.add_parser(
        "live", help="run the scenarios against the real agent, as a conversation"
    )
    live.add_argument("--name", required=True, help="which agent")
    live.add_argument("--out", default=None, help="artifact directory")
    live.add_argument(
        "--once",
        dest="interactive",
        action="store_false",
        help="run unattended instead of staying open",
    )
    live.set_defaults(run=_live, interactive=True)

    runs = sub.add_parser("run", help="run the scenarios and grade what happened")
    runs.add_argument("--name", required=True, help="which agent")
    runs.add_argument("--out", default=None, help="artifact directory")
    runs.add_argument(
        "--target",
        default="local",
        choices=target_kinds(),
        help="where the agent under test runs",
    )
    runs.add_argument(
        "--only", nargs="*", default=None, help="run only these scenarios, by name"
    )
    runs.add_argument("--model", default=None, help="model for the run")
    runs.add_argument(
        "--quiet",
        action="store_true",
        help="only the verdicts, without the conversations as they happen",
    )
    runs.set_defaults(run=_run)

    simulation = sub.add_parser(
        "simulate",
        help="run through the agent modality and shipped runtime inferred from its contract",
    )
    simulation.add_argument("--name", required=True, help="which agent")
    simulation.add_argument("--out", default=None, help="artifact directory")
    simulation.add_argument(
        "--only", nargs="*", default=None, help="run only these scenarios, by name"
    )
    simulation.add_argument("--model", default=None, help=argparse.SUPPRESS)
    simulation.set_defaults(run=_simulate)

    auto = sub.add_parser(
        "auto",
        help="from one agent source connection, build everything and run unattended",
    )
    auto.add_argument("--path", required=True, help="agent repository")
    auto.add_argument(
        "--name",
        default=None,
        help="agent name (defaults to the repository folder name)",
    )
    auto.add_argument(
        "--kind", default="repo", choices=supported(), help="how the agent is supplied"
    )
    auto.add_argument(
        "--out",
        default=None,
        help="fresh artifact directory (defaults to a unique session directory)",
    )
    auto.add_argument("--count", type=int, default=10, help="number of scenarios")
    auto.add_argument("--model", default=DEFAULT_MODEL, help=argparse.SUPPRESS)
    auto.add_argument("--run-model", default=None, help=argparse.SUPPRESS)
    auto.set_defaults(run=_auto)

    chat = sub.add_parser(
        "chat",
        help="one conversation: understand, build the world, write the scenarios",
    )
    # Nothing is required. Which agent, where it lives and how many scenarios are all things
    # you say; naming one here is a shortcut back into work already in progress.
    chat.add_argument("--name", default=None, help=argparse.SUPPRESS)
    chat.add_argument("--path", default=None, help=argparse.SUPPRESS)
    chat.add_argument(
        "--kind", default="repo", choices=supported(), help=argparse.SUPPRESS
    )
    chat.add_argument("--out", default=None, help=argparse.SUPPRESS)
    chat.set_defaults(run=_chat)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "run", None) is None:
        args = parser.parse_args([*(argv or []), "chat"])
    return asyncio.run(args.run(args))


if __name__ == "__main__":
    raise SystemExit(main())
