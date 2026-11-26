"""Changing the contract after the fact, and being honest about having done it.

The contract is what the agent verifiably is, read from its own source. That makes it the thing
everything downstream is confined to, and it is why the harness cannot invent a tool or a value.

But it is not permanent. Two situations genuinely require changing it, and they are different:

- **It was read wrong.** Stage one missed a value the agent really accepts. Correcting that is
  restoring the truth, and the correction should come from the source.
- **The agent is being changed.** Somebody adds an item to the world because the real menu is
  gaining one. The world and the action space have to move together: an item the world holds but
  the agent cannot name is dead data, and a scenario about it can only fail.

Either way the amendment is recorded on the contract itself rather than blended into what was
read, so that a month later it is still possible to tell what came from the agent and what came
from us. That distinction is the whole value of the contract; quietly widening it would make it
the same kind of guess it exists to prevent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..providers.agent import _redact
from .contract import (
    CONTRACT_FILE,
    AgentContract,
    AmendmentRecord,
    RecordRefusal,
    RuleSource,
    rule_id,
    known_modalities,
    validate_contract,
    write_contract,
)
from .json_file import write_json_atomic

CONTRACT = CONTRACT_FILE
MAX_RULE_CHARS = 2000


def _kept_shape(written: Any, before: Any) -> Any:
    """``written`` without the empty keys ``before`` never had, so an edit never shows a change to a field nobody
    set."""
    if isinstance(written, dict) and isinstance(before, dict):
        return {
            key: _kept_shape(value, before.get(key))
            for key, value in written.items()
            if key in before or value not in ("", None, [], {})
        }
    if isinstance(written, list) and isinstance(before, list) and len(written) == len(before):
        return [_kept_shape(one, was) for one, was in zip(written, before)]
    return written


def widen(
    contract: AgentContract,
    destination: Path,
    *,
    tool_name: str,
    argument: str,
    values: list[str],
    why: str,
) -> tuple[bool, str]:
    """Let a tool's argument accept values it did not before.

    Amends the contract the stage is holding, then persists it. Loading a second copy from disk
    and writing that back would leave the stage still working from the old one, so the world it
    goes on to check would be checked against an action space that no longer matches.

    Returns whether it was amended, and what happened.
    """
    spec = next((tool for tool in contract.tools if tool.name == tool_name), None)
    if spec is None:
        return False, (
            f"{tool_name!r} is not in the contract. It has: "
            f"{', '.join(sorted(contract.tool_names()))}. To add a tool the agent's source defines "
            "(one it gained, or one an earlier version removed), use declare_tool."
        )
    if argument not in spec.args:
        return False, (
            f"{tool_name} takes no argument called {argument!r}. It takes: "
            f"{', '.join(spec.args) or 'nothing'}"
        )
    if not why.strip():
        return (
            False,
            "say why: an unexplained amendment is indistinguishable from a guess",
        )

    existing = spec.arg_values.get(argument)
    current = list(existing) if isinstance(existing, (list, tuple)) else []
    fresh = [value for value in values if value and value not in current]
    if not fresh:
        return False, f"{argument} already accepts {', '.join(values) or 'nothing new'}"

    spec.arg_values[argument] = [*current, *fresh]
    contract.amendments.append(
        f"{tool_name}.{argument} widened by {', '.join(fresh)}: {why.strip()}"
    )

    problems = validate_contract(contract)
    if problems:
        spec.arg_values[argument] = current
        contract.amendments.pop()
        return False, "the amended contract would not be valid: " + "; ".join(problems)

    contract.amendment_records.append(AmendmentRecord(kind="tool", key=tool_name, action="changed"))
    _persist(contract, destination)
    return True, (
        f"{tool_name}.{argument} now accepts {', '.join(fresh)}. "
        f"{len(contract.amendments)} amendment(s) recorded on the contract."
    )


# Rules dropped since the person's current message began, by environment: an add in the same message with no
# `replaces` names them, so the builder links the one it rewords.
_DROPPED_THIS_TURN: dict[str, list[str]] = {}


def _turn_key(destination: Path) -> str:
    return str(Path(destination).resolve())


def begin_turn(destination: Path) -> None:
    """A new message from the person: rules dropped before it are no longer this message's rewrite."""
    _DROPPED_THIS_TURN.pop(_turn_key(destination), None)


