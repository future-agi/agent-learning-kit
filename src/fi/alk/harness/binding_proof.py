"""Binding proofs (`environment-releases.md` §2.4.3).

A `tool_routed` binding is used for runs only once it is shown, on PostgreSQL and through the tool
server's own execution path, that the agent's own reference trajectory reaches every scenario's
expected world, and that the binding does not reach it for the agent: doing nothing, skipping a
state-changing call, or sending another valid call in its place must each fail the scenario's
checks, unless the same change made to the environment's own calls holds too (the profile does not
need what changed: the variant is `exempt`).

Any trajectory that solves a scenario will do, not only one that follows its reference: a
scenario whose authored routes use a tool the binding does not reach is still proved when the
supplied trajectory (written by the builder in the agent's own tools) passes the same outcome
and adversarial controls. Only a scenario with no route open to the binding and no trajectory
that solves it is `unscorable`; the proved scenarios are the only ones a run under the binding may
select (`unscored`, which `compose_bundle` enforces).

    python -m fi.alk.harness.binding_proof --environment DIR --suite DIR --binding FILE \
        --trajectories FILE --postgres-dsn DSN --out FILE

`--binding` is a sealed binding document (`seal_agent_binding`): the proof names its digest, and
the environment it validates against is the one given. The command writes the proof and exits 0
when it passed, 3 when it did not, and 1 on invalid input.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
)

from .bindings import (
    AgentTool,
    MappedToolBinding,
    Step,
    _nodes,
    run_bound_call,
)
from .json_schemas import value_problems
from .releases import (
    AgentBindingV1,
    BindingMode,
    DigestRef,
    PostgresProof,
    ReleaseError,
    _check_digest,
    check_tool_routed,
    load_agent_binding,
    load_environment_release,
    load_suite_release,
    object_digest,
)

BINDING_PROOF_SCHEMA = "futureagi.binding-proof.v1"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TrajectoryCall(_Strict):
    tool: str
    arguments: JsonValue = Field(default_factory=dict)


_TRAJECTORIES = TypeAdapter(dict[str, list[TrajectoryCall]])


class ScenarioBindingProof(_Strict):
    status: Literal["proved", "failed", "unscorable"]
    reason: str = ""
    adversarial: dict[str, Literal["failed", "held", "exempt"]] = Field(
        default_factory=dict
    )


class BindingProofV1(_Strict):
    schema_version: Literal["futureagi.binding-proof.v1"] = BINDING_PROOF_SCHEMA
    binding: DigestRef
    suite: DigestRef
    environment: DigestRef
    scenarios: dict[str, ScenarioBindingProof]
    passed: bool


# --- another valid call in a call's place ---------------------------------------------------------

_Path = tuple[Any, ...]  # into JSON: keys and indexes
_MISSING: Any = object()
# How far into a value that only takes a certain form (a date, a reference number) it is changed.
_CHARACTERS = 32


def _same(one: Any, other: Any) -> bool:
    """The same JSON: 1 is not true."""
    return json.dumps(one, sort_keys=True, default=str) == json.dumps(
        other, sort_keys=True, default=str
    )


def _leaves(value: Any, path: _Path = ()) -> Iterator[tuple[_Path, Any]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from _leaves(item, (*path, key))
    elif isinstance(value, list):
        for at, item in enumerate(value):
            yield from _leaves(item, (*path, at))
    elif value is not None:
        yield path, value


def _at(value: Any, path: _Path) -> Any:
    for step in path:
        try:
            value = value[step]
        except (KeyError, IndexError, TypeError):
            return _MISSING
    return value


def _put(value: Any, path: _Path, leaf: Any) -> Any:
    if not path:
        return leaf
    copied = list(value) if isinstance(value, list) else dict(value)
    copied[path[0]] = _put(value[path[0]], path[1:], leaf)
    return copied


_NAMING = frozenset(
    {
        "enum",
        "examples",
        "const",
        "default",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
    }
)


def _named(schema: Any) -> Iterator[Any]:
    """The values a schema names anywhere in it: what an argument it constrains may be."""
    if isinstance(schema, Mapping):
        for keyword, inner in schema.items():
            if keyword in _NAMING:
                yield from inner if isinstance(inner, list) else [inner]
            yield from _named(inner)
    elif isinstance(schema, list):
        for inner in schema:
            yield from _named(inner)


def _shifted(character: str, by: int) -> str:
    for first, size in (("0", 10), ("a", 26), ("A", 26)):
        if 0 <= (at := ord(character) - ord(first)) < size:
            return chr(ord(first) + (at + by) % size)
    return character


def _others(leaf: Any, index: int, named: Sequence[Any]) -> Iterator[Any]:
    """Values to try in a leaf's place, the furthest from it first: a string nobody sent, a number
    7 away, one the schemas or the mapping name, and the same value a character or a step off."""
    if isinstance(leaf, bool):
        yield not leaf
    elif isinstance(leaf, int | float):
        numbers = [
            one
            for one in named
            if isinstance(one, int | float) and not isinstance(one, bool)
        ]
        for by in (7, 1, *numbers):
            yield leaf + by
            yield leaf - by
        yield from numbers
        yield leaf * 2
        yield leaf // 2 if isinstance(leaf, int) else leaf / 2
    elif isinstance(leaf, str):
        yield f"zz-perturbed-{index}"
        yield "".join(_shifted(one, 1) for one in leaf)
        yield from (one for one in named if isinstance(one, str))
        yield from ("true", "false")
        for at in reversed(range(max(0, len(leaf) - _CHARACTERS), len(leaf))):
            for by in (1, -1):
                yield leaf[:at] + _shifted(leaf[at], by) + leaf[at + 1 :]


def perturb(
    value: Any,
    leaves: Iterable[tuple[_Path, Any]],
    index: int,
    named: Sequence[Any],
    comes_to: Callable[[Any], Any],
) -> tuple[Any, list[_Path], list[_Path]]:
    """`value` with every one of `leaves` changed that can be, and the paths of the two kinds that
    did not count: those with no other value at all, and those whose other values change nothing.

    `comes_to` is what a candidate would come to, None when it is refused. A refused candidate is
    never used: a call turned away before anything runs says nothing about what the binding does
    with the arguments of one that is not. Every leaf that can be is changed, so a leaf no
    other value is valid for keeps its own and does not hide the rest."""
    current = comes_to(value)
    kept: list[_Path] = []
    ignored: list[_Path] = []
    for path, leaf in leaves:
        same_effect = _MISSING
        for other in _others(leaf, index, named):
            if _same(other, leaf):
                continue
            candidate = _put(value, path, other)
            if (result := comes_to(candidate)) is None:
                continue
            if not _same(result, current):
                value, current = candidate, result
                break
            if same_effect is _MISSING:
                same_effect = candidate
        else:
            if same_effect is _MISSING:
                kept.append(path)
            else:
                value = same_effect
                ignored.append(path)
    return value, kept, ignored


Counterpart = tuple[AgentBindingV1, list[tuple[str, Any]]]
Variant = tuple[str, list[tuple[str, Any]], Counterpart | None]


def _replay(
    binding: AgentBindingV1,
    outcomes: Sequence[Any],
    position: int,
    arguments: Sequence[Mapping[str, Any]] | None,
) -> Counterpart:
    """The proved trajectory's committed agent calls, replayed in the environment's own
    vocabulary, with call `position` left out (`arguments` None) or its operations sent
    `arguments`.

    Each committed agent call becomes one replay call whose steps are its recorded environment
    operations with constant arguments, so it runs through `WorldTools.call` in one
    transaction: a refused later step rolls the earlier ones back exactly as it did for the agent.
    """
    agent_tools: list[AgentTool] = []
    entries: list[MappedToolBinding] = []
    calls: list[tuple[str, Any]] = []
    for at, outcome in enumerate(outcomes):
        if outcome.status != "ok" or (at == position and arguments is None):
            continue
        name = f"call_{at + 1}"
        sent = [operation.arguments for operation in outcome.operations]
        if at == position:
            sent = list(arguments or ())
        steps = [
            {
                "id": f"op_{number}",
                "tool": operation.tool,
                "arguments": {key: {"const": value} for key, value in one.items()},
            }
            for number, (operation, one) in enumerate(
                zip(outcome.operations, sent, strict=True), start=1
            )
        ]
        agent_tools.append(
            AgentTool(
                name=name,
                description="",
                parameters={"type": "object"},
                admission="routed",
            )
        )
        entries.append(
            MappedToolBinding.model_validate({"agent_tool": name, "steps": steps})
        )
        calls.append((name, {}))
    replay = binding.model_copy(
        update={
            "agent_tools": agent_tools,
            "tools": entries,
            "fixtures": {},
            "aliases": {},
        }
    )
    return replay, calls


def _perturbed(
    binding: AgentBindingV1,
    agent_tool: str,
    sent: Mapping[str, Any],
    operations: Sequence[Any],
    definitions: Sequence[Any],
    index: int,
) -> Iterator[tuple[str, Any, list[Mapping[str, Any]]]]:
    """`perturb:i`, and the call's operations changed the same way in the environment's own
    vocabulary: as the changed call makes them, and whatever it left as it was changed too, which
    is what the binding settled rather than the agent. An argument read off one of the agent's
    that has no other valid value stays: there was nothing else for the agent to send.

    Then `perturb:i:argument` for an argument the mapping ignores although the operations hold
    that very value, from the binding: another argument may matter and hide it from `perturb:i`.

    Both sides stay within their schemas. What the environment's refuses, it refuses before the
    handler and whoever chose the target, so there too it would be no evidence."""
    entry = next(
        one
        for one in binding.tools
        if isinstance(one, MappedToolBinding) and one.agent_tool == agent_tool
    )
    schema = next(
        one.parameters for one in binding.agent_tools if one.name == agent_tool
    )
    environment = {one.name: one.parameters for one in definitions}
    made_of = list(zip(operations, entry.steps, strict=True))
    sent = {name: value for name, value in sent.items() if value is not None}
    named = [*_named(schema)]
    for step in entry.steps:
        named += _named(environment[step.tool])
        for value in step.arguments.values():
            for node in _nodes(value):
                named += node.map_ or ()
                named += binding.aliases.get(node.alias or "", ())

    def comes_to(arguments: Any) -> list[dict[str, Any]] | None:
        """The operations the mapping makes of `arguments`, from the results the world gave."""
        if value_problems(schema, arguments, formats=True):
            return None
        results = iter([one.world_result for one in operations])
        outcome = run_bound_call(
            binding,
            agent_tool,
            arguments,
            run_operation=lambda _tool, _arguments: next(results),
            environment_tools=definitions,
        )
        if outcome.status != "ok":
            return None
        return [one.arguments for one in outcome.operations]

    def operation(
        tool: str, arguments: Any, leaves: Iterable[tuple[_Path, Any]]
    ) -> Any:
        rules = environment[tool]

        def accepted(one: Any) -> Any:
            return None if value_problems(rules, one, formats=True) else one

        return perturb(arguments, leaves, index, [*_named(rules)], accepted)[0]

    changed, kept, ignored = perturb(sent, _leaves(sent), index, named, comes_to)

    def settled(step: Step, path: _Path) -> bool:
        value = step.arguments[path[0]]
        return value.form == "arg" and (value.arg, *path[1:]) in kept

    after = comes_to(changed) or [one.arguments for one in operations]
    yield (
        f"perturb:{index}",
        changed,
        [
            operation(
                step.tool,
                made,
                [
                    (path, leaf)
                    for path, leaf in _leaves(made)
                    if _same(_at(one.arguments, path), leaf) and not settled(step, path)
                ],
            )
            for made, (one, step) in zip(after, made_of, strict=True)
        ],
    )
    for path in ignored:
        name = str(_at(sent, path)).casefold()
        others = [
            operation(
                step.tool,
                one.arguments,
                [
                    (at, leaf)
                    for at, leaf in _leaves(one.arguments)
                    if str(leaf).casefold() == name
                    and not isinstance(leaf, bool)
                    and (
                        settled(step, at)
                        or {part.form for part in step.arguments[at[0]].leaves()}
                        <= {"fixture", "const"}
                    )
                ],
            )
            for one, step in made_of
        ]
        if not _same(others, [one.arguments for one in operations]):
            yield (
                f"perturb:{index}:{'.'.join(map(str, path))}",
                _put(sent, path, _at(changed, path)),
                others,
            )


def _variants(
    binding: AgentBindingV1,
    calls: Sequence[tuple[str, Any]],
    outcomes: Sequence[Any],
    mutates: set[str],
    definitions: Sequence[Any],
) -> Iterator[Variant]:
    """`nothing`, then `skip:i` and `perturb:i` (1-based) for every call that can change state,
    each with its canonical counterpart: the same change made to the environment operations the
    proved trajectory committed (none for `nothing`; the suite proof already shows an untouched
    world fails).

    A call the reference trajectory itself saw refused changed nothing, so removing or changing
    it cannot fail the scenario whatever the binding does; it gets no variants.
    """
    yield "nothing", [], None
    for position, (tool, arguments) in enumerate(calls):
        if tool not in mutates or outcomes[position].status != "ok":
            continue
        index = position + 1
        yield (
            f"skip:{index}",
            [*calls[:position], *calls[index:]],
            _replay(binding, outcomes, position, None),
        )
        for name, changed, operations in _perturbed(
            binding,
            tool,
            arguments,
            outcomes[position].operations,
            definitions,
            index,
        ):
            yield (
                name,
                [*calls[:position], (tool, changed), *calls[index:]],
                _replay(binding, outcomes, position, operations),
            )


def _closed(scenario: Any, reached: set[str]) -> str:
    """Why none of the scenario's authored routes (its reference solution and alternatives) is
    open to the binding; empty when one is."""
    routes = [scenario.solution, *(getattr(scenario, "alternatives", None) or ())]
    missing = [sorted({step.tool for step in route} - reached) for route in routes]
    if not all(missing):
        return ""
    return (
        f"the reference solution uses {', '.join(missing[0])}, which no mapped step reaches"
        + (
            f" (nor is any of its {len(routes) - 1} alternatives reached)"
            if len(routes) > 1
            else ""
        )
    )


def _why(attempt: Any) -> str:
    if not attempt.ready.ok:
        return attempt.ready.said
    return "; ".join([*attempt.faults, *attempt.failed]) or "no deterministic check"


def prove_binding(
    environment_root: str | Path,
    suite_root: str | Path,
    binding: AgentBindingV1,
    trajectories: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    postgres_dsn: str,
) -> BindingProofV1:
    """Prove a sealed `tool_routed` binding against a sealed suite of the environment it targets.

    Raises `ReleaseError` only for inputs that cannot be proved at all; a scenario that fails, or
    an adversarial variant that holds, is reported and makes `passed` false.
    """
    from .folder import read_folder

    environment_root, suite_root = Path(environment_root), Path(suite_root)
    environment = load_environment_release(environment_root)
    suite = load_suite_release(suite_root)
    problems = []
    if binding.mode is not BindingMode.TOOL_ROUTED:
        problems.append(f"a {binding.mode.value} binding has nothing to prove")
    if binding.environment.digest != environment.digest:
        problems.append("the binding targets another environment")
    if suite.environment.digest != environment.digest:
        problems.append("the suite was proved against another environment")
    try:
        parsed = _TRAJECTORIES.validate_python(dict(trajectories))
    except ValidationError as exc:
        problems.append(f"trajectories: {exc}")
        parsed = {}
    keys = {entry.key for entry in suite.scenarios}
    if unknown := sorted(set(parsed) - keys):
        problems.append(
            f"trajectories for scenarios not in the suite: {', '.join(unknown)}"
        )
    if problems:
        raise ReleaseError("binding_proof_invalid", "; ".join(problems))
    _check_digest(binding, "binding_digest_mismatch")

    scenarios: dict[str, ScenarioBindingProof] = {}
    with PostgresProof(
        suite_root, environment_root, suite.policy_profile, postgres_dsn
    ) as proof:
        # A binding may map the agent onto an attached server's tool, as a run serves it.
        definitions = [*proof.environment_tools, *proof.attached_definitions()]
        check_tool_routed(binding, definitions)
        # An attached server's tool changes state unless its server marks it read-only, and a
        # call routed onto it is skipped and perturbed like one onto the environment's own.
        changing = {tool.name for tool in definitions if tool.mutates_state}
        mapped = [
            entry for entry in binding.tools if isinstance(entry, MappedToolBinding)
        ]
        reached = {step.tool for entry in mapped for step in entry.steps}
        mutates = {
            entry.agent_tool
            for entry in mapped
            if any(step.tool in changing for step in entry.steps)
        }
        for entry in suite.scenarios:
            scenario = read_folder(suite_root, Path(entry.path).name)
            closed = _closed(scenario, reached)
            if entry.key not in parsed:
                scenarios[entry.key] = ScenarioBindingProof(
                    status="unscorable" if closed else "failed",
                    reason=closed or "no trajectory",
                )
                continue
            calls = [(call.tool, call.arguments) for call in parsed[entry.key]]
            solved = proof.attempt(scenario, calls, binding)
            # A call the binding refuses as invalid (the agent's schema does not take it, or it
            # cannot be translated) is not one the agent can make: the scenario is not proved by
            # it, whatever the checks say.
            if invalid := [
                f"{tool}: {outcome.error}"
                for (tool, _arguments), outcome in zip(
                    calls, solved.outcomes, strict=False
                )
                if outcome.status == "invalid"
            ]:
                scenarios[entry.key] = ScenarioBindingProof(
                    status="failed",
                    reason="the trajectory makes calls the agent cannot make: "
                    + "; ".join(invalid),
                )
                continue
            if not solved.holds:
                # With no authored route open to the binding, the supplied trajectory was the
                # last candidate: the scenario is not scored rather than the binding failed.
                scenarios[entry.key] = ScenarioBindingProof(
                    status="unscorable" if closed else "failed",
                    reason=f"{closed}, and the supplied trajectory does not solve it: "
                    f"{_why(solved)}"
                    if closed
                    else _why(solved),
                )
                continue
            adversarial: dict[str, Any] = {}
            for name, variant, counterpart in _variants(
                binding, calls, solved.outcomes, mutates, definitions
            ):
                if not proof.attempt(scenario, variant, binding).checks_hold:
                    adversarial[name] = "failed"
                # A variant that holds shows the binding helps only if the same change, made in
                # the environment's own vocabulary, would fail; otherwise the policy profile
                # simply does not need what changed.
                elif (
                    counterpart is not None
                    and proof.attempt(
                        scenario, counterpart[1], counterpart[0]
                    ).checks_hold
                ):
                    adversarial[name] = "exempt"
                else:
                    adversarial[name] = "held"
            held = [name for name, outcome in adversarial.items() if outcome == "held"]
            exempt = [
                name for name, outcome in adversarial.items() if outcome == "exempt"
            ]
            # A held variant means the binding, not the agent, reached the expected world; the
            # scenario then measures nothing about the agent and is not used for runs.
            scenarios[entry.key] = ScenarioBindingProof(
                status="failed" if held else "proved",
                reason=f"adversarial variants held: {', '.join(held)}"
                if held
                else f"exempt: {', '.join(exempt)} (not needed under this profile: the "
                "canonical calls hold with the same change)"
                if exempt
                else "",
                adversarial=adversarial,
            )
    return BindingProofV1(
        binding=DigestRef(digest=binding.digest),
        suite=DigestRef(digest=suite.digest),
        environment=DigestRef(digest=environment.digest),
        scenarios=scenarios,
        passed=all(item.status != "failed" for item in scenarios.values())
        and not any(
            outcome == "held"
            for item in scenarios.values()
            for outcome in item.adversarial.values()
        ),
    )


def load_binding_proof(path: str | Path) -> BindingProofV1:
    try:
        return BindingProofV1.model_validate_json(
            Path(path).read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise ReleaseError("binding_proof_invalid", f"{path}: {exc}") from exc


def proof_digest(proof: BindingProofV1) -> str:
    """The proof's content address: every status, reason and reference in it, in canonical JSON.
    The proof names what it is of but nothing names it, so whoever keeps it records this beside
    the adaptation, and a run's manifest freezes it (`RunManifestV1.binding_proof`)."""
    return object_digest(proof)


