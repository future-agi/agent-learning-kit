"""Trajectories derived from reference solutions: the harness's half of a binding proof.

A binding proof (`binding_proof.py`) replays each scenario in the agent's own vocabulary, from a
trajectory `[{tool, arguments}]` in that vocabulary. The clinic's eight trajectories were written
by the builder; a vertical with a hundred scenarios cannot wait for that. Where a binding entry
inverts one environment operation (one step, each argument renamed from one of the agent's,
perhaps converted, mapped or aliased, the rest fixed) the agent's call is determined by the
reference step, so code writes it. What the inversion cannot settle (an entry of several steps, a
joined or split value, a conversion with many preimages, an argument the entry never sends) is
left to the builder, with the reason. A scenario's authored alternatives (`Scenario.alternatives`,
other routes its instructions allow) are tried in turn when the reference uses a tool no entry
reaches; a scenario none of whose routes is reached needs no trajectory from the harness: unless
the builder writes one in the agent's own tools, the proof reports it unscorable.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .bindings import CONVERSIONS, MappedToolBinding, Value
from .releases import AgentBindingV1, BindingMode, ToolDefinition

_PLAIN_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class Derivation:
    # scenario key -> the agent's calls, in order
    trajectories: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # scenario key -> why no trajectory could be derived (the builder writes these)
    undecided: dict[str, str] = field(default_factory=dict)
    # scenario key -> the environment tools its reference uses that no entry reaches, when no
    # authored alternative is reached either
    unreached: dict[str, str] = field(default_factory=dict)


class _Undecided(Exception):
    pass


def derive_trajectories(
    binding: AgentBindingV1,
    scenarios: Iterable[
        tuple[str, Sequence[Any]] | tuple[str, Sequence[Any], Sequence[Sequence[Any]]]
    ],
    environment_tools: Sequence[ToolDefinition],
) -> Derivation:
    """`scenarios` are `(key, reference steps)` or `(key, reference steps, alternatives)`, each
    step carrying `tool` and `arguments` in the environment's vocabulary; the first route whose
    every call inverts gives the trajectory. Only a `tool_routed` binding has anything to derive."""
    derivation = Derivation()
    if binding.mode is not BindingMode.TOOL_ROUTED:
        return derivation
    tools = {tool.name: tool for tool in environment_tools}
    agent_tools = {tool.name: tool for tool in binding.agent_tools}
    single = [
        entry
        for entry in binding.tools
        if isinstance(entry, MappedToolBinding) and len(entry.steps) == 1
    ]
    reached = {
        step.tool
        for entry in binding.tools
        if isinstance(entry, MappedToolBinding)
        for step in entry.steps
    }
    for key, steps, *alternatives in scenarios:
        routes = [steps, *(alternatives[0] if alternatives else ())]
        open_routes = [
            route for route in routes if {step.tool for step in route} <= reached
        ]
        if not open_routes:
            derivation.unreached[key] = ", ".join(
                sorted({step.tool for step in steps} - reached)
            )
            continue
        reasons: list[str] = []
        for route in open_routes:
            try:
                derivation.trajectories[key] = [
                    _agent_call(
                        step.tool,
                        dict(step.arguments or {}),
                        [entry for entry in single if entry.steps[0].tool == step.tool],
                        tools[step.tool],
                        agent_tools,
                        binding,
                    )
                    for step in route
                ]
                break
            except _Undecided as exc:
                reasons.append(str(exc))
        else:
            derivation.undecided[key] = (
                reasons[0]
                if len(reasons) == 1
                else "; ".join(
                    f"route {number}: {reason}"
                    for number, reason in enumerate(reasons, start=1)
                )
            )
    return derivation


def _agent_call(
    tool: str,
    reference: dict[str, Any],
    entries: Sequence[MappedToolBinding],
    definition: ToolDefinition,
    agent_tools: Mapping[str, Any],
    binding: AgentBindingV1,
) -> dict[str, Any]:
    if not entries:
        raise _Undecided(
            f"{tool} is reached only through an entry of several steps; write the call by hand"
        )
    reasons = []
    for entry in entries:
        try:
            return {
                "tool": entry.agent_tool,
                "arguments": _invert(
                    entry, reference, definition, agent_tools, binding
                ),
            }
        except _Undecided as exc:
            reasons.append(f"{entry.agent_tool}: {exc}")
    raise _Undecided(f"{tool}: " + "; ".join(reasons))


def _invert(
    entry: MappedToolBinding,
    reference: dict[str, Any],
    definition: ToolDefinition,
    agent_tools: Mapping[str, Any],
    binding: AgentBindingV1,
) -> dict[str, Any]:
    step = entry.steps[0]
    produced: dict[str, Any] = {}
    sent: set[str] = set()
    for name, value in step.arguments.items():
        if name not in reference:
            # The entry would send an argument the reference did not pass. An agent argument is
            # simply left out of the call; a fixed value the entry always sends changes the
            # replay, so it is the builder's to decide.
            if value.form == "arg":
                continue
            raise _Undecided(
                f"always sends {step.tool}.{name}, which the reference does not pass"
            )
        sent.add(name)
        preimage = _preimage(value, reference[name], binding, f"{step.tool}.{name}")
        if preimage is None:
            continue
        agent_argument, agent_value = preimage
        if agent_argument in produced and produced[agent_argument] != agent_value:
            raise _Undecided(
                f"{agent_argument} would have to be both {produced[agent_argument]!r} "
                f"and {agent_value!r}"
            )
        produced[agent_argument] = agent_value
    if missing := sorted(set(reference) - sent):
        raise _Undecided(
            f"never sends {step.tool}.{', '.join(missing)}, which the reference passes"
        )
    schema = (agent_tools.get(entry.agent_tool) or {}).parameters
    required = set((schema or {}).get("required") or [])
    if unmet := sorted(required - set(produced)):
        raise _Undecided(
            f"the agent's {entry.agent_tool} requires {', '.join(unmet)}, which no "
            "environment argument determines"
        )
    return produced


def _preimage(
    value: Value, environment_value: Any, binding: AgentBindingV1, where: str
) -> tuple[str, Any] | None:
    """The agent argument and value that make `value` produce `environment_value`; `None` for a
    fixed value that already does; `_Undecided` when there is no unique answer."""
    form = value.form
    if form == "const":
        if value.const == environment_value:
            return None
        raise _Undecided(
            f"fixes {where} to {value.const!r}, the reference passes {environment_value!r}"
        )
    if form == "fixture":
        fixed = binding.fixtures.get(value.fixture or "")
        if fixed == environment_value:
            return None
        raise _Undecided(
            f"fixes {where} to fixture {value.fixture}, the reference passes {environment_value!r}"
        )
    if form != "arg":
        raise _Undecided(f"builds {where} from a {form}, which has no inverse")
    path = value.arg or ""
    if not _PLAIN_NAME.match(path):
        raise _Undecided(
            f"reads {where} through the path {path!r}, which has no inverse"
        )
    current: Any = environment_value
    # Modifiers run convert, then map, then alias; undo them in the opposite order.
    if value.alias is not None:
        current = _unique_key(
            binding.aliases.get(value.alias) or {},
            current,
            f"alias {value.alias}",
            where,
        )
    if value.map_ is not None:
        current = _unique_key(value.map_, current, "map", where)
    if value.convert is not None:
        try:
            again = CONVERSIONS[value.convert](current)
        except Exception as exc:
            raise _Undecided(
                f"convert {value.convert} does not take {current!r} back for {where}: {exc}"
            ) from exc
        if again != current:
            raise _Undecided(
                f"convert {value.convert} has no unique inverse for {where}={current!r}"
            )
    return path, current


def _unique_key(table: Mapping[str, Any], value: Any, what: str, where: str) -> str:
    keys = [
        key for key, item in table.items() if item == value or str(item) == str(value)
    ]
    if len(keys) != 1:
        raise _Undecided(
            f"{what} has {len(keys)} entries for {where}={value!r}, so no unique inverse"
        )
    return keys[0]