def _dropped_this_turn(contract: AgentContract, destination: Path) -> list[str]:
    """The rules dropped during this message that no rule replaces yet."""
    dropped = set(_DROPPED_THIS_TURN.get(_turn_key(destination), []))
    return [
        one.key
        for one in contract.amendment_records
        if one.kind == "rule" and one.action == "removed" and not one.replaced_by and one.key in dropped
    ]


def add_rule(
    contract: AgentContract,
    destination: Path,
    *,
    rule: str,
    why: str,
    replaces: str = "",
    source_file: str = "",
    source_quote: str = "",
) -> tuple[bool, str]:
    """Give the agent a rule its source did not state, or (`replaces`) reword one it has.

    A hard constraint is not decoration: the agent under test is told it, and the judge grades
    against it. So this is a real change to what is being tested, and like a widened argument it
    is recorded rather than blended into what was read from the source. `replaces` names the rule
    this one rewrites, by its id or its exact words, a current rule or one removed earlier: the
    records then link the two (`replaced_by`), and nothing is inferred from how alike they read.
    """
    rule = rule.strip()
    if not rule:
        return False, "no rule given"
    if len(rule) > MAX_RULE_CHARS:
        return False, f"a rule can be at most {MAX_RULE_CHARS} characters; this one has {len(rule)}. Ask for a shorter rule"
    if bool(source_file.strip()) != bool(source_quote.strip()):
        return False, "a rule's source is a file and the line quoted from it: give both, or neither"
    if len(source_quote.strip()) > 500:
        return False, "source_quote is the one line the rule is written on, quoted in at most 500 characters"
    if not why.strip():
        return False, "say why: an unexplained rule is indistinguishable from a guess"
    # Whether two rules are one rule reworded is the builder's judgment: the rules dropped in this message are only
    # named in the reply, never linked here.
    dropped = [] if replaces.strip() else _dropped_this_turn(contract, destination)
    # Both sides are compared as they are saved: a contract held from an older save can still
    # hold its rules raw. One call, so a refused URL is logged once. The reply names the rule as
    # saved too, since the chat sends it on to the platform.
    cleaned, *existing = _redact([rule, *contract.hard_constraints])
    current, removed = _replaced(contract, replaces) if replaces.strip() else (None, None)
    if cleaned.lower() in (one.lower() for one in existing):
        held = next(one for one, saved in zip(contract.hard_constraints, existing) if saved.lower() == cleaned.lower())
        if removed is not None:
            # Added before it was said to replace the removed rule: the two are linked now, nothing else changes.
            return _link(contract, destination, removed, held)
        return False, f"the agent already has that rule: {cleaned}"
    if replaces.strip() and current is None and removed is None:
        return False, (
            f"no rule {replaces.strip()!r} to replace: name it by its id or its exact words. It has:\n  - "
            + "\n  - ".join(f"{rule_id(one)}: {one}" for one in contract.hard_constraints)
        )

    if current is not None:
        contract.hard_constraints.remove(current)
    contract.hard_constraints.append(rule)
    contract.amendments.append(f"rule added — {rule}: {why.strip()}")
    problems = validate_contract(contract)
    if problems:
        contract.hard_constraints.pop()
        contract.amendments.pop()
        if current is not None:
            contract.hard_constraints.append(current)
        return False, "the amended contract would not be valid: " + "; ".join(problems)

    old = current if current is not None else (removed.text if removed is not None else "")
    new_id = rule_id(contract.hard_constraints[-1])
    was_written = contract.rule_source(old) if old else None
    if source_file.strip():
        contract.rule_sources.append(
            RuleSource(rule=contract.hard_constraints[-1], file=source_file.strip(), quote=source_quote.strip())
        )
    elif was_written is not None:
        # A reworded rule still comes from where the old one was written; that line is what its cases cite.
        contract.rule_sources.append(was_written.model_copy(update={"rule": contract.hard_constraints[-1]}))
    if current is not None:
        contract.amendment_records.append(
            AmendmentRecord(kind="rule", key=rule_id(current), action="removed", replaced_by=new_id, text=current)
        )
    elif removed is not None:
        removed.replaced_by = new_id
    contract.amendment_records.append(
        AmendmentRecord(
            kind="rule",
            key=new_id,
            action="added",
            previous=rule_id(old) if old else "",
            text=contract.hard_constraints[-1],
        )
    )
    _persist(contract, destination)
    return True, (
        f"added. The agent now has {len(contract.hard_constraints)} rules, and this one is "
        "graded from here on."
        + "".join(
            f" You dropped {one} earlier in this message. If this rule rewords it, link them: add_rule with this "
            f"rule and replaces {one}."
            for one in dropped
        )
    ) + (
        cases_citing(destination, rule=old, written=was_written, rules=contract.hard_constraints)
        if old
        else _unlinked_removals(contract, new_id)
    )


