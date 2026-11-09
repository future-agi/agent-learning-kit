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

from .contract import AgentContract, known_modalities, validate_contract

CONTRACT = "contract.json"


def _kept_shape(written: Any, before: Any) -> Any:
    """``written`` without the empty keys ``before`` never had.

    A contract read by an older harness lacks fields added since; dumping the model fills them with their empty
    defaults, and an edit that touched one rule then also shows a change to a field nobody set.
    """
    if isinstance(written, dict) and isinstance(before, dict):
        return {
            key: _kept_shape(value, before.get(key))
            for key, value in written.items()
            if key in before or value not in ("", None, [], {})
        }
    if isinstance(written, list) and isinstance(before, list) and len(written) == len(before):
        return [_kept_shape(one, was) for one, was in zip(written, before)]
    return written


def _contract_json(contract: AgentContract, destination: Path) -> str:
    """The contract as written back after an edit: the edit, and no new empty fields."""
    written = contract.model_dump()
    try:
        before = json.loads((destination / CONTRACT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        before = None
    if isinstance(before, dict):
        written = _kept_shape(written, before)
    return json.dumps(written, indent=2, ensure_ascii=False)


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
            f"{tool_name!r} is not a tool this agent has. It has: "
            f"{', '.join(sorted(contract.tool_names()))}"
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

    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / CONTRACT).write_text(
        _contract_json(contract, destination),
        encoding="utf-8",
    )
    return True, (
        f"{tool_name}.{argument} now accepts {', '.join(fresh)}. "
        f"{len(contract.amendments)} amendment(s) recorded on the contract."
    )


def add_rule(
    contract: AgentContract, destination: Path, *, rule: str, why: str
) -> tuple[bool, str]:
    """Give the agent a rule its source did not state.

    A hard constraint is not decoration: the agent under test is told it, and the judge grades
    against it. So this is a real change to what is being tested, and like a widened argument it
    is recorded rather than blended into what was read from the source.
    """
    rule = rule.strip()
    if not rule:
        return False, "no rule given"
    if not why.strip():
        return False, "say why: an unexplained rule is indistinguishable from a guess"
    if any(rule.lower() == existing.lower() for existing in contract.hard_constraints):
        return False, f"the agent already has that rule: {rule}"

    contract.hard_constraints.append(rule)
    contract.amendments.append(f"rule added — {rule}: {why.strip()}")
    problems = validate_contract(contract)
    if problems:
        contract.hard_constraints.pop()
        contract.amendments.pop()
        return False, "the amended contract would not be valid: " + "; ".join(problems)

    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / CONTRACT).write_text(
        _contract_json(contract, destination),
        encoding="utf-8",
    )
    return True, (
        f"added. The agent now has {len(contract.hard_constraints)} rules, and this one is "
        "graded from here on."
    )


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

    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / CONTRACT).write_text(
        _contract_json(contract, destination),
        encoding="utf-8",
    )
    reached = {
        "voice": "a call placed to the agent where it is hosted, its own tools answered over a "
        "webhook",
        "chat": "a typed conversation, the agent reconstructed here from its contract",
        "browser": "a browser the agent drives",
    }[named]
    return True, f"modality is now {named}. Every scenario will be run as {reached}."


def drop_rule(
    contract: AgentContract, destination: Path, *, rule: str, why: str
) -> tuple[bool, str]:
    """Take away a rule the agent does not really have.

    Stage one can misread a comment as a constraint, and a rule nobody has is worse than a
    missing one: the agent under test is told to obey it and the judge fails it for not doing
    something it was never supposed to do.
    """
    if not why.strip():
        return False, "say why: removing a rule changes what is being graded"
    match = next(
        (
            existing
            for existing in contract.hard_constraints
            if existing.lower() == rule.strip().lower()
        ),
        None,
    ) or next(
        (
            existing
            for existing in contract.hard_constraints
            if rule.strip().lower() in existing.lower()
        ),
        None,
    )
    if match is None:
        return False, (
            "no rule like that. It has:\n  - "
            + "\n  - ".join(contract.hard_constraints)
        )
    contract.hard_constraints.remove(match)
    contract.amendments.append(f"rule removed — {match}: {why.strip()}")
    _persist(contract, destination)
    return True, f"removed. {len(contract.hard_constraints)} rules left"


def fix_tool(
    contract: AgentContract,
    destination: Path,
    *,
    tool_name: str,
    why: str,
    args: list[str] | None = None,
    arg_types: dict[str, str] | None = None,
    arg_units: dict[str, str] | None = None,
    description: str = "",
    refuses: str = "",
    effects: str = "",
    inputs_from: dict[str, str] | None = None,
    remove: bool = False,
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
            f"{tool_name!r} is not a tool this agent has. It has: "
            f"{', '.join(sorted(contract.tool_names()))}"
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
        _persist(contract, destination)
        return True, f"{tool_name} removed. {len(contract.tools)} tools left"

    changed = []
    if args is not None:
        # Values recorded against an argument that no longer exists would silently be lost, so
        # they are carried across by name and anything orphaned is said out loud.
        orphaned = sorted(set(spec.arg_values) - set(args))
        spec.args = list(args)
        spec.arg_types = {k: v for k, v in spec.arg_types.items() if k in spec.args}
        spec.arg_values = {k: v for k, v in spec.arg_values.items() if k in spec.args}
        spec.arg_units = {k: v for k, v in spec.arg_units.items() if k in spec.args}
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
    if description:
        spec.description = description
        changed.append("description updated")
    for field, value in (("refuses", refuses), ("effects", effects), ("inputs_from", inputs_from)):
        if value:
            setattr(spec, field, value)
            changed.append(f"{field} updated")
    if not changed:
        return False, (
            "nothing to change: give args, arg_types, arg_units, description, refuses, effects, inputs_from, "
            "or remove"
        )

    contract.amendments.append(f"tool corrected — {tool_name}: {why.strip()}")
    problems = validate_contract(contract)
    if problems:
        return False, "the amended contract would not be valid: " + "; ".join(problems)
    _persist(contract, destination)
    return True, f"{tool_name}: {', '.join(changed)}"


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


def _persist(contract: AgentContract, destination: Path) -> None:
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / CONTRACT).write_text(
        _contract_json(contract, destination),
        encoding="utf-8",
    )


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