def unscored(
    binding: AgentBindingV1,
    suite: Any,
    environment_digest: str,
    proof: BindingProofV1 | None,
    *,
    anchored: str | None,
) -> dict[str, str]:
    """The scenarios of `suite` a run under `binding` must not select, with why.

    A `tool_routed` binding is scored only on the scenarios its proof against this very suite
    proved, so it needs that proof; any other binding calls the environment's tools by name and
    is scored on the whole suite. `anchored` is the proof digest the run's sealed manifest
    froze: a proof is a file anyone holding it can edit, so its statuses are read only when it
    is the proof the manifest names.
    """
    if binding.mode is not BindingMode.TOOL_ROUTED:
        return {}
    if proof is None:
        raise ReleaseError(
            "binding_unproved",
            "a tool_routed binding runs only with its binding proof against the suite",
        )
    if anchored is None:
        raise ReleaseError(
            "binding_unproved",
            "the run manifest names no binding proof digest, so the proof cannot be trusted",
        )
    if (computed := proof_digest(proof)) != anchored:
        raise ReleaseError(
            "binding_proof_digest_mismatch",
            f"the run manifest froze {anchored}, the proof delivered is {computed}",
        )
    problems = []
    if proof.binding.digest != binding.digest:
        problems.append("the binding proof is of another binding")
    if proof.suite.digest != suite.digest:
        problems.append("the binding proof is against another suite")
    if proof.environment.digest != environment_digest:
        problems.append("the binding proof is against another environment")
    if not proof.passed:
        problems.append("the binding proof did not pass")
    if problems:
        raise ReleaseError("binding_unproved", "; ".join(problems))
    excluded: dict[str, str] = {}
    for entry in suite.scenarios:
        verdict = proof.scenarios.get(entry.key)
        if verdict is None:
            excluded[entry.key] = "the binding proof does not cover it"
        elif verdict.status != "proved":
            excluded[entry.key] = verdict.reason or verdict.status
    return excluded


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fi.alk.harness.binding_proof")
    parser.add_argument("--environment", required=True)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--binding", required=True, help="a sealed binding.json")
    parser.add_argument("--trajectories", required=True)
    parser.add_argument("--postgres-dsn", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        try:
            trajectories = json.loads(
                Path(args.trajectories).read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise ReleaseError("binding_proof_invalid", f"trajectories: {exc}") from exc
        if not isinstance(trajectories, dict):
            raise ReleaseError(
                "binding_proof_invalid", "trajectories must be a JSON object"
            )
        result = prove_binding(
            args.environment,
            args.suite,
            load_agent_binding(args.binding),
            trajectories,
            postgres_dsn=args.postgres_dsn,
        )
    except ReleaseError as exc:
        print(json.dumps({"error": exc.code, "detail": exc.detail}), file=sys.stderr)
        return 1
    Path(args.out).write_text(
        json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "passed": result.passed,
                "scenarios": {
                    key: item.status for key, item in result.scenarios.items()
                },
            }
        )
    )
    return 0 if result.passed else 3


if __name__ == "__main__":
    raise SystemExit(main())