def _link(contract: AgentContract, destination: Path, removed: AmendmentRecord, rule: str) -> tuple[bool, str]:
    """Record that `rule`, already among the rules, replaced the removed one."""
    new_id = rule_id(rule)
    removed.replaced_by = new_id
    added = next(
        (one for one in reversed(contract.amendment_records) if one.kind == "rule" and one.key == new_id), None
    )
    if added is not None:
        added.previous = removed.key
    else:
        contract.amendment_records.append(
            AmendmentRecord(kind="rule", key=new_id, action="added", previous=removed.key, text=rule)
        )
    _persist(contract, destination)
    written = contract.rule_source(removed.text)
    if written is not None and contract.rule_source(rule) is None:
        contract.rule_sources.append(written.model_copy(update={"rule": rule}))
        _persist(contract, destination)
    return True, f"linked: {new_id} replaces {removed.key}." + cases_citing(
        destination, rule=removed.text, written=written, rules=contract.hard_constraints
    )


def _unlinked_removals(contract: AgentContract, new_id: str) -> str:
    """The rules removed and not replaced by any rule: one this rule rewords is linked with `replaces`."""
    open_ = [
        one
        for one in contract.amendment_records
        if one.kind == "rule" and one.action == "removed" and not one.replaced_by
    ]
    if not open_:
        return ""
    return (
        "\nThese rules were removed and nothing replaces them yet. If this rule rewords one, link them now: "
        f"add_rule with this rule and replaces naming that rule's id ({new_id} is this one).\n  - "
        + "\n  - ".join(f"{one.key}: {one.text}" for one in open_)
    )


def _replaced(contract: AgentContract, named: str) -> tuple[str | None, AmendmentRecord | None]:
    """The rule `named` (by id or exact words) among the current rules, else among those removed and not yet
    replaced."""
    named = named.strip()
    current = next((one for one in contract.hard_constraints if named in (rule_id(one), one)), None)
    if current is not None:
        return current, None
    removed = next(
        (
            one
            for one in reversed(contract.amendment_records)
            if one.kind == "rule" and one.action == "removed" and not one.replaced_by and named in (one.key, one.text)
        ),
        None,
    )
    return None, removed


def set_modality(
    contract: AgentContract, destination: Path, *, modality: str, why: str
) -> tuple[bool, str]:
    """Correct how a person actually reaches this agent.

    Worth its own amendment because modality is the one field that reroutes everything: it picks
    the world, the simulator and the transport, so a wrong value does not degrade a run, it runs
    a different test. And it is the field the source is least able to settle. An agent's code
    reads the same whether it is answering a chat window or a phone call, so a reader with no
    other evidence concludes whatever the repository looks like, which for a text benchmark is
    text, even when the person asking said they had deployed it to a phone number.

    Where the agent is deployed is a fact about somebody's setup rather than about the source, so
    when the two disagree the person is right and the source is describing a different runtime of
    the same agent. Recorded like every other amendment, because it is still a change to what was
    read.
    """
    named = (modality or "").strip().lower()
    if named not in known_modalities():
        return (
            False,
            f"{named!r} is not a modality. It is one of: {', '.join(known_modalities())}",
        )
    if named == "browser":
        # The kind is set where the agent is read, after its second look; a correction here
        # would skip that look and the build's own check of the environment.
        return False, (
            "whether an agent drives a browser is settled when it is read, not during a build, "
            f"so the type stays {contract.modality}. Read the agent again to change it."
        )
    if "browser" in contract.environment_needs and named != "chat":
        return False, (
            "this agent drives a browser and runs as a chat agent does, so the type stays "
            f"{contract.modality}."
        )
    if not why.strip():
        return False, "say why: modality decides how every scenario is run"
    if named == contract.modality:
        return False, f"the contract already says {named}"

    was = contract.modality
    contract.modality = named
    contract.amendments.append(f"modality {was} -> {named}: {why.strip()}")
    problems = validate_contract(contract)
    if problems:
        contract.modality = was
        contract.amendments.pop()
        return False, "the amended contract would not be valid: " + "; ".join(problems)

    contract.amendment_records.append(
        AmendmentRecord(kind="modality", key=str(named), action="changed", previous=str(was or ""))
    )
    _persist(contract, destination)
    reached = {
        "voice": "a call placed to the agent where it is hosted, its own tools answered over a "
        "webhook",
        "chat": "a typed conversation, the agent reconstructed here from its contract",
    }[named]
    return True, f"modality is now {named}. Every scenario will be run as {reached}."


