"""The tools the harness offers a session, and the gates behind them.

The model does judgement; these do the parts that must be exact. Validation lives inside the
tool rather than after the session, so a problem is returned into the conversation and fixed on
the next turn instead of surfacing once the session is already over.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import threading
from pathlib import Path
from typing import Any

from fi.simulate.agent.wrappers.http_events import (
    CompletedToolEvents,
    CompletedToolRows,
)

from .agent_type_check import (
    SECOND_LOOK_READ,
    SecondLook,
    agent_type_failure,
    browser_tools_found,
    browser_tools_in_agent,
    code_the_agent_reaches,
    failure_message,
    is_browser,
    modality_text,
)
from .backends import qualified as qualified  # noqa: F401  (re-export; callers import it here)
from .backends import tool, tool_server

from .contract import (
    prompt_quote,
    CALL_DIRECTIONS,
    AgentContract,
    Runtime,
    RuntimeInterface,
    RuntimeSecretFile,
    RuntimeConfigFile,
    HttpObservation,
    RuntimeHttpFailureRule,
    RuntimeHttpSetupRequest,
    LocalHttpAuth,
    known_modalities,
    generated_chat_interface_problem,
    runtime_public_environment,
    validate_contract,
)
from .failure_file import FAILURE_FILE, write_failure_file
from .instrumentation_evidence import detect_instrumentation
from .runtime_selection import (
    COMPOSE_SERVICE_OUTSIDE_FOLDER,
    PathLookupsSpent,
    RuntimeSelectionError,
    path_lookup_budget,
    select_runtime,
)
from .model_citation import (
    model_problems,
    quote_pieces,
    quote_problem,
    says_something,
    read_source,
    resolved_in_source,
    source_lines,
)
from .python_source import utf8_size
from .source_folder import SubfolderNotFound, agent_folder, bind_runtime

_LOG = logging.getLogger(__name__)

CONTRACT_SERVER = "contract"


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


# validate_contract returns short codes: they are stable, testable, and the same string every
# time. What a code means is a separate question, and answering it here keeps the codes exact
# while the message the model reads says what to actually do.
_GUIDANCE = {
    "empty:agent": "the `agent` field is empty. A short lower-case name; it is only the "
    "artifact folder's label",
    "no-use-cases": "the `real_use_cases` field is empty — note the name, it is not "
    "`use_cases`. List the concrete situations this agent handles, from its tools and data",
    "duplicate-tool-names": "the same tool is listed twice; keep one entry per tool",
    "types-for-unknown-args": "arg_types names an argument that is not in args. The names must "
    "match the source exactly",
}


def _advice(code: str) -> str:
    for key, said in _GUIDANCE.items():
        if code.startswith(key) or key in code:
            return f"{code} — {said}"
    return code


def _problems(problems: list[str], arrived: list[str] | None = None) -> dict[str, Any]:
    """Every problem at once, each with what to do about it.

    All of them together, never one at a time: a gate that reveals the next problem only after
    the last is fixed costs a full turn per problem and reads as though the rules are being
    invented as it goes.

    When the fields arrived under names this does not recognise, it says which names it got.
    Without that the answer is "agent is empty, there are no tools" about a submission that
    contained both, and the only way out is guessing at the packaging.
    """
    said = "Not accepted. Fix all of these and call submit_contract again:\n  - " + (
        "\n  - ".join(_advice(problem) for problem in problems)
    )
    unrecognised = arrived is not None and not any(
        key in arrived for key in ("agent", "tools", "real_use_cases")
    )
    if unrecognised:
        said += (
            f"\n\nWhat arrived was: {', '.join(arrived) or '(nothing)'}. None of those are "
            "contract fields, so the fields were probably nested inside something or sent as "
            "one JSON string. Send them as the tool's own top-level arguments — agent, tools, "
            "real_use_cases and the rest — not wrapped in an outer object."
        )
    return {
        "content": [{"type": "text", "text": said}],
        "is_error": True,
    }


_CONTRACT_KEYS = ("agent", "tools", "real_use_cases", "one_liner", "hard_constraints")


def _looks_like_a_contract(value: Any) -> bool:
    return isinstance(value, dict) and any(key in value for key in _CONTRACT_KEYS)


def unwrapped(payload: dict[str, Any]) -> dict[str, Any]:
    """The contract itself, however it was packaged.

    A contract is a nested thing being described, so it arrives wrapped — ``{"contract": {...}}``
    — or stringified, as JSON in a single argument, often enough to matter. In both the fields
    are present and correct and only the packaging is wrong. Rejecting that teaches nothing
    about the agent and costs a full turn, so it is unpacked; only an object that actually looks
    like a contract is unwrapped, so a real field that happens to hold a dict is never mistaken
    for an envelope.
    """
    if not isinstance(payload, dict):
        payload = {}
    if any(key in payload for key in ("agent", "tools", "real_use_cases")):
        return payload
    for value in payload.values():
        if _looks_like_a_contract(value):
            return value
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("```"):
                # Fenced JSON: the model wrote it as it would in a message.
                text = text.strip("`").removeprefix("json").strip()
            if not text.startswith("{"):
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if _looks_like_a_contract(parsed):
                return parsed
            for inner in parsed.values() if isinstance(parsed, dict) else []:
                if _looks_like_a_contract(inner):
                    return inner
    return payload


def accept_contract(
    payload: dict[str, Any],
    destination: Path,
    *,
    source_root: Path | None = None,
    require_setup_contract: bool = False,
    subfolder: str | None = None,
) -> dict[str, Any]:
    """The gate itself: validate, and write only if it passes.

    A plain function rather than only a tool body, so the rule that decides whether a contract
    is usable can be exercised and reasoned about without standing up a session.
    """
    checked = _checked_contract(
        payload,
        source_root=source_root,
        require_setup_contract=require_setup_contract,
        subfolder=subfolder,
    )
    if isinstance(checked, dict):
        return checked
    return _save_contract(checked, destination)


def _checked_contract(
    payload: dict[str, Any],
    *,
    source_root: Path | None,
    require_setup_contract: bool,
    subfolder: str | None,
    reached: list[Path] | None = None,
    asked: set[str] | None = None,
    held: dict[str, str] | None = None,
) -> AgentContract | dict[str, Any]:
    """The contract to save, or the problems to send back. It reads the customer's files and
    writes nothing, so the tool runs it off the event loop. ``reached`` is the code outside the
    folder the browser scan found the agent reaches, when it has run."""
    arrived = sorted(payload) if isinstance(payload, dict) else [type(payload).__name__]
    payload = _without_nulls(unwrapped(payload))
    try:
        contract = AgentContract.model_validate(payload)
    except Exception as invalid:
        return _problems([f"schema:{invalid}"[:600]], arrived)
    # Only the save records lines it couldn't confirm.
    contract.unconfirmed_prompt_from = []

    problems = validate_contract(contract)
    if (
        require_setup_contract
        and contract.modality != "voice"
        and (contract.data_store is None or not contract.data_store.kind.strip())
    ):
        problems.append(
            "missing-storage-decision: inspect the source and declare data_store.kind. "
            "Use its actual storage engine, filesystem for file-backed state, in_process "
            "for memory-only state, or none when there is no state store. "
            "Omitting data_store does not declare a stateless agent."
        )
    if require_setup_contract and contract.modality == "chat":
        if problem := generated_chat_interface_problem(contract.runtime):
            problems.append(problem)
    with path_lookup_budget():
        return _checked_against_source(
            contract, problems, arrived, source_root, subfolder, reached, asked, held
        )


def _checked_against_source(
    contract: AgentContract,
    problems: list[str],
    arrived: list[str],
    source_root: Path | None,
    subfolder: str | None,
    reached: list[Path] | None = None,
    asked: set[str] | None = None,
    held: dict[str, str] | None = None,
) -> AgentContract | dict[str, Any]:
    """The checks that read the customer's files, under the save's one path lookup budget.
    ``asked`` is the reading's record of what it has been asked once. A model whose citation was
    refused before is left out the second time, and lines that didn't hold the quote are left out
    for the quote, rather than refused again: both are optional, and neither may cost the
    reading its turns. ``held`` is the reading's latest quote (submit_contract keeps it), so
    lines named later without one are still checked against what was quoted."""
    if source_root is not None:
        from .source_tool_evidence import tool_evidence_problems

        quote = contract.system_prompt_excerpt
        # Lines named with no quote of their own (or one too short to say anything), checked
        # against the reading's latest quote, which submit_contract holds.
        alone = False
        if (
            held is not None
            and contract.system_prompt_from
            and not quote_pieces(quote)
            and held.get("prompt-quote")
            # A short quote held for this very call is this call's own, not one given before.
            and held["prompt-quote"] != quote
        ):
            quote = held["prompt-quote"]
            alone = True
        # Said when the fallback below sets the lines aside in a call refused for something else.
        set_aside: list[str] = []
        try:
            problems.extend(tool_evidence_problems(contract, source_root, subfolder))
            copy_problems = _copy_prompt(contract, source_root)
            problems.extend(copy_problems)
            if contract.system_prompt_from and not copy_problems:
                named = ", ".join(
                    f"{where.path[:200]} {where.start_line}-{where.end_line}"
                    for where in contract.system_prompt_from
                )
                if mismatch := quote_problem(quote, contract.system_prompt_excerpt, named):
                    if asked is None:
                        problems.append(mismatch)
                    elif alone and "prompt-lines-alone" not in asked:
                        # The lines may be right and the quote they were checked against wrong:
                        # asked once to quote these lines, before either is given up.
                        asked.add("prompt-lines-alone")
                        shown = " ".join(quote.split())[:80]
                        problems.append(
                            f"system_prompt_from: the lines named ({named}) don't hold the quote "
                            f"you gave before ({shown}); if these lines are the prompt, quote "
                            "them word for word in system_prompt_excerpt, with ... where you "
                            "leave text out. If they are refused again, the quote is kept and "
                            "the lines are recorded as unconfirmed"
                        )
                    elif asked & {"prompt-quote", "prompt-lines-alone"}:
                        # Either way the lines are kept on record as unconfirmed.
                        contract.unconfirmed_prompt_from = list(contract.system_prompt_from)
                        if quote_pieces(quote):
                            # The quote, which is what was read, rather than lines nobody
                            # confirmed.
                            contract.system_prompt_excerpt = quote
                            contract.system_prompt_from = []
                            set_aside.append(
                                f"system_prompt_from: the lines named ({named}) are left out and "
                                "the quote is kept, since they don't hold it"
                            )
                        else:
                            # A quote of a few words says too little to stand for the prompt:
                            # the lines are copied.
                            set_aside.append(
                                f"system_prompt_from: the lines named ({named}) are copied "
                                "unconfirmed, since they don't hold the short quote"
                            )
                    else:
                        problems.append(mismatch)
                        asked.add("prompt-quote")
                        problems.append(
                            "system_prompt_from: lines named later are checked against this "
                            "quote until you give another; a later mismatch, with any quote, "
                            "keeps the quote and records the lines as unconfirmed"
                        )
            if cited := model_problems(contract, source_root):
                if asked is not None and "model" in asked:
                    contract.model = None
                else:
                    problems.extend(cited)
                    if asked is not None:
                        asked.add("model")
                        problems.append("model: if the model is refused again, it is left out")
        except PathLookupsSpent as spent:
            return _problems([*problems, str(spent)], arrived)
        if problems:
            problems.extend(set_aside)
    else:
        # With no source there is no line to find the name on, so the model is unknown.
        contract.model = None
    runtime = contract.runtime
    if subfolder and source_root is not None:
        try:
            runtime = Runtime.model_validate(
                bind_runtime(
                    runtime.model_dump() if runtime is not None else None,
                    source_root,
                    subfolder,
                )
            )
            # A shell can change folder before the program starts, past the check on its
            # arguments; repair refuses a shell anyway.
            from .runtime_repair import _SHELL_LAUNCHERS

            if runtime.command and Path(runtime.command[0]).name.casefold() in _SHELL_LAUNCHERS:
                raise ValueError(
                    f"runtime_command_shell_forbidden: {runtime.command[0]}; name the program "
                    f"and its arguments directly so the command stays in {subfolder}"
                )
            if refusal := _builds_a_sibling(source_root, runtime, subfolder):
                problems.append(refusal)
        except SubfolderNotFound:
            problems.append(
                f"subfolder_not_found: {subfolder} is not a folder with files in the source, "
                "so no description of it can be saved."
            )
        except PathLookupsSpent as spent:
            problems.append(str(spent))
        except ValueError as invalid:
            problems.append(str(invalid))
    elif runtime is not None and runtime.source_subfolder:
        runtime = runtime.model_copy(update={"source_subfolder": ""})
    if problems:
        return _problems(problems, arrived)
    return contract.model_copy(
        update={
            # With a folder, what it imports from elsewhere is read too, as the other scans do.
            "instrumentation": detect_instrumentation(
                agent_folder(source_root, subfolder),
                code_the_agent_reaches(source_root, subfolder) if reached is None else reached,
            )
            if source_root and subfolder
            else detect_instrumentation(source_root),
            "runtime": runtime,
        }
    )


def _save_contract(contract: AgentContract, destination: Path) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / "contract.json"
    # Written beside it and moved into place: a write that fails partway leaves the contract an
    # earlier call was told was saved as it was.
    partial = destination / f".contract.json.{os.getpid()}.{threading.get_ident()}"
    try:
        partial.write_text(
            json.dumps(contract.model_dump(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)
    return _ok(
        f"Accepted and saved to {path}.\n"
        f"{len(contract.tools)} tools: {', '.join(sorted(contract.tool_names()))}\n"
        f"{len(contract.hard_constraints)} rules, "
        f"{len(contract.real_use_cases)} use cases, "
        f"{len(contract.open_questions)} open questions."
    )


# One prompt file, and what the excerpt copies from all of them: the same file named a hundred
# times would otherwise write a hundred of it into contract.json.
_LARGEST_PROMPT_FILE = 1024 * 1024
# What the copy reads from distinct files in all.
_LARGEST_PROMPT_READ = 4 * _LARGEST_PROMPT_FILE


def _copy_prompt(contract: AgentContract, source_root: Path) -> list[str]:
    """Replace the quoted excerpt with the lines `system_prompt_from` names, as written."""
    root = source_root.resolve()
    parts: list[str] = []
    problems: list[str] = []
    lines_of: dict[Path, list[str]] = {}
    # A file past the limit is read once too, and what was read of it counts.
    too_large: set[Path] = set()
    # Each path named is resolved once, however many entries name it.
    found_at: dict[str, Path | None] = {}
    read = copied = 0
    for index, where in enumerate(contract.system_prompt_from):
        if where.path not in found_at:
            # Through links, resolving one path can cost a lookup for each part of every
            # target; each is charged to the save's budget.
            found_at[where.path] = resolved_in_source(root, where.path)
        path = found_at[where.path]
        raw = None
        if path is not None and path not in lines_of and path not in too_large:
            raw = read_source(path, _LARGEST_PROMPT_FILE)
            if raw is None:
                # Not a regular file, or one that can't be opened: the same answer as missing.
                found_at[where.path] = path = None
        if path is None:
            problems.append(f"system_prompt_from[{index}]: {where.path} is not a file in the source")
            continue
        if raw is not None:
            read += len(raw)
            if read > _LARGEST_PROMPT_READ:
                problems.append(
                    f"system_prompt_from[{index}]: the files named so far come to more than "
                    "4 MiB; name only the files the prompt is written in"
                )
                break
            if len(raw) > _LARGEST_PROMPT_FILE:
                too_large.add(path)
            else:
                lines_of[path] = source_lines(raw)
        if path in too_large:
            problems.append(
                f"system_prompt_from[{index}]: {where.path} is larger than 1 MiB; name the "
                "file the prompt is written in"
            )
            continue
        lines = lines_of[path]
        if not 1 <= where.start_line <= where.end_line <= len(lines):
            problems.append(
                f"system_prompt_from[{index}]: lines {where.start_line}-{where.end_line} are "
                f"outside {where.path}, which has {len(lines)} lines"
            )
            continue
        excerpt = "\n".join(lines[where.start_line - 1 : where.end_line])
        copied += utf8_size(excerpt)
        if copied > _LARGEST_PROMPT_FILE:
            problems.append(
                f"system_prompt_from[{index}]: the lines named so far come to more than 1 MiB; "
                "name only the prompt's own lines, once each"
            )
            break
        parts.append(excerpt)
    if parts and not problems:
        contract.system_prompt_excerpt = "\n\n".join(parts)
    return problems


def _without_nulls(value: Any) -> Any:
    """Treat JSON null like an omitted optional field, including in nested records."""
    if isinstance(value, dict):
        return {
            key: item if key == "failure_values" else _without_nulls(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_without_nulls(item) for item in value if item is not None]
    return value


# Each selected eval is one judge call per call in the suite.
MOST_CHOSEN_EVALS = 6


def _new_receipt() -> str:
    return secrets.token_hex(4)


def _stop_message(destination: Path) -> str:
    said = failure_message(destination) or "This build can't go on."
    return f"{said} Don't submit again or do more work; tell the person."


# alk.yaml and the Compose files together; the largest real Compose file seen is 53 KB.
_SAVE_YAML_BYTES = 128 * 1024


def _builds_a_sibling(source_root: Path, runtime: Runtime, subfolder: str) -> str | None:
    """The refusal when the runtime's service builds another agent's folder, told to the model
    here because it can fix that. Any other fault in selecting is left for bundling to meet, as
    it does with no folder: the model can't repair a Compose file nobody can read."""
    from .bundle_author_v2 import _declared_runtime_environment

    # Overlays run `docker compose config`, and a file past real sizes takes seconds to read,
    # which the save would wait for; bundling refuses the same service, so they're left to it.
    if runtime.compose_overrides:
        return None
    try:
        folder = agent_folder(source_root, subfolder)
        manifest = folder / "alk.yaml"
        spent = 0
        if manifest.is_file():
            with manifest.open("rb") as handle:
                spent = len(handle.read(_SAVE_YAML_BYTES + 1))
        if spent > _SAVE_YAML_BYTES:
            return None
        select_runtime(
            source_root,
            runtime.model_dump(),
            # The values bundling sees, or this refuses what bundling accepts.
            public_environment={
                **_declared_runtime_environment(folder),
                **runtime_public_environment(runtime.model_dump()),
            },
            validate_environment_dependencies=False,
            compose_bytes=_SAVE_YAML_BYTES - spent,
        )
    except RuntimeSelectionError as refused:
        if str(refused).startswith(f"{COMPOSE_SERVICE_OUTSIDE_FOLDER}:"):
            return str(refused)
    except (ValueError, OSError, RuntimeError) as exc:
        # What the customer's files can make selecting raise; bundling selects again and owns
        # that failure. Anything else is a bug and isn't caught here. The type only: the message
        # can quote a line of the customer's file.
        _LOG.warning("sibling check skipped: %s", type(exc).__name__)
    return None


