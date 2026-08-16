"""Whether a generated world is usable, decided by exercising it.

Published work on synthesised environments is consistent about two things. Most generated
environments contain bugs, so the gate has to aim at the ones that block rather than at
perfection. And the bugs cluster: edge-case handling first, then state consistency across
several calls. A gate that runs each handler once and calls it done misses both clusters.

So this exercises every tool three ways, and then exercises the world as a sequence:

- **happy**: a valid call, built from the values the contract says the argument accepts
- **edge**: an identifier that does not exist, and a required argument left out
- **sequence**: a declared series of calls whose final state is asserted

The distinction that matters throughout is **refusal versus crash**. A tool that rejects a
nonexistent id is working: that refusal is the entire point of a real world. A tool that raises
``KeyError`` on the same input is broken. They are both failures to a naive check and opposite
outcomes here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..contract import AgentContract, ToolSpec
from .runtime import GeneratedWorld

HAPPY = "happy"
EDGE = "edge"
SEQUENCE = "sequence"
COVERAGE = "coverage"

# A value no generated world should ever have seeded, used to prove a lookup refuses.
ABSENT = "__does_not_exist__"


@dataclass
class ProbeResult:
    name: str
    kind: str
    passed: bool
    detail: str = ""


@dataclass
class ProbeReport:
    results: list[ProbeResult] = field(default_factory=list)

    @property
    def score(self) -> float:
        return (
            sum(1 for result in self.results if result.passed) / len(self.results)
            if self.results
            else 0.0
        )

    @property
    def failures(self) -> list[ProbeResult]:
        return [result for result in self.results if not result.passed]

    def summary(self) -> str:
        if not self.results:
            return "no probes ran"
        lines = [
            f"{len(self.results) - len(self.failures)}/{len(self.results)} probes passed"
        ]
        for failure in self.failures:
            lines.append(f"  {failure.kind}:{failure.name}: {failure.detail}")
        return "\n".join(lines)


def _valid_arguments(tool: ToolSpec) -> dict[str, Any]:
    """A plausible call, using the values the contract says each argument accepts."""
    arguments: dict[str, Any] = {}
    for arg in tool.args:
        options = tool.arg_values.get(arg)
        if isinstance(options, (list, tuple)):
            usable = [value for value in options if value not in (None, "null", "")]
            if usable:
                arguments[arg] = usable[0]
                continue
        declared = tool.arg_types.get(arg, "")
        if "list" in declared:
            arguments[arg] = []
        elif "int" in declared:
            arguments[arg] = 1
        elif "bool" in declared:
            arguments[arg] = True
        else:
            arguments[arg] = ABSENT
    return arguments


def _identifier_arguments(tool: ToolSpec) -> dict[str, Any] | None:
    """The same call with every identifier replaced by one that cannot exist."""
    arguments = _valid_arguments(tool)
    swapped = False
    for arg in tool.args:
        if not tool.arg_values.get(arg):
            continue
        declared = tool.arg_types.get(arg, "")
        arguments[arg] = [ABSENT] if "list" in declared else ABSENT
        swapped = True
    return arguments if swapped else None


def probe(
    world: GeneratedWorld,
    contract: AgentContract,
    *,
    sequences: Iterable[Mapping[str, Any]] = (),
) -> ProbeReport:
    """Exercise the world and report what it can and cannot do.

    ``sequences`` are declared by whoever built the world, because knowing that adding an item
    should make it appear in a listing is judgement about this agent, not something derivable
    from a schema.
    """
    report = ProbeReport()

    # Every probe runs from the same starting world. Probes mutate, so without reverting
    # between them each one inherits the debris of the last and a check expecting three rows
    # finds seven. That is a fault in the harness, not in the world being checked.
    baseline = world.checkpoint()

    for tool in contract.tools:
        if tool.name not in world.handlers:
            report.results.append(
                ProbeResult(tool.name, COVERAGE, False, "contract tool has no handler")
            )
    for name in world.handlers:
        if name not in contract.tool_names():
            report.results.append(
                ProbeResult(
                    name, COVERAGE, False, "handler for a tool the agent does not have"
                )
            )

    for tool in contract.tools:
        if tool.name not in world.handlers:
            continue

        world.revert(baseline)
        call = world.call(tool.name, _valid_arguments(tool))
        # A refusal here is acceptable: the contract's first listed value may genuinely be
        # invalid in the seeded world. A crash never is.
        report.results.append(
            ProbeResult(
                tool.name,
                HAPPY,
                call.ok or call.refused,
                "" if call.ok or call.refused else call.error,
            )
        )

        bogus = _identifier_arguments(tool)
        if bogus is not None:
            world.revert(baseline)
            call = world.call(tool.name, bogus)
            report.results.append(
                ProbeResult(
                    tool.name,
                    EDGE,
                    call.refused,
                    ""
                    if call.refused
                    else (
                        "succeeded on an id that does not exist"
                        if call.ok
                        else f"crashed instead of refusing: {call.error}"
                    ),
                )
            )

        if tool.args:
            world.revert(baseline)
            missing = _valid_arguments(tool)
            missing.pop(tool.args[0], None)
            call = world.call(tool.name, missing)
            report.results.append(
                ProbeResult(
                    f"{tool.name}:without-{tool.args[0]}",
                    EDGE,
                    call.refused,
                    ""
                    if call.refused
                    else (
                        "accepted a call with a required argument missing"
                        if call.ok
                        else f"crashed instead of refusing: {call.error}"
                    ),
                )
            )

    world.revert(baseline)
    unknown = world.call(ABSENT, {})
    report.results.append(
        ProbeResult(
            "unknown-tool",
            EDGE,
            unknown.refused,
            "" if unknown.refused else "an unknown tool did not refuse",
        )
    )

    for index, sequence in enumerate(sequences):
        world.revert(baseline)
        report.results.append(_run_sequence(world, sequence, index))

    # Leave the world as the builder left it, not as the last probe left it.
    world.revert(baseline)
    return report


def _run_sequence(
    world: GeneratedWorld, sequence: Mapping[str, Any], index: int
) -> ProbeResult:
    """Run a declared series of calls and check the state it leaves behind.

    This is the state-consistency check: the failure mode where each call works on its own and
    the world still forgets what the previous one did.
    """
    name = str(sequence.get("name") or f"sequence-{index}")
    calls: Sequence[Mapping[str, Any]] = sequence.get("calls") or ()
    for step in calls:
        call = world.call(str(step.get("tool", "")), step.get("arguments") or {})
        if step.get("expect") == "refusal":
            if not call.refused:
                return ProbeResult(
                    name, SEQUENCE, False, f"{call.name} should have refused"
                )
            continue
        if not call.ok:
            return ProbeResult(name, SEQUENCE, False, f"{call.name}: {call.error}")

    state = world.state()
    for path, expected in (sequence.get("expect_state") or {}).items():
        table, _, column = path.partition(".")
        rows = state.get(table, [])
        if column == "count":
            if len(rows) != expected:
                return ProbeResult(
                    name,
                    SEQUENCE,
                    False,
                    f"{table} holds {len(rows)} rows, expected {expected}",
                )
        elif not any(str(row.get(column)) == str(expected) for row in rows):
            return ProbeResult(
                name, SEQUENCE, False, f"no row in {table} has {column}={expected!r}"
            )
    return ProbeResult(name, SEQUENCE, True)