def drop_rule(
    contract: AgentContract,
    destination: Path,
    *,
    rule: str,
    why: str,
    replaced_by: str = "",
    orphan: bool = False,
) -> tuple[bool, str]:
    """Take away a rule the agent does not really have.

    Stage one can misread a comment as a constraint, and a rule nobody has is worse than a
    missing one: the agent under test is told to obey it and the judge fails it for not doing
    something it was never supposed to do.
    """
    if not why.strip():
        return False, "say why: removing a rule changes what is being graded"
    # Both sides are compared as they are saved: a contract held from an older save can still
    # hold its rules raw. One call, so a refused URL is logged once.
    wanted, *saved_rules = _redact([rule.strip(), *contract.hard_constraints])
    wanted = wanted.lower()
    held = list(zip(contract.hard_constraints, (one.lower() for one in saved_rules)))
    match = next(
        (existing for existing, as_saved in held if as_saved == wanted),
        None,
    ) or next(
        (existing for existing, as_saved in held if wanted in as_saved),
        None,
    )
    if match is None:
        # The rules as saved, since the chat sends this reply on to the platform.
        return False, (
            "no rule like that. It has:\n  - "
            + "\n  - ".join(saved_rules)
        )
    if replaced_by.strip():
        # The rule reworded: added with `replaces`, so the two are linked and the cases citing it are reviewed.
        return add_rule(contract, destination, rule=replaced_by, why=why, replaces=rule_id(match))
    resting = _cases_resting_on(
        destination, rule=match, written=contract.rule_source(match), rules=contract.hard_constraints
    )
    if resting and not orphan:
        # Cases still rest on it: whether it is reworded or really goes is the builder's to say, in this call.
        return False, (
            f"not removed: {len(resting)} case(s) cite this rule ({rule_id(match)}). Say what becomes of it in this "
            "call: `replaced_by` with the new rule's text when it is reworded (the two are linked and those cases "
            "are listed for review), or `orphan: true` when the agent really no longer has it (those cases must then "
            "be rewritten or dropped)."
        )
    contract.hard_constraints.remove(match)
    contract.amendments.append(f"rule removed — {match}: {why.strip()}")
    contract.amendment_records.append(AmendmentRecord(kind="rule", key=rule_id(match), action="removed", text=match))
    _persist(contract, destination)
    _DROPPED_THIS_TURN.setdefault(_turn_key(destination), []).append(rule_id(match))
    return True, (
        f"removed ({rule_id(match)}). {len(contract.hard_constraints)} rules left. To reword it instead, add the new "
        "rule with replaces naming this id."
        + cases_citing(
            destination, rule=match, written=contract.rule_source(match), rules=contract.hard_constraints
        )
    )