def _tools_in_agent_folder(
    scan_root: Path | None, subfolder: str | None, reached: list[Path] | None = None
) -> list[str]:
    """The browser tools this agent uses, which a sibling's aren't. ``reached`` is filled with
    the code it reaches outside its folder."""
    if not scan_root or not subfolder:
        return browser_tools_found(scan_root)
    try:
        return browser_tools_in_agent(scan_root, subfolder, reached)
    except SubfolderNotFound:
        # The folder check reports this; the model's reading shouldn't be refused for it too.
        return []


def contract_tools(
    destination: Path,
    available_evals: list[dict[str, Any]] | None = None,
    *,
    source_root: Path | None = None,
    scan_root: Path | None = None,
    require_setup_contract: bool = False,
    subfolder: str | None = None,
) -> Any:
    """A server exposing ``submit_contract``, writing to ``destination`` on acceptance.

    ``available_evals`` is the platform's own eval catalogue when the job carried one. Absent, the
    contract simply chooses none, which is what every job did before the catalogue existed.
    """
    events_schema = CompletedToolEvents.model_json_schema()
    events_schema["properties"]["failure_events"]["items"] = (
        events_schema.pop("$defs")["CompletedToolFailureEvent"]
    )
    observation_schema = HttpObservation.model_json_schema()
    observation_schema["properties"]["parameters"]["additionalProperties"] = (
        observation_schema.pop("$defs")["ObservationParameter"]
    )

    # Name to modality, empty or "any" for all. Refused here too, where it is still free to fix.
    offered_modality = {
        str(one.get("name") or "").strip(): str(one.get("modality") or "")
        .strip()
        .lower()
        for one in (available_evals or [])
        if isinstance(one, dict) and str(one.get("name") or "").strip()
    }
    offered = set(offered_modality)
    # Each of these is a nudge, not a wall: the first submission missing something that is
    # nearly always there gets sent back with directions, and a second submission is accepted.
    # A gate with no way through would permanently block the rare agent that genuinely lacks it,
    # and this stage cannot tell those two apart from the outside.
    nudged: set[str] = set()
    # What was quoted for the prompt when the lines named for it were refused.
    held: dict[str, str] = {}
    refused = False
    second_look = SecondLook()
    found: list[str] | None = None
    # Calls in one message check side by side, so the one sent last can finish first; it's the
    # model's last word, and an earlier call that finishes later mustn't write over it.
    sent = saved = 0
    # Calls in one message share one scan: each running its own takes that many times longer.
    scan: asyncio.Future[list[str]] | None = None
    # The code outside the folder the scan found the agent reaches, for the instrumentation
    # check, so the save walks the imports once.
    reach: list[Path] = []

    @tool(
        "submit_contract",
        "Submit the agent's testing contract: everything verifiably true about this agent, as "
        "one flat object. Every field is described in the schema; fill in what the source "
        "supports and leave the rest out.\n\n"
        "It is validated when you call it. If anything is wrong you get the whole list back at "
        "once, in terms of what to fix, and you submit again. When the agent's source is in front "
        "of you, submit only after an `agent_reader` has checked your draft against it and you "
        "have corrected what it found.",
        # Nothing required, and that is deliberate. This layer runs before the tool body, so
        # anything it rejects never reaches the code that could have understood it — a contract
        # sent inside a wrapper is complete and correct, and is unwrapped a few lines below, but
        # only if it gets there. accept_contract is the single gate; it reports every problem at
        # once and says what to do about each.
        #
        # The descriptions are the point of this block. The schema is shown to the model before
        # it calls anything, so what is written here is the difference between a correct first
        # call and a sequence of rejected guesses.
        schema(
            {
                SECOND_LOOK_READ: {
                    "type": "string",
                    "description": "Only when a second look asked for it: the value it gave. "
                    "It is not part of the contract.",
                },
                "agent": {
                    "type": "string",
                    "description": "Short lower-case identifier, no spaces. Only a label for "
                    "the artifact folder.",
                },
                "one_liner": {
                    "type": "string",
                    "description": "One sentence: what this agent is for.",
                },
                "modality": {
                    "type": "string",
                    "enum": list(known_modalities()),
                    "description": "How this agent is run, read from its runtime. A voice "
                    "session (LiveKit, telephony, TTS/STT) is voice; a text conversation is chat; "
                    "an agent a person uses to carry out work on websites or a computer is "
                    "browser. This decides how it is later run.",
                },
                "call_direction": {
                    "type": "string",
                    "enum": list(CALL_DIRECTIONS),
                    "description": "Voice only, and read from the agent's own instructions rather "
                    "than guessed. Outbound if it places the call and the person is not expecting "
                    'it ("you placed this call", "this is us calling about"); inbound if '
                    'people dial in to it ("callers dial in", "thanks for calling"). '
                    "Leave unset for chat, which a person always starts. This decides how the "
                    "simulated person is briefed: someone who did not dial has no opening request "
                    "to make.",
                },
                "agent_languages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Voice only: the languages the agent's own instructions say it "
                    "speaks, for example ['English'] for 'respond in English only'. Leave empty when "
                    "its instructions do not say. The simulated caller's transcriber follows it.",
                },
                "conversational": {
                    "type": "boolean",
                    "description": "True if a person talks with it turn by turn. False for an "
                    "agent given one task and left to it.",
                },
                "system_prompt_excerpt": {
                    "type": "string",
                    "description": "The agent's own instructions, quoted. Often lives away from "
                    "the main agent file.",
                },
                "system_prompt_from": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "start_line": {"type": "integer"},
                            "end_line": {"type": "integer"},
                        },
                        "required": ["path", "start_line", "end_line"],
                    },
                    "description": "Where the agent's whole prompt is written: every part it is "
                    "assembled from, in order, as a path relative to the source root and the first "
                    "and last line of each. Once they are checked against your quote in "
                    "system_prompt_excerpt, these lines replace it as written: keep the quote too.",
                },
                # Nothing required inside it either: a model sent without its line reaches the
                # save, which says what is missing, instead of failing the whole call here.
                "model": {
                    "type": "object",
                    "properties": {
                        "provider": {"type": "string"},
                        "name": {"type": "string"},
                        "path": {"type": "string"},
                        "line": {"type": "integer"},
                    },
                    "description": "The model the agent talks to the person with, read from its "
                    "source: provider as the source names it (openai, anthropic, google...; leave "
                    "out when it doesn't say), name exactly as written (letters, digits and "
                    ". _ : / @ - only), and the file (relative to "
                    "the source root) and line the name is written on. One model; a voice agent's "
                    "speech-to-text and text-to-speech models don't count. Leave model out when "
                    "the name is only set at run time.",
                },
                "hard_constraints": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Rules it must obey, in the source's own words. The agent "
                    "under test is told these and graded against them.",
                },
                "tools": {
                    "type": "array",
                    "description": "Every tool the agent really has. Everything downstream is "
                    "built from these, so a tool without its arguments cannot be tested.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "The exact callable name the model emits.",
                            },
                            "args": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Exact parameter names, in order.",
                            },
                            "arg_types": {
                                "type": "object",
                                "description": "Declared type per argument where the source "
                                'states one: {"recipient_ids": "list[str]"}.',
                            },
                            "arg_values": {
                                "type": "object",
                                "description": "Real permitted values per argument where it is "
                                "constrained to a set, an enum or a lookup: "
                                '{"priority": ["low", "normal", "urgent"]}.',
                            },
                            "description": {"type": "string"},
                            "requires": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Tools that must have run before this one stops "
                                "refusing, read from a guard in its own body. Only state the agent "
                                "builds during the conversation counts: a quote taken, an option "
                                "selected. Identity established when the call opens does not, "
                                "since it holds before any tool runs. Empty means callable first "
                                "thing, and naming a tool that is not gated makes every future "
                                "test of it replay a preamble it never needed.",
                            },
                            "refuses": {
                                "type": "string",
                                "description": "Every refusal that depends on an argument's value "
                                "or on something outside the call, each with what it waits for, and "
                                "every refusal the code returns as an ordinary value rather than an "
                                "error. Read from the tool, the helpers it calls and the service "
                                "behind it.",
                            },
                            "effects": {
                                "type": "string",
                                "description": "What a successful call reads, creates and changes, "
                                "and what earlier state it clears, which a later step then has to "
                                "produce again.",
                            },
                            "inputs_from": {
                                "type": "object",
                                "additionalProperties": {"type": "string"},
                                "description": "Arguments whose value must come from an earlier "
                                "tool's output, as argument name to that tool's name.",
                            },
                        },
                        # Nothing required: a tool genuinely taking no arguments is ordinary,
                        # and requiring args here rejects the whole contract because of one.
                        # That every tool has none is the real defect, and validate_contract
                        # is where it is caught, with an explanation.
                    },
                },
                "data_schema": {
                    "type": "object",
                    "description": "The shape of the records the agent works on: which fields "
                    "each kind of record has.",
                },
                "base_environment": {
                    "type": "object",
                    "description": "Its real starting data, reproduced exactly — including "
                    "anything that looks like a mistake. The world is a replica, not a "
                    "corrected version.",
                },
                "runtime_dependencies": {
                    "type": "array",
                    "description": "External runtime connections such as RTC transport and model "
                    "inference providers. These use the supplied configuration/credentials; "
                    "they are not business-world stores or services to recreate. Never put "
                    "business databases, files, queues or tools backends here.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "kind": {
                                "type": "string",
                                "description": "transport or inference",
                            },
                            "what": {"type": "string"},
                            "engine": {"type": "string"},
                            "reached": {
                                "type": "object",
                                "properties": {
                                    "dsn_env": {
                                        "type": "string",
                                        "description": "Environment variable NAME, never its secret value",
                                    },
                                },
                            },
                        },
                        "required": ["name"],
                    },
                },
                "dependencies": {
                    "type": "array",
                    "description": "Business-world dependencies only. Put RTC transport and model "
                    "provider connections in runtime_dependencies instead. Everything this agent reaches for that has to exist before "
                    "it can work, so the next stage knows what to build. A datastore, a service "
                    "it calls over HTTP, a file it reads, a queue it publishes to. The world is "
                    "a sandbox and nothing reaches outside it, so each of these is built inside "
                    "it — the agent's call goes to something real that happens to be ours.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "kind": {
                                "type": "string",
                                "description": "datastore, service, file, queue, or whatever "
                                "this actually is.",
                            },
                            "what": {
                                "type": "string",
                                "description": "What it holds or answers, and what the agent "
                                "needs from it.",
                            },
                            "used_by": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "The tools that cannot work without it.",
                            },
                            "engine": {
                                "type": "string",
                                "description": "The exact database/service engine the agent uses.",
                            },
                            "version": {
                                "type": "string",
                                "description": "The engine version where the source pins one.",
                            },
                            "reached": {
                                "type": "object",
                                "description": "The agent's existing connection seam. Record "
                                "where secrets come from, never their values.",
                                "properties": {
                                    "dsn_env": {"type": "string"},
                                    "config_key": {"type": "string"},
                                    "host": {"type": "string"},
                                    "port": {"type": "integer"},
                                    "database": {"type": "string"},
                                    "user": {"type": "string"},
                                    "password_from": {"type": "string"},
                                    "loader_module": {"type": "string"},
                                    "loader_function": {"type": "string"},
                                },
                            },
                        },
                    },
                },
                "real_use_cases": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "What this agent is for, one plain sentence each. These are "
                    "capabilities, not test cases: 'cancel an order that has not shipped', not "
                    "a narrated situation with a customer, a name and an outcome. Each one is "
                    "something the agent's own text says it does, never stretched from a passing "
                    "word or a tool name. Scenarios are written later, from these.",
                },
                "caller_identity": {
                    "type": "string",
                    "description": "How the agent knows who it is talking to (the number they "
                    "call from, a login, an account id passed in), and what it loads from that "
                    "before any tool runs (their account, an order or booking already in flight). "
                    "Read it from the code that starts a session. Empty only when the agent never "
                    "identifies the person.",
                },
                "notes": {
                    "type": "string",
                    "description": "Free-form, yours. Anything else worth carrying forward: "
                    "quirks, traps, a plausible name that does not exist, an id that looks like "
                    "a typo but is real. Shown verbatim to every later stage.",
                },
                "open_questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "What the source did not settle and you could not ask about.",
                },
                "chosen_evals": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Names of platform evals this agent should be judged by, "
                    f"at most {MOST_CHOSEN_EVALS}, taken only from the catalogue in your "
                    "briefing. Choose the ones that judge how the agent conducted the "
                    "conversation, which its scenarios' own checks cannot see, and only from "
                    "the section matching the modality you record here. Leave it out where the "
                    "catalogue offers nothing this agent can be judged by; an eval whose inputs "
                    "this agent never produces scores it against nothing, and an eval for spoken "
                    "calls means nothing for a chat agent.",
                },
                "implementation": {
                    "type": "string",
                    "enum": ["present", "absent", "partial"],
                    "description": "Whether the agent ships working code for its tools, as "
                    "opposed to only declaring them. The environment runs the agent's own code "
                    "wherever it exists, so this decides whether anything gets written for it.",
                },
                "tool_entrypoints": {
                    "type": "array",
                    "description": "How to reach the agent's own implementation of each tool. "
                    "One entry per tool that has code. Without this the environment has to write "
                    "a replacement, which tests our reading of the agent instead of the agent.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {
                                "type": "string",
                                "description": "The tool name, exactly as in `tools`.",
                            },
                            "mode": {
                                "type": "string",
                                "enum": [
                                    "import",
                                    "construct",
                                    "service",
                                    "unreachable",
                                ],
                                "description": "import: a module-level function or a method on a "
                                "class, reachable directly. construct: it hangs off an object "
                                "that has to be built first. service: its effect is implemented "
                                "by a shipped HTTP service. unreachable: no runnable seam exists, "
                                "which blocks environment creation rather than generating a "
                                "replacement.",
                            },
                            "module": {
                                "type": "string",
                                "description": "Importable path as the agent's own code would "
                                "write it, e.g. package.module.file. Not a filesystem path.",
                            },
                            "callable": {
                                "type": "string",
                                "description": "What to call inside that module. May be dotted "
                                "to reach a method on a class, e.g. TheClass.the_method.",
                            },
                            "factory": {
                                "type": "string",
                                "description": "For construct: the expression that builds the "
                                "object, including whatever it needs to be constructed with.",
                            },
                            "first_arg": {
                                "type": "string",
                                "description": "If the callable takes the agent's own state as "
                                "its first argument, its name. Empty when the callable opens its "
                                "own connection instead.",
                            },
                            "service": {
                                "type": "string",
                                "description": "For service mode, the Compose service or source "
                                "dependency that answers this tool.",
                            },
                            "endpoint": {
                                "type": "string",
                                "description": "The dependency POST path invoked by this tool, "
                                "without a leading slash. Record it even for import/construct "
                                "tools when their implementation calls a service, and especially "
                                "when it differs from the model-facing name (for example "
                                "check_status calling get_status). Leave empty only for a fully "
                                "local tool.",
                            },
                            "method": {
                                "type": "string",
                                "enum": ["POST"],
                                "description": "HTTP method used by the submitted implementation.",
                            },
                            "notes": {
                                "type": "string",
                                "description": "Anything about reaching it that the fields above "
                                "do not carry, especially why a tool cannot be reached.",
                            },
                        },
                    },
                },
                "refusal_signature": {
                    "type": "string",
                    "description": "How this agent's own code says no in a value it returns "
                    "rather than by raising, described so it can be recognised, e.g. a string "
                    "beginning with a particular marker. Production code often reports failure "
                    "this way, and without this a refusal is recorded as a success, which hides "
                    "the behaviour most worth testing.",
                },
                "data_store": {
                    "type": "object",
                    "description": "What the agent's tools read and write, and how to point them "
                    "at a different one. Explicitly declare kind, including none for a stateless "
                    "agent, during autonomous source setup.",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "description": "postgres, clickhouse, mysql, sqlite, in_process for "
                            "state held in memory, filesystem for file-backed state, or none.",
                        },
                        "configured_by": {
                            "type": "string",
                            "description": "How the code chooses its connection: the environment "
                            "variable it reads, the config file, or the constructor argument. "
                            "This is what makes substituting a store possible without editing "
                            "the agent, so say if it is hardcoded.",
                        },
                        "schema_from": {
                            "type": "string",
                            "description": "Where the schema comes from: its migrations, a DDL "
                            "file, its ORM models.",
                        },
                        "loaded_by": {
                            "type": "string",
                            "description": "The agent's own loader, if it has one that builds "
                            "its starting data, as module and callable.",
                        },
                        "loader_module": {
                            "type": "string",
                            "description": "The module that loader is imported from, so it can "
                            "be called rather than reimplemented.",
                        },
                        "version": {
                            "type": "string",
                            "description": "The engine version, where the agent pins one.",
                        },
                        "config_key": {
                            "type": "string",
                            "description": "Where a config file holds the connection instead, as "
                            "a dotted path such as database.url.",
                        },
                        "host": {
                            "type": "string",
                            "description": "The host the agent expects. Record it even when it "
                            "is hardcoded: a hardcoded name is not a dead end, it is a name our "
                            "store can answer to.",
                        },
                        "port": {
                            "type": "integer",
                            "description": "The port it expects.",
                        },
                        "database": {
                            "type": "string",
                            "description": "The database name it expects. Ours is created with "
                            "exactly this name rather than the agent being changed.",
                        },
                        "user": {
                            "type": "string",
                            "description": "The user it connects as.",
                        },
                        "password_from": {
                            "type": "string",
                            "description": "Where the password comes from, never the password "
                            "itself. A contract is written to disk and read by people, so a "
                            "secret in it outlives the run that needed it.",
                        },
                    },
                },
                "runtime": {
                    "type": "object",
                    "description": "What it takes to run the agent's code.",
                    "properties": {
                        "language": {
                            "type": "string",
                            "description": Runtime.model_fields["language"].description,
                        },
                        "version": {"type": "string"},
                        "install": {
                            "type": "string",
                            "description": Runtime.model_fields["install"].description,
                        },
                        "extras": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": Runtime.model_fields["extras"].description,
                        },
                        "requirements_files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": Runtime.model_fields[
                                "requirements_files"
                            ].description,
                        },
                        "observations": {
                            "type": "object",
                            "maxProperties": 32,
                            "additionalProperties": observation_schema,
                        },
                        "workspaces": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                            "description": Runtime.model_fields[
                                "workspaces"
                            ].description,
                        },
                        "config_files": {
                            "type": "object",
                            "additionalProperties": RuntimeConfigFile.model_json_schema(),
                            "description": "Source-relative dotenv instance resources. Public values and declared target-provider aliases only; confidential files remain outside build contexts.",
                        },
                        "directories": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Source-relative missing writable bind directories created independently per instance.",
                        },
                        "secret_files": {
                            "type": "object",
                            "additionalProperties": {
                                "type": "array",
                                "items": RuntimeSecretFile.model_json_schema(),
                            },
                            "description": "Confidential files by selected service. Bind declared target-provider aliases to absolute container destinations; never include secret values.",
                        },
                        "workdir": {
                            "type": "string",
                            "description": "Where in the source imports resolve from, if not the "
                            "root.",
                        },
                        "execution": {
                            "type": "string",
                            "enum": ["auto", "native", "compose"],
                            "description": "Select native or compose explicitly from source evidence. auto preserves legacy discovery.",
                        },
                        "service": {
                            "type": "string",
                            "description": "Exact selected Compose service. Its dependency closure is retained.",
                        },
                        "additional_services": {
                            "type": "array",
                            "maxItems": 32,
                            "uniqueItems": True,
                            "items": {
                                "type": "string",
                                "maxLength": 128,
                                "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]*$",
                            },
                            "description": "Additional source-declared Compose roots needed by this workflow. Their declared dependency closures are retained alongside service. Requires explicit compose_file and service; exclude service itself. Never infer roots from environment URLs.",
                        },
                        "required_environment": {
                            "type": "object",
                            "additionalProperties": {
                                "type": "array",
                                "items": {"type": "string"},
                                "uniqueItems": True,
                            },
                            "description": "Required environment names by selected process name. Native control process is agent. No values.",
                        },
                        "compose_file": {
                            "type": "string",
                            "description": "Repository-relative path to the submitted Compose file to run "
                            "when the repository contains more than one. Never invent one.",
                        },
                        "compose_image_services": {
                            "type": "array",
                            "maxItems": 64,
                            "uniqueItems": True,
                            "items": {
                                "type": "string",
                                "maxLength": 128,
                                "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]*$",
                            },
                            "description": Runtime.model_fields["compose_image_services"].description,
                        },
                        "compose_overrides": {
                            "type": "array",
                            "maxItems": 8,
                            "uniqueItems": True,
                            "items": {"type": "string", "maxLength": 1024},
                            "description": "Ordered repository-relative source Compose overlays applied after compose_file using the native Compose CLI. Requires explicit compose_file and service. Never synthesize overlays or change upstream files.",
                        },
                        "dockerfile": {
                            "type": "string",
                            "description": "Path to its own Dockerfile, if it has one. Theirs is "
                            "used in preference to anything written for it.",
                        },
                        "command": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Exact argv for the submitted process when the "
                            "repository has no container entrypoint. Leave empty only when one "
                            "conventional entrypoint is unambiguous in source.",
                        },
                        "context_excludes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Repository-relative large generated outputs or "
                            "documentation or repeated test-only patterns to omit from a generated "
                            "build context. Simple * and ** patterns are supported. Never omit "
                            "runtime source, dependency manifests, seed data, or tool data.",
                        },
                        "platform": {
                            "type": "string",
                            "description": "Container target declared by the repository, such "
                            "as linux/amd64. Preserve it exactly; never infer one from the "
                            "runner machine.",
                        },
                        "interface": {
                            "type": "object",
                            "description": "For a chat agent, the existing ingress exposed by "
                            "the submitted runtime. Record only an endpoint the repository "
                            "actually implements; never invent a gateway or route.",
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": ["http", "websocket", "callable", "command"],
                                    "description": RuntimeInterface.model_fields["kind"].description,
                                },
                                "protocol": {
                                    "type": "string",
                                    "enum": ["fi.alk", "openai_chat", "json_template"],
                                    "description": RuntimeInterface.model_fields["protocol"].description,
                                },
                                "model": {
                                    "type": "string",
                                    "description": "For openai_chat: exact source-owned model "
                                    "or profile identifier accepted by this endpoint. Read it "
                                    "from the source API or configuration; do not substitute "
                                    "the backing provider model unless the endpoint expects it.",
                                },
                                "port": {
                                    "type": "integer",
                                    "description": "Container port the submitted chat service "
                                    "listens on.",
                                },
                                "path": {
                                    "type": "string",
                                    "description": "Exact POST path for one conversational turn. "
                                    "Path placeholders may use canonical inputs or prior setup "
                                    "captures; substituted values are encoded as path segments.",
                                },
                                "health_path": {
                                    "type": "string",
                                    "description": "Optional existing GET readiness path.",
                                },
                                "include_tools": {
                                    "type": "boolean",
                                    "description": "Whether the endpoint accepts tool schemas "
                                    "with each request.",
                                },
                                "request_template": {
                                    "description": "For json_template only: the exact JSON body "
                                    "accepted by one turn. Supported placeholders include "
                                    "{{thread_id}}, {{execution_id}}, {{turn_index}}, "
                                    "{{scenario_name}}, {{messages}}, {{new_message}}, "
                                    "{{new_message_content}}, {{tools}}, and {{metadata}}. A "
                                    "whole-value placeholder preserves arrays/objects; embedded "
                                    "placeholders become strings. The source's user_id field can "
                                    "use {{thread_id}}; {{user_id}} is not a provided placeholder. "
                                    "Unknown placeholders are rejected.",
                                },
                                "response_path": {
                                    "type": "string",
                                    "description": RuntimeInterface.model_fields["response_path"].description,
                                },
                                "completed_tools": CompletedToolRows.model_json_schema(),
                                "completed_tool_events": events_schema,
                                "local_auth": LocalHttpAuth.model_json_schema(),
                                "setup_secrets": {
                                    "type": "object",
                                    "additionalProperties": {"type": "string"},
                                    "maxProperties": 16,
                                    "description": "Map private setup placeholder names to declared target-provider secret aliases. Values resolve privately into setup bodies/headers only. Never put credential values in this declaration.",
                                },
                                "failure_rule": {
                                    "type": "object",
                                    "required": ["status_path"],
                                    "oneOf": [
                                        {"required": ["failure_values"]},
                                        {"required": ["success_values"]},
                                    ],
                                    "description": "Optional source-declared application failure envelope. Never infer failures from an arbitrary error field.",
                                    "properties": {
                                        "status_path": {
                                            "type": "string",
                                            "minLength": 1,
                                            "maxLength": 512,
                                        },
                                        "failure_values": {
                                            "type": "array",
                                            "minItems": 1,
                                            "maxItems": 32,
                                            "items": {
                                                "type": [
                                                    "string",
                                                    "number",
                                                    "boolean",
                                                    "null",
                                                ]
                                            },
                                        },
                                        "success_values": {
                                            "type": "array",
                                            "minItems": 1,
                                            "maxItems": 32,
                                            "items": {
                                                "type": [
                                                    "string",
                                                    "number",
                                                    "boolean",
                                                    "null",
                                                ]
                                            },
                                            "description": "Alternative to failure_values. Any status outside this explicit successful set fails, including missing status fields. Preserve JSON scalar types: [null] for JSON null, [0] for numeric zero, [false] for boolean false. An empty list is invalid.",
                                        },
                                        "message_path": {
                                            "type": "string",
                                            "maxLength": 512,
                                        },
                                    },
                                },
                                "headers_template": {
                                    "type": "object",
                                    "additionalProperties": {"type": "string"},
                                    "maxProperties": 32,
                                    "description": RuntimeInterface.model_fields["headers_template"].description,
                                },
                                "setup_requests": {
                                    "type": "array",
                                    "maxItems": 8,
                                    "description": "Source-evidenced HTTP requests that must run "
                                    "once before the first turn, such as creating a session. Paths "
                                    "and JSON bodies accept the same placeholders. If a response "
                                    "creates an identifier, capture it by dotted response path and "
                                    "use the capture name as {{name}} in later templates.",
                                    "items": {
                                        "type": "object",
                                        "required": ["path"],
                                        "properties": {
                                            "method": {
                                                "type": "string",
                                                "enum": ["GET", "POST", "PUT", "PATCH"],
                                            },
                                            "path": {"type": "string"},
                                            "body_template": {},
                                            "encoding": {
                                                "type": "string",
                                                "enum": ["json", "form"],
                                            },
                                            "headers_template": {
                                                "type": "object",
                                                "description": RuntimeHttpSetupRequest.model_fields["headers_template"].description,
                                                "additionalProperties": {
                                                    "type": "string"
                                                },
                                                "maxProperties": 32,
                                            },
                                            "capture_headers": {
                                                "type": "object",
                                                "additionalProperties": {
                                                    "type": "string"
                                                },
                                                "maxProperties": 16,
                                                "description": "Map placeholder names to response header names. Use capture for JSON response fields; cookies are automatic.",
                                            },
                                            "failure_rule": {
                                                **RuntimeHttpFailureRule.model_json_schema(),
                                                "oneOf": [
                                                    {"required": ["failure_values"]},
                                                    {"required": ["success_values"]},
                                                ],
                                                "description": "Optional source-declared application status rule for this setup response. Checked after HTTP status and before captures or the next request. Use it when HTTP success can contain application failure.",
                                            },
                                            "accepted_statuses": {
                                                "type": "array",
                                                "items": {"type": "integer"},
                                                "maxItems": 16,
                                            },
                                            "capture": {
                                                "type": "object",
                                                "description": "Map safe placeholder names to "
                                                "dotted/list paths in this setup response, for "
                                                'example {"session_id": "id"}.',
                                                "additionalProperties": {
                                                    "type": "string"
                                                },
                                                "maxProperties": 16,
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
            [],
        ),
    )
    async def submit_contract(args: dict[str, Any]) -> dict[str, Any]:
        payload = dict(unwrapped(args))
        # The quote as the contract will read it: under any alias, a list joined.
        excerpt = prompt_quote(payload)
        # Held before anything can send this call back (a nudge, a refusal, a schema problem), so
        # lines named later without a quote are checked against the reading's latest one. A short
        # quote is held only while nothing longer is: it says less.
        if quote_pieces(excerpt) or (
            says_something(excerpt) and not quote_pieces(held.get("prompt-quote", ""))
        ):
            held["prompt-quote"] = excerpt
        # What this call was already asked, as it stood when the call was sent: a call sent in the
        # same message as one that is refused hasn't seen that refusal yet.
        asked_before = set(nudged)
        receipt = payload.pop(SECOND_LOOK_READ, None) or (
            args.get(SECOND_LOOK_READ) if isinstance(args, dict) else None
        )
        nonlocal refused, found, sent, saved, scan
        # Taken before the scan's wait: a call already running when a look went out hasn't read it.
        looked_before = second_look.given
        sent += 1
        mine = sent
        if refused or (destination / FAILURE_FILE).is_file():
            return _ok(_stop_message(destination))
        if found is None:
            # Run again only a scan that can't give its answer here: one that raised or was
            # cancelled, or one on another loop, which this one can't wait on. One that finished
            # after every call waiting on it was cancelled still holds its answer.
            if (
                scan is None
                or scan.get_loop() is not asyncio.get_running_loop()
                or (scan.done() and (scan.cancelled() or scan.exception() is not None))
            ):
                scan = asyncio.ensure_future(
                    asyncio.to_thread(_tools_in_agent_folder, scan_root, subfolder, reach)
                )
            # Shielded: one call cancelled mustn't cancel the scan the others wait on.
            found = await asyncio.shield(scan)
            # A call running alongside this one may have refused while this one waited.
            if refused or (destination / FAILURE_FILE).is_file():
                return _ok(_stop_message(destination))
        claimed = modality_text(payload.get("modality"))
        if claimed and not is_browser(claimed) and claimed not in known_modalities():
            return _problems(
                [
                    f"{claimed!r} is not a modality. Use voice or chat, or browser if its main "
                    "job is work on websites or a computer."
                ]
            )
        verdict = second_look.verdict(claimed, receipt, found, given_before=looked_before)
        if verdict == "refuse":
            # Set first: if the write fails, the model must not get a resubmission through.
            refused = True
            # First: an earlier reading in this folder would otherwise build the agent just
            # refused, even if the write below fails.
            try:
                (destination / "contract.json").unlink(missing_ok=True)
            finally:
                write_failure_file(agent_type_failure(found), destination)
            return _ok(_stop_message(destination))
        if verdict == "second_look":
            return _problems([second_look.issue(claimed, found, _new_receipt())])
        data_store = payload.get("data_store")
        if not isinstance(data_store, dict):
            data_store = {}
        source_has_seed_loader = bool(
            str(data_store.get("loaded_by") or "").strip()
            or str(data_store.get("loader_module") or "").strip()
        )
        data_schema = payload.get("data_schema")
        if not isinstance(data_schema, dict):
            data_schema = {}
        base_environment = payload.get("base_environment")
        if not isinstance(base_environment, dict):
            base_environment = {}
        schema_collections = {
            str(name).strip().lower().replace("-", "_").replace(" ", "_")
            for name in data_schema
        }
        starting_collections = {
            str(name).strip().lower().replace("-", "_").replace(" ", "_")
            for name in base_environment
        }
        collection_overlap = schema_collections & starting_collections
        store_kind = str(data_store.get("kind") or "").strip().lower()
        structured_seed_store = any(
            marker in store_kind
            for marker in ("sqlite", "postgres", "mysql", "mariadb", "clickhouse")
        )
        badly_aligned_seed = (
            source_has_seed_loader
            and structured_seed_store
            and len(schema_collections) > 1
            and bool(starting_collections)
            and len(collection_overlap) * 2 < len(starting_collections)
        )
        if badly_aligned_seed:
            return _problems(
                [
                    "base_environment does not use enough of the collections declared by "
                    "data_schema. Record source fixture rows under their exact collection/table "
                    "names (for example hotel_rooms, hotel_bookings, "
                    "restaurant_reservations), preserving real IDs and values. Semantic "
                    "summaries such as rooms, notable_bookings, pricing or restaurant_slots "
                    "cannot seed or verify a structured submitted store. This is a correctness "
                    "requirement for a source-loaded structured store; submit a corrected "
                    "contract rather than the same summary again."
                ]
            )

        def rows_for(collection: str) -> list[dict[str, Any]]:
            value = base_environment.get(collection, [])
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
            if isinstance(value, dict):
                return [value]
            return []

        missing_fk_parents: list[str] = []
        if source_has_seed_loader and structured_seed_store:
            for collection, fields in data_schema.items():
                if not isinstance(fields, dict):
                    continue
                for field, specification in fields.items():
                    words = (
                        str(specification).replace("(", " ").replace(")", " ").split()
                    )
                    try:
                        marker = next(
                            index
                            for index, word in enumerate(words)
                            if word.upper() == "FK"
                        )
                    except StopIteration:
                        continue
                    if marker + 1 >= len(words):
                        continue
                    target_reference = words[marker + 1].strip("`'\".,:;")
                    if "." in target_reference:
                        target, target_key = target_reference.rsplit(".", 1)
                    else:
                        target = target_reference
                        inferred_key = str(field).rsplit("_", 1)[-1]
                        target_fields = data_schema.get(target)
                        if (
                            isinstance(target_fields, dict)
                            and inferred_key in target_fields
                        ):
                            target_key = inferred_key
                        elif (
                            isinstance(target_fields, dict)
                            and str(field) in target_fields
                        ):
                            # A same-named key such as payment_methods.rider_id ->
                            # users.rider_id must not be shortened blindly to users.id.
                            target_key = str(field)
                        else:
                            target_key = inferred_key
                    target_values = {
                        row.get(target_key)
                        for row in rows_for(target)
                        if row.get(target_key) not in (None, "")
                    }
                    for row in rows_for(str(collection)):
                        value = row.get(field)
                        if value not in (None, "") and value not in target_values:
                            missing_fk_parents.append(
                                f"{collection}.{field}={value!r} -> {target}.{target_key}"
                            )
        if missing_fk_parents:
            examples = ", ".join(sorted(set(missing_fk_parents))[:8])
            return _problems(
                [
                    "base_environment is not referentially closed: "
                    + examples
                    + ". Include the exact submitted parent rows for every represented FK. "
                    "Downstream environment generation must never invent a stub row that the "
                    "target's source loader does not contain."
                ]
            )

        # Refused rather than dropped: dropping leaves the run judged by fewer evals than claimed.
        asked_evals = payload.get("chosen_evals")
        if isinstance(asked_evals, str):
            asked_evals = [asked_evals]
        if isinstance(asked_evals, list):
            wanted: list[str] = []
            for one in asked_evals:
                name = str(one).strip()
                if name and name not in wanted:
                    wanted.append(name)
            unknown = [name for name in wanted if offered and name not in offered]
            if unknown:
                return _problems(
                    [
                        "chosen_evals names evals the platform did not offer: "
                        + ", ".join(unknown)
                        + ". Choose only from the catalogue in your briefing, by exact name."
                    ]
                )
            if not offered and wanted:
                return _problems(
                    [
                        "chosen_evals was given but this job carries no eval catalogue, so there "
                        "is nothing to choose from. Leave it out."
                    ]
                )
            claimed = str(payload.get("modality") or "").strip().lower()
            mismatched = [
                f"{name} (applies to {offered_modality[name]})"
                for name in wanted
                if offered_modality.get(name)
                and offered_modality[name] not in ("any", claimed)
            ]
            if mismatched:
                return _problems(
                    [
                        f"chosen_evals names evals belonging to another modality than {claimed!r}: "
                        + ", ".join(mismatched)
                        + ". The platform refuses those, so choose only from the section of the "
                        "catalogue matching the modality you recorded."
                    ]
                )
            if len(wanted) > MOST_CHOSEN_EVALS:
                return _problems(
                    [
                        f"chosen_evals has {len(wanted)} evals and at most {MOST_CHOSEN_EVALS} may "
                        "be chosen. Keep the ones that judge conduct this agent's own checks "
                        "cannot see, and drop the rest."
                    ]
                )
            payload["chosen_evals"] = wanted

        thin = [
            (
                "prompt",
                bool(payload.get("conversational", True))
                and not payload.get("hard_constraints")
                and not excerpt
                # Lines named are the prompt, found: copied whole, with no quote needed.
                and not payload.get("system_prompt_from"),
                "no hard_constraints and no system_prompt_excerpt, for a conversational agent. "
                "Its prompt usually exists and often lives away from the main agent file — "
                "search the whole source for a long instructions string before deciding there "
                "is none. If you find it, name its lines in system_prompt_from so it is copied "
                "whole.",
            ),
            (
                "data",
                bool(payload.get("tools"))
                and structured_seed_store
                and not payload.get("data_schema")
                and not payload.get("base_environment"),
                "no data_schema and no base_environment, although data_store declares a "
                "structured database. Record the source-owned schema and any existing records "
                "the tools require, using real source identifiers and values. An initially "
                "empty database may be correct; do not invent records or schema to satisfy "
                "this warning.",
            ),
            (
                "starting-data",
                bool(payload.get("tools"))
                and bool(payload.get("data_schema"))
                and not payload.get("base_environment")
                and source_has_seed_loader,
                "data_schema is present but base_environment is empty even though data_store "
                "records a source seed loader. Read that fixture and record its real starting "
                "identifiers and values: the whole dataset when small, or a representative "
                "sample that includes every branch likely to be tested. Downstream scenarios "
                "must use those exact records; a plausible invented identifier creates a world "
                "the submitted agent does not have.",
            ),
            (
                "starting-data-collections",
                source_has_seed_loader
                and len(schema_collections) > 1
                and bool(starting_collections)
                and len(collection_overlap) < min(2, len(schema_collections)),
                "base_environment does not use the collections declared by data_schema. Record "
                "the source fixture rows under their exact collection/table names (for example "
                "hotel_rooms, hotel_bookings, restaurant_reservations), preserving real IDs and "
                "values. Semantic summaries such as rooms, notable_bookings or restaurant_slots "
                "cannot seed or verify the submitted store.",
            ),
            (
                # A quote may stop short of the whole prompt; lines are copied whole. Asked once,
                # since a prompt built at run time has no lines to name, and its quote is kept.
                "prompt-lines",
                source_root is not None
                and bool(excerpt)
                and not payload.get("system_prompt_from")
                # Asked already, with the "prompt" nudge or a refusal of named lines: the lines
                # aren't asked for twice.
                and not nudged & {"prompt", "prompt-quote", "prompt-lines-alone"},
                "system_prompt_excerpt is a quote, and system_prompt_from names no lines. If the "
                "prompt is written in the source, name its lines in system_prompt_from so it is "
                "copied whole.",
            ),
        ]
        # All of them together, and each only once. Nudging in sequence would cost a turn per
        # nudge and read as though the requirements were being invented one at a time.
        say = [said for key, when, said in thin if when and key not in nudged]
        nudged.update(key for key, when, _ in thin if when)
        if say:
            return _problems(
                say + ["If any of these genuinely does not apply, submit again as is."]
            )
        # The checks read the customer's files, which can take a second on a crafted one, so
        # they run off the loop. The write stays here, after a refusal that came meanwhile.
        checked = await asyncio.to_thread(
            _checked_contract,
            payload,
            source_root=source_root,
            require_setup_contract=require_setup_contract,
            subfolder=subfolder,
            # The scan's answer is for scan_root; another root's code is found again.
            reached=reach if scan_root == source_root else None,
            asked=asked_before,
            held=held,
        )
        # Whatever this call asked is asked for the calls sent after it.
        nudged.update(asked_before)
        if isinstance(checked, dict):
            return checked
        if refused or (destination / FAILURE_FILE).is_file():
            return _ok(_stop_message(destination))
        if mine < saved:
            return _ok(
                "Not saved: a contract submitted after this one, in the same turn, was saved "
                "instead. Don't submit this one again."
            )
        result = _save_contract(checked, destination)
        # After the write: if it raised, nothing was saved for an earlier call to defer to.
        saved = mine
        return result

    return tool_server(name=CONTRACT_SERVER, version="0.1.0", tools=[submit_contract])


_JSON_TYPES = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """A tool's inputs, described well enough to be filled in correctly the first time.

    Two things this exists for.

    **Required means required.** Handing the decorator a plain ``{name: type}`` mapping marks
    every parameter mandatory, so a tool with an optional field refuses any call that leaves it
    out — "Input validation error: 'seed' is a required property" — for a field the tool itself
    treats as optional.

    **A schema is documentation, not just validation.** It is shown to the model before it calls
    anything, so a property carrying only ``{"type": "array"}`` says nothing about what belongs
    in it, and the model discovers the shape by being rejected. That is a full turn per guess and
    it is avoidable: pass a full JSON-schema fragment instead of a bare type wherever the shape
    is not obvious from the name, and it is right on the first call.

        schema({"name": str,
                "size": {"type": "string", "enum": ["S", "M", "L"]}}, ["name"])
    """
    wanted = list(required)
    return {
        "type": "object",
        "properties": {
            name: _schema_fragment(dict(kind), optional=name not in wanted)
            if isinstance(kind, dict)
            else _typed(_JSON_TYPES.get(kind, "string"), optional=name not in wanted)
            for name, kind in properties.items()
        },
        "required": wanted,
    }


def _schema_fragment(fragment: dict[str, Any], *, optional: bool) -> dict[str, Any]:
    """Make every non-required object property nullable before the SDK validates it.

    Tool input validation happens before our handler. Models correctly use null for optional
    nested fields, so allowing null at only the top level still rejects useful payloads such as
    ``{"tool_entrypoints": [{"module": null}]}`` before normalization can omit the value.
    """
    result = dict(fragment)
    if result.get("type") == "object" and isinstance(result.get("properties"), dict):
        required = set(result.get("required") or [])
        result["properties"] = {
            name: _schema_fragment(dict(value), optional=name not in required)
            for name, value in result["properties"].items()
        }
    elif result.get("type") == "array" and isinstance(result.get("items"), dict):
        result["items"] = _schema_fragment(dict(result["items"]), optional=False)
    if optional:
        kind = result.get("type")
        if isinstance(kind, str):
            result["type"] = [kind, "null"]
        if isinstance(result.get("enum"), list) and None not in result["enum"]:
            result["enum"] = [*result["enum"], None]
    return result


def _typed(kind: str, *, optional: bool) -> dict[str, Any]:
    """One property's type, letting an optional field be null.

    Filling a field that does not apply with null is what a model does, and it is not wrong: the
    alternative is inventing a value. Rejecting it costs a whole turn, and the rejection does not
    even say which field was at fault: "None is not of type 'string'" is the entire message.
    """
    return {"type": [kind, "null"]} if optional else {"type": kind}


def brief(value: Any, limit: int = 1800) -> str:
    """What a call returned, shortened only when it has to be.

    Generous, and explicit when it cuts. A record from a real agent's data is long, and a reply
    trimmed silently in the middle of it reads as though the field being looked for is absent:
    the answer is then six more calls working around something that was there all along.

    Shared, because every stage that shows a caller what a tool answered has the same problem and
    they were not agreeing about it: one showed 1800 characters and said when it cut, the other
    showed 200 and said nothing, so the stage that most needs to read a record was the one that
    could not.
    """
    rendered = value if isinstance(value, str) else json.dumps(value, default=str)
    if len(rendered) <= limit:
        return rendered
    return (
        rendered[:limit]
        + f"\n... cut here, {len(rendered) - limit} more characters. Ask for one record rather "
        "than many if you need the whole of it."
    )