def fix_tool(
    contract: AgentContract,
    destination: Path,
    *,
    tool_name: str,
    why: str,
    args: list[str] | None = None,
    arg_types: dict[str, str] | None = None,
    arg_units: dict[str, str] | None = None,
    returns: dict[str, str] | None = None,
    description: str = "",
    refuses: str = "",
    effects: str = "",
    inputs_from: dict[str, str] | None = None,
    remove: bool = False,
    refusals: list[dict[str, Any]] | None = None,
) -> tuple[bool, str]:
    """Correct a tool that was read wrong, or take away one the agent does not have.

    The most damaging thing stage one can get wrong. Every argument name flows into the world's
    handlers, the probes and the scenarios, so a tool recorded with the wrong argument produces
    a world that refuses everything and a suite that blames the agent for it.
    """
    if not why.strip():
        return False, "say why: this changes what everything downstream is built from"
    spec = next((tool for tool in contract.tools if tool.name == tool_name), None)
    if spec is None:
        return False, (
            f"{tool_name!r} is not in the contract. It has: "
            f"{', '.join(sorted(contract.tool_names()))}. To add a tool the agent's source defines "
            "(one it gained, or one an earlier version removed), use declare_tool."
        )

    if remove:
        before = contract.model_copy(deep=True)
        contract.tools.remove(spec)
        drop_tool_references(contract)
        contract.amendments.append(f"tool removed — {tool_name}: {why.strip()}")
        problems = validate_contract(contract)
        if problems:
            for field in ("tools", "tool_entrypoints", "dependencies", "amendments"):
                setattr(contract, field, getattr(before, field))
            return False, "cannot remove it: " + "; ".join(problems)
        contract.amendment_records.append(AmendmentRecord(kind="tool", key=tool_name, action="removed"))
        _persist(contract, destination)
        return True, f"{tool_name} removed. {len(contract.tools)} tools left" + cases_citing(destination, tool=tool_name)

    changed = []
    if args is not None:
        # Values recorded against an argument that no longer exists would silently be lost, so
        # they are carried across by name and anything orphaned is said out loud.
        orphaned = sorted(set(spec.arg_values) - set(args))
        spec.args = list(args)
        spec.arg_types = {k: v for k, v in spec.arg_types.items() if k in spec.args}
        spec.arg_values = {k: v for k, v in spec.arg_values.items() if k in spec.args}
        spec.arg_units = {k: v for k, v in spec.arg_units.items() if k in spec.args}
        spec.arg_required = [k for k in spec.arg_required if k in spec.args]
        spec.arg_bounds = {k: v for k, v in spec.arg_bounds.items() if k in spec.args}
        changed.append(f"arguments are now {', '.join(args)}")
        if orphaned:
            changed.append(f"dropped values recorded for {', '.join(orphaned)}")
    if arg_types:
        unknown = sorted(set(arg_types) - set(spec.args))
        if unknown:
            return False, f"{tool_name} takes no argument called {', '.join(unknown)}"
        spec.arg_types.update(arg_types)
        changed.append("types updated")
    if arg_units:
        unknown = sorted(set(arg_units) - set(spec.args))
        if unknown:
            return False, f"{tool_name} takes no argument called {', '.join(unknown)}"
        spec.arg_units.update(arg_units)
        changed.append(
            "units updated ("
            + "; ".join(
                f"{field} in {unit}{_code_says(contract, spec, field)}" for field, unit in sorted(arg_units.items())
            )
            + "); if the code that reads a field or the data it holds says otherwise, the code's unit is the one to "
            "record, and the person is told about the mismatch"
        )
    if returns:
        spec.returns.update(returns)
        changed.append("returned fields updated")
    if description:
        spec.description = description
        changed.append("description updated")
    for field, value in (("refuses", refuses), ("effects", effects), ("inputs_from", inputs_from)):
        if value:
            setattr(spec, field, value)
            changed.append(f"{field} updated")
    if refusals is not None:
        try:
            spec.refusals = [RecordRefusal.model_validate(one) for one in refusals]
        except ValueError as exc:
            return False, f"refusals not understood: {exc}"
        changed.append("record refusals updated")
    if not changed:
        return False, (
            "nothing to change: give args, arg_types, arg_units, returns, description, refuses, refusals, effects, "
            "inputs_from, or remove"
        )

    contract.amendments.append(f"tool corrected — {tool_name}: {why.strip()}")
    problems = validate_contract(contract)
    if problems:
        return False, "the amended contract would not be valid: " + "; ".join(problems)
    contract.amendment_records.append(AmendmentRecord(kind="tool", key=tool_name, action="changed"))
    _persist(contract, destination)
    return True, f"{tool_name}: {', '.join(changed)}" + cases_citing(destination, tool=tool_name)


def drop_tool_references(contract: AgentContract) -> list[str]:
    """Drop entrypoints, ``requires`` and ``used_by`` naming tools the contract no longer has.

    Returns where each dropped name was. Free text is left alone.
    """
    kept = contract.tool_names()
    dropped = [
        f"tool_entrypoints {one.tool}"
        for one in contract.tool_entrypoints
        if one.tool not in kept
    ]
    contract.tool_entrypoints = [
        one for one in contract.tool_entrypoints if one.tool in kept
    ]
    for owner, attribute, label in (
        *((one, "requires", f"{one.name}.requires") for one in contract.tools),
        *((one, "used_by", f"{one.name}.used_by") for one in contract.dependencies),
    ):
        names = getattr(owner, attribute)
        dropped.extend(f"{label} {name}" for name in names if name not in kept)
        setattr(owner, attribute, [name for name in names if name in kept])
    return dropped


def set_worker_tools(
    contract: AgentContract, destination: Path, *, worker: str, allowed_tools: list[str], why: str
) -> tuple[bool, str]:
    """Change which of the agent's tools one participant may use.

    The test world refuses every other tool for that participant, so this is the only way a request such as "let
    both of them update an order" changes what is tested: a description or an argument's values never do. It is a
    widening the person asked for and confirmed, not something read from the source: any of the agent's tools may be
    named, one the contract did not list for the participant is added to what it holds, and the reason is recorded as
    an amendment so it is never taken for what the code showed.
    """
    found = next((one for one in contract.workers if one.id == worker), None)
    if found is None:
        return False, (
            f"{worker!r} is not one of this agent's participants. They are: "
            f"{', '.join(one.id for one in contract.workers) or 'none'}"
        )
    if not why.strip():
        return False, "say why: an unexplained change to what a participant may use is indistinguishable from a guess"
    wanted = list(dict.fromkeys(one.strip() for one in allowed_tools if one.strip()))
    if not wanted:
        return False, "name at least one tool it may use"
    unknown = sorted(set(wanted) - contract.tool_names())
    if unknown:
        return False, (
            f"{', '.join(unknown)} is not a tool this agent has. It has: {', '.join(sorted(contract.tool_names()))}"
        )
    was_tools, was_allowed = list(found.tools), list(found.allowed_tools)
    held = [*found.tools, *(one for one in wanted if one not in found.tools)]
    # Allowed every tool it holds: no list, so the participant is never limited to a set that misses a later tool.
    allowed = [] if set(wanted) == set(held) else wanted
    if held == was_tools and set(allowed) == set(was_allowed):
        return False, f"{worker} may already use {', '.join(wanted)}"
    found.tools, found.allowed_tools = held, allowed
    contract.amendments.append(f"{worker} may use {', '.join(wanted)}, as asked: {why.strip()}")
    problems = validate_contract(contract)
    if problems:
        found.tools, found.allowed_tools = was_tools, was_allowed
        contract.amendments.pop()
        return False, "the amended contract would not be valid: " + "; ".join(problems)
    contract.amendment_records.append(AmendmentRecord(kind="worker", key=worker, action="changed"))
    _persist(contract, destination)
    refused = sorted(set(held) - set(wanted))
    return True, (
        f"{worker} may now use {', '.join(wanted)}"
        + (f"; the test world refuses its {', '.join(refused)}" if refused else "")
        + ". The change takes effect in the next saved version."
    )


# The texts a rule change listed for review that are not yet edited or kept, by scenario key and field.
RULE_REVIEW = ".futureagi-rule-review.json"


def _review_fields(scenario: Any) -> dict[str, str]:
    """The texts that state what a case tests, by the field `edit_scenario` changes: its use case (the heading its
    group is shown under), branch, tests, instruction, and each control's `why` as `control:<name>`."""
    texts = {
        "use_case": scenario.use_case,
        "branch": scenario.branch,
        "tests": scenario.tests,
        "instruction": scenario.instruction,
        **{f"control:{control.name}": control.why for control in scenario.negative_controls},
    }
    return {field: " ".join(str(text).split()) for field, text in texts.items() if text}


def _case_texts(scenario: Any) -> str:
    """A listed case with the texts that state what it tests, so the review reads each against the current rule."""
    head = f"{scenario.scenario_key or scenario.name}: {scenario.name}"
    shown = {"use_case": "use case (its group heading)"}
    return head + "".join(
        f"\n      {shown.get(field, field.replace(':', ' '))}: {text}" for field, text in _review_fields(scenario).items()
    )


def _load_review(destination: Path) -> dict[str, dict[str, str]]:
    try:
        held = json.loads((Path(destination) / RULE_REVIEW).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return held if isinstance(held, dict) else {}


def _open_review(destination: Path, citing: list[Any]) -> None:
    """Hold every listed text of the cases a rule change rests on as it reads now, until each is edited or kept."""
    held = _load_review(destination)
    for one in citing:
        held[one.scenario_key or one.name] = {**held.get(one.scenario_key or one.name, {}), **_review_fields(one)}
    write_json_atomic(Path(destination) / RULE_REVIEW, held)


def review_still_open(destination: Path, scenarios: list[Any]) -> dict[str, list[str]]:
    """The listed texts that still read as they did when the rule changed, by scenario key: a text edited since,
    or a case no longer in the suite, is done and forgotten here."""
    held = _load_review(destination)
    if not held:
        return {}
    by_key = {one.scenario_key or one.name: one for one in scenarios}
    still: dict[str, dict[str, str]] = {}
    for key, fields in held.items():
        if key not in by_key or not isinstance(fields, dict):
            continue
        now = _review_fields(by_key[key])
        same = {field: text for field, text in fields.items() if now.get(field) == text}
        if same:
            still[key] = same
    if still != held:
        write_json_atomic(Path(destination) / RULE_REVIEW, still)
    return {key: sorted(fields) for key, fields in still.items()}


def keep_reviewed(destination: Path, key: str, fields: list[str]) -> list[str]:
    """Mark listed texts of one case as read against the changed rule and kept as they are; returns those marked."""
    held = _load_review(destination)
    open_ = held.get(key) or {}
    kept = [field for field in fields if field in open_]
    for field in kept:
        open_.pop(field)
    if open_:
        held[key] = open_
    else:
        held.pop(key, None)
    write_json_atomic(Path(destination) / RULE_REVIEW, held)
    return kept


def _rests_on(
    scenario: Any, destination: Path, *, rule: str, written: RuleSource | None, rules: list[str]
) -> bool:
    """Whether a case loses its ground with `rule` gone, the agent keeping `rules`: a `rule` source that resolves to
    `rule` (`grounding.resolve_sources`) and to none of `rules`, or a `file` source citing exactly the line `rule` was
    recorded from (`written`) when that line is no longer in the file."""
    from .grounding import Rules, agent_source_root, resolve_sources
    from .leaks import normalise

    sources = list(getattr(scenario, "sources", None) or [])
    if not sources:
        return False
    root = agent_source_root(destination)
    now = resolve_sources(scenario, Rules(hard_constraints=list(rules)), destination, root)
    held = resolve_sources(scenario, Rules(hard_constraints=[*rules, rule]), destination, root) if rule else now
    named = written.file.strip().removeprefix("file:").strip("/") if written is not None else ""
    for source, after, before in zip(sources, now, held):
        if source.kind == "rule" and rule and before["resolved"] and not after["resolved"]:
            return True
        if (
            source.kind == "file"
            and written is not None
            and normalise(written.quote)
            and source.ref.strip().removeprefix("file:").strip("/") == named
            and normalise(source.quote) == normalise(written.quote)
            and after["resolved"] is False
        ):
            return True
    return False


def _sub_goals_quoting(destination: Path, rule: str) -> list[str]:
    """The sub-goals whose `reference`, `what` or criteria quote this rule in full, as a source's quote is matched
    (`leaks.normalise`)."""
    from .catalogue import load_catalogue
    from .leaks import normalise

    wanted = normalise(rule)
    try:
        catalogue = load_catalogue(destination)
    except Exception:  # noqa: BLE001 - the change is made; this only says what to review next
        return []
    return [
        one.name
        for one in catalogue.sub_goals
        if wanted and any(wanted in normalise(text) for text in (one.reference, one.what, one.judged) if text)
    ]


def _cases_resting_on(
    destination: Path,
    *,
    rule: str = "",
    tool: str = "",
    written: RuleSource | None = None,
    rules: list[str] | None = None,
) -> list[Any]:
    """The cases that lose their ground with `rule` gone and the agent keeping `rules` (`_rests_on`), or whose solution
    or controls call `tool`; [] when the suite cannot be read."""
    from .scenario_tools import load_scenarios

    try:
        scenarios = load_scenarios(destination)
    except Exception:  # noqa: BLE001 - the change is made; this only says what to review next
        return []
    kept = [one for one in (rules or []) if one != rule]
    return [
        one
        for one in scenarios
        if ((rule or written is not None) and _rests_on(one, destination, rule=rule, written=written, rules=kept))
        or (
            tool
            and any(
                step.tool == tool
                for step in [*one.solution, *(step for control in one.negative_controls for step in control.steps)]
            )
        )
    ]


def rules_gone(contract: AgentContract) -> list[str]:
    """The rules the agent had and no longer has, as they were removed with nothing replacing them: a reworded rule's
    cases stand on its replacement (`grounding.resolve_sources`) and are reviewed against it instead."""
    held = set(contract.hard_constraints)
    succeeded = {one.previous for one in contract.amendment_records if one.kind == "rule" and one.previous}
    gone: list[str] = []
    for one in contract.amendment_records:
        if (
            one.kind == "rule"
            and one.action == "removed"
            and not one.replaced_by
            and one.key not in succeeded
            and one.text
            and one.text not in held
            and one.text not in gone
        ):
            gone.append(one.text)
    return gone


def cases_citing_rules_gone(destination: Path, scenarios: list[Any], contract: AgentContract) -> list[str]:
    """The cases, by key, that rest on a rule the agent had and no longer has, judged as a drop judges them
    (`_rests_on`): a source quoting nothing, or a rule that never existed, is not one."""
    rules = list(contract.hard_constraints)
    gone = rules_gone(contract)
    listed = []
    for one in scenarios:
        try:
            resting = any(
                _rests_on(one, destination, rule=rule, written=contract.rule_source(rule), rules=rules) for rule in gone
            )
        except Exception:  # noqa: BLE001 - a case whose sources cannot be read is not listed
            continue
        if resting:
            listed.append(one.scenario_key or one.name)
    return listed


def cases_citing(
    destination: Path,
    *,
    rule: str = "",
    tool: str = "",
    written: RuleSource | None = None,
    rules: list[str] | None = None,
) -> str:
    """The cases that rest on a rule or tool that just changed, as the rest of the reply: those whose `rule` source
    cites it (resolved as the case's sources are, `grounding.resolve_sources`, against the rule as it read), those
    citing exactly the line of the agent's material the rule was written on when that line is gone (`written`), judged
    against the rules the agent keeps (`rules`), or whose
    solution or negative controls call the tool. Nothing in their text is read for values: the builder re-reads each
    against the current rules."""
    citing = _cases_resting_on(destination, rule=rule, tool=tool, written=written, rules=rules)
    goals = _sub_goals_quoting(destination, rule) if rule else []
    said = (
        f"\n{len(goals)} sub-goal(s) quote the rule that changed in their contract or text: "
        + ", ".join(goals)
        + ". Reword each with add_sub_goal so it names the rule's value as the current rule sets it, or not at all."
        if goals
        else ""
    )
    if not citing:
        return said
    lines = [_case_texts(one) for one in citing]
    _open_review(destination, citing)
    return said + (
        f"\n{len(citing)} case(s) rest on what changed. Review each against the current rules and rewrite every "
        "text below that states what changed, with edit_scenario (`use_case` is its group's heading); a text that "
        "is right as it reads, keep with edit_scenario `reviewed` naming its field. Each one neither edited nor kept "
        "is listed after every reply, and shows Source changed until it is reviewed. Other cases may restate the old wording without citing it: search the suite for it too.\n  - "
        + "\n  - ".join(lines)
    )


def _persist(contract: AgentContract, destination: Path) -> None:
    """Save the contract the stage is holding. Its free text is cleaned in place as it is saved,
    so the stage goes on working from what was saved."""
    write_contract(contract, Path(destination), shape=_kept_shape)


def not_offered(contract: AgentContract, candidates: dict[str, set[str]]) -> list[str]:
    """For each argument, the candidate values the contract does not let the agent send.

    ``candidates`` maps an argument name to identifiers found in the world that plausibly belong
    to it. Kept as an argument rather than inferred here, because which column feeds which
    argument is knowledge about one agent, not something a schema states.
    """
    missing: list[str] = []
    for tool in contract.tools:
        for argument, values in (tool.arg_values or {}).items():
            if argument not in candidates or not isinstance(values, (list, tuple)):
                continue
            permitted = {str(value) for value in values}
            absent = sorted(candidates[argument] - permitted)
            if absent:
                missing.append(f"{tool.name}.{argument}: {', '.join(absent)}")
    return missing


def _code_says(contract: Any, spec: Any, field: str) -> str:
    """What the code and the world's data say about a field, beside the unit just recorded for it: its declared type
    and each stored column of that name, so the unit is checked against them where it is written."""
    seen = [f"the code types it {spec.arg_types[field]}"] if spec.arg_types.get(field) else []
    for collection, columns in sorted((getattr(contract, "data_schema", None) or {}).items()):
        if isinstance(columns, dict) and field in columns:
            seen.append(f"{collection}.{field} is stored as {columns[field]}")
    return f" ({'; '.join(seen)})" if seen else ""

