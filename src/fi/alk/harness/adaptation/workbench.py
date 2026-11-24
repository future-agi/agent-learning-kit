"""What the binding session works with, and every gate around it (`environment-releases.md` §7.5).

The session drafts the binding; code decides the rest: what the agent is (inspected, never drafted),
whether a binding is valid, whether it proves, which policy conflicts the person sees, and sealing.
The test suite is never changed: a scenario the binding cannot reach is reported unscorable.

State lives in the output directory, so a session that is reopened carries on where it stopped.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..binding_trajectories import derive_trajectories
from ..bindings import UnsupportedToolBinding, agent_call_problems
from ..catalogue import load_catalogue
from ..folder import read_folder
from ..releases import (
    BindingMode,
    ReleaseError,
    ScenarioOrigin,
    load_environment_release,
    load_policy,
    load_servers,
    load_suite_release,
    load_tools,
    seal_suite_release,
)
from .errors import AdaptationFailure
from .profile import (
    AgentProfile,
    argument_mismatches,
    build_profile,
    identity_mapping,
    seal_binding,
    select_mode,
)
from .proofs import Candidate, binding_verdicts, prove_binding_on, write_suite_tree

RESULT_SCHEMA = "futureagi.adaptation-result.v1"
RESULT = "result.json"
PROFILE = "profile.json"
BINDING = "binding.json"
BINDING_PROOF = "binding-proof.json"
# The agent's proved calls per scenario: a composition's suite edits re-prove the binding with them.
TRAJECTORIES = "trajectories.json"
SUITE = "suite"
STATE = "state.json"
INPUTS = "inputs.json"
SIMULATOR_PROMPT = "simulator_prompt.md"
_ROW_LIMIT = 200
_YES = {"yes", "y", "true", "approve", "approved", "ok"}

# The person's answer to the approval question, given the question and its options.
Approve = Callable[[str, list[str]], Awaitable[str]]


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def frozen_pairing(
    pairing: Any, environment_tools: Sequence[Any], agent_tools: Sequence[str]
) -> dict[str, str | None]:
    """The pairing the person reviewed (environment tool -> agent tool, or null), complete: every
    environment tool is in it, one it does not name paired with nothing. A name that is not an
    environment tool, or a value that is not one of the agent's admitted tools, is refused, so
    what is bound is the design the person saw and nothing else."""
    if not isinstance(pairing, Mapping):
        raise AdaptationFailure(
            "adaptation_pairing_invalid",
            "the pairing must be an object of environment tool -> agent tool or null",
        )
    environment = [tool.name for tool in environment_tools]
    admitted = set(agent_tools)
    problems = [
        f"{name!r} is not an environment tool"
        for name in pairing
        if name not in environment
    ]
    problems += [
        f"{name}: {value!r} is not one of the agent's admitted tools"
        for name, value in pairing.items()
        if value is not None and not (isinstance(value, str) and value in admitted)
    ]
    if problems:
        raise AdaptationFailure("adaptation_pairing_invalid", "; ".join(problems))
    return {name: pairing.get(name) for name in environment}


def pairing_problems(
    binding: Any, pairing: Mapping[str, str | None], environment_tools: Sequence[Any]
) -> list[str]:
    """Why `binding` is not the reviewed `pairing`; empty when it is. An agent tool paired with
    an environment tool reaches it, changes state through no environment tool it was not paired
    with (a step that only reads may look a value up), and an agent tool paired with none is
    unsupported."""
    wanted: dict[str, set[str]] = {}
    for environment_tool, agent_tool in pairing.items():
        if agent_tool:
            wanted.setdefault(agent_tool, set()).add(environment_tool)
    changing = {tool.name for tool in environment_tools if tool.mutates_state}
    problems: list[str] = []
    for entry in binding.tools:
        targets = wanted.get(entry.agent_tool, set())
        if isinstance(entry, UnsupportedToolBinding):
            if targets:
                problems.append(
                    f"{entry.agent_tool}: the reviewed pairing maps it onto "
                    f"{', '.join(sorted(targets))}, the binding marks it unsupported"
                )
            continue
        if not targets:
            problems.append(
                f"{entry.agent_tool}: the reviewed pairing gives it no environment tool, so "
                "it must be unsupported"
            )
            continue
        reached = {step.tool for step in entry.steps}
        if missing := sorted(targets - reached):
            problems.append(
                f"{entry.agent_tool}: paired with {', '.join(missing)}, which no step calls"
            )
        if extra := sorted((reached & changing) - targets):
            problems.append(
                f"{entry.agent_tool}: a step changes state through {', '.join(extra)}, which "
                "the reviewed pairing does not give it"
            )
    return problems


def failed_result(code: str, message: str) -> dict[str, Any]:
    return {
        "schema_version": RESULT_SCHEMA,
        "status": "failed",
        "failure": {"code": code, "message": message},
        "environment": [],
        "suite": None,
        "binding": None,
        "binding_proof": None,
        "alignment": None,
    }


def profile_from_document(body: Mapping[str, Any]) -> AgentProfile:
    """The profile `AgentProfile.document()` saved; admission is recomputed, deterministically."""
    return build_profile(
        dict(body.get("profile") or {}),
        connector=str(body.get("connector") or ""),
        agent=body.get("agent"),
    )


@dataclass
class BindingDraft:
    mapping: dict[str, Any]
    trajectories: dict[str, list[dict[str, Any]]]
    extension_candidates: list[dict[str, str]] = field(default_factory=list)


def parse_binding_draft(body: Mapping[str, Any]) -> BindingDraft:
    """`trajectories` may be left out or partial: code derives one for every scenario whose
    reference solution the binding inverts (`binding_trajectories`), and the builder writes the rest."""
    mapping = body.get("binding")
    trajectories = body.get("trajectories")
    if trajectories is None:
        trajectories = {}
    candidates = body.get("extension_candidates") or []
    if not isinstance(mapping, Mapping) or not isinstance(trajectories, Mapping):
        raise TypeError(
            "a draft needs a `binding` object and, if given, a `trajectories` object"
        )
    if not isinstance(candidates, list) or not all(
        isinstance(one, Mapping) and {"agent_tool", "reason"} <= set(one)
        for one in candidates
    ):
        raise TypeError("extension_candidates must be a list of {agent_tool, reason}")
    return BindingDraft(
        mapping={
            key: mapping[key]
            for key in ("tools", "fixtures", "aliases", "rendering")
            if key in mapping
        },
        trajectories={str(key): list(calls) for key, calls in trajectories.items()},
        extension_candidates=[
            {"agent_tool": str(one["agent_tool"]), "reason": str(one["reason"])}
            for one in candidates
        ],
    )


class Workbench:
    def __init__(
        self,
        *,
        environments: Sequence[Path],
        suite_root: Path,
        output: Path,
        postgres_dsn: str,
        profile: AgentProfile,
        finished_marker: Path | None = None,
        pairing: Mapping[str, str | None] | None = None,
    ) -> None:
        self.environments = [Path(one) for one in environments]
        self.suite_root = Path(suite_root)
        self.output = Path(output)
        self.postgres_dsn = postgres_dsn
        self.profile = profile
        self.finished_marker = finished_marker
        self.output.mkdir(parents=True, exist_ok=True)
        self._load_releases()
        write_json(self.output / PROFILE, profile.document())
        self.mode = select_mode(profile, self.environment_tools)
        # The pairing the person reviewed, when the platform sent one: every binding submitted
        # is held to it in code (`submit_binding`), whoever wrote it.
        self.pairing: dict[str, str | None] | None = None
        if pairing is not None:
            self.freeze_pairing(pairing)
            # A canonical binding serves every environment tool, so one the person left unpaired
            # is served only by a routed binding, which holds to the pairing.
            if self.mode is BindingMode.CANONICAL and any(
                name != paired for name, paired in pairing.items()
            ):
                self.mode = BindingMode.TOOL_ROUTED
        self.binding: Any = None
        self.draft: BindingDraft | None = None
        self.proof: dict[str, Any] | None = None
        self.verdicts: dict[str, Any] = {}
        self.conflicts: list[dict[str, str]] | None = None
        self.declined = ""
        self.done = False
        self._suite: tuple[Path, Any, bool] | None = None
        self.alignment: dict[str, Any] = {
            "mode": self.mode.value,
            "admission": [one.model_dump(mode="json") for one in profile.admission],
            "mapped": [],
            "unsupported": [],
            "extension_candidates": [],
            "unscorable": [],
            "conflicts": [],
        }
        if self.mode is not BindingMode.TOOL_ROUTED:
            self.binding = seal_binding(
                profile,
                self.mode,
                None,
                environment_root=self.target_root,
                environment_tools=self.environment_tools,
            )
            if self.mode is BindingMode.CANONICAL:
                admitted = {one.name for one in profile.admitted}
                self.alignment["mapped"] = [
                    {"agent_tool": one.name, "steps": [one.name]}
                    for one in profile.admitted
                ]
                # The agent calls the environment's tools by name, so a scenario whose solution
                # needs a tool it lacks (an attached MCP server's, say) cannot be passed.
                for entry in self.base_suite.scenarios:
                    scenario = read_folder(self.suite_root, Path(entry.path).name)
                    if missing := sorted(
                        {step.tool for step in scenario.solution} - admitted
                    ):
                        self.alignment["unscorable"].append(
                            {
                                "scenario_key": entry.key,
                                "reason": f"the agent has no {', '.join(missing)}",
                            }
                        )
            else:
                self.alignment["unscorable"] = [
                    {
                        "scenario_key": entry.key,
                        "reason": "the agent reaches no environment tool",
                    }
                    for entry in self.base_suite.scenarios
                ]
        self._restore()
        # An agent with some of the environment's tools, each called as-is, needs no mapping
        # written: its own tools onto themselves, so only those are served.
        self.identity = (
            self._identity() if self.mode is BindingMode.TOOL_ROUTED else None
        )
        if self.identity is not None and self.draft is None:
            self.submit_binding({"binding": self.identity})

    # --- releases -------------------------------------------------------------------------

    def _load_releases(self) -> None:
        try:
            releases = [load_environment_release(self.environments[0])]
            if len(self.environments) == 2:
                releases.append(
                    load_environment_release(
                        self.environments[1], parent_root=self.environments[0]
                    )
                )
            self.base_suite = load_suite_release(self.suite_root)
        except ReleaseError as exc:
            raise AdaptationFailure(
                "release_invalid", f"{exc.code}: {exc.detail}"
            ) from exc
        self.releases = [{"digest": one.digest, "name": one.name} for one in releases]
        self.target_root = self.environments[-1]
        self.target_digest = releases[-1].digest
        self.environment_tools = [
            *load_tools(self.target_root),
            *self._attached_tools(),
        ]
        self.policy = load_policy(self.target_root)
        self.catalogue = load_catalogue(self.suite_root)

    def _attached_tools(self) -> list[Any]:
        """The attached servers' tools as a world offers them, `<id>__<tool>` with the schema the
        server itself lists: the agent may be mapped onto them as onto the release's own."""
        if not load_servers(self.target_root):
            return []
        from ..releases import PostgresProof

        try:
            with PostgresProof(
                self.suite_root,
                self.target_root,
                self.base_suite.policy_profile,
                self.postgres_dsn,
            ) as proof:
                return proof.attached_definitions()
        except Exception as exc:  # a server that will not start fails the job
            raise AdaptationFailure(
                "release_invalid", f"attached servers: {type(exc).__name__}: {exc}"
            ) from exc

    def _suite_for_target(self) -> tuple[Path, Any, bool]:
        """The base suite on the target environment: as it is, or (on an extension) the same
        scenarios sealed against the extension, unchanged."""
        if self._suite is not None:
            return self._suite
        if self.base_suite.environment.digest == self.target_digest:
            self._suite = (self.suite_root, self.base_suite, False)
            return self._suite
        root = self.output / SUITE
        candidates = [
            Candidate(entry=entry, source=self.suite_root / entry.path)
            for entry in self.base_suite.scenarios
        ]
        write_suite_tree(
            root,
            candidates,
            self.catalogue,
            (self.suite_root / SIMULATOR_PROMPT).read_text(encoding="utf-8"),
        )
        try:
            suite = seal_suite_release(
                root,
                name=self.base_suite.name,
                environment_root=self.target_root,
                scenarios=[
                    one.entry.model_copy(
                        update={"origin": ScenarioOrigin.BASE, "derived_from": one.key}
                    )
                    for one in candidates
                ],
                parent_root=self.suite_root,
                policy_profile=self.base_suite.policy_profile,
                postgres_dsn=self.postgres_dsn,
            )
        except ReleaseError as exc:
            raise AdaptationFailure(
                "suite_unproved", f"{exc.code}: {exc.detail}"
            ) from exc
        self._suite = (root, suite, True)
        return self._suite

    # --- what the session reads -----------------------------------------------------------

    def brief(self) -> str:
        """Everything the binding needs, derived: tools, policy, tables, agent, scenarios."""
        scenarios = []
        for entry in self.base_suite.scenarios:
            scenario = read_folder(self.suite_root, Path(entry.path).name)
            scenarios.append(
                {
                    "scenario_key": entry.key,
                    "instruction": scenario.instruction,
                    "persona": scenario.persona.model_dump(mode="json")
                    if scenario.persona
                    else {},
                    "setup_code": scenario.setup_code,
                    "reference_solution": [
                        {"tool": step.tool, "arguments": step.arguments}
                        for step in scenario.solution
                    ],
                    "checks": {
                        name: goal.check
                        for name in scenario.sub_goals
                        if (goal := self.catalogue.named(name)) is not None
                    },
                }
            )
        agent = self.profile.target
        lines = [
            f"Mode: {self.mode.value}.",
            f"Agent: {agent.get('name') or 'unnamed'} ({self.profile.connector}).",
            "\nENVIRONMENT TOOLS:\n"
            + json.dumps(
                [one.model_dump(mode="json") for one in self.environment_tools],
                indent=1,
            ),
            "\nENVIRONMENT POLICY:\n"
            + json.dumps(self.policy.model_dump(mode="json"), indent=1),
            "\nWORLD TABLES: "
            + _tables(self._world_database())
            + " (rows: world_rows)",
            "\nTHE AGENT (provider profile, redacted):\n" + _agent_summary(agent),
            "\nTHE AGENT'S ADMITTED TOOLS:\n"
            + json.dumps(self.profile.agent_tools, indent=1),
            "\nSCENARIOS (reference solutions in environment vocabulary):\n"
            + json.dumps(scenarios, indent=1, ensure_ascii=False),
        ]
        environment = {tool.name: tool.parameters for tool in self.environment_tools}
        mismatched = {
            str(tool["name"]): problems
            for tool in self.profile.agent_tools
            if str(tool.get("name")) in environment
            and (
                problems := argument_mismatches(
                    tool.get("parameters") or {}, environment[str(tool["name"])]
                )
            )
        }
        if self.identity is not None:
            lines.append(
                "\nThe agent's tools are some of the environment's, each called as-is: the "
                "harness submitted the binding that maps each onto itself, so the agent is served "
                "its own tools and no others. Resubmit only to change it; run_proof next."
            )
        if mismatched:
            lines.append(
                "\nSAME NAME, DIFFERENT ARGUMENTS (these need a mapping like any other tool):\n"
                + json.dumps(mismatched, indent=1)
            )
        return "\n".join(lines)

    def _world_database(self) -> Path:
        return self.target_root / "world" / "world.sqlite"

    def world_rows(self, table: str) -> str:
        return _world_rows(self._world_database(), table)

    # --- binding, proof, conflicts --------------------------------------------------------

    def freeze_pairing(self, pairing: Any) -> None:
        """Raises `AdaptationFailure` (`adaptation_pairing_invalid`) for a pairing that names a
        tool neither side has."""
        self.pairing = frozen_pairing(
            pairing,
            self.environment_tools,
            [str(one["name"]) for one in self.profile.agent_tools],
        )

    def _identity(self) -> dict[str, Any] | None:
        """`identity_mapping`, held to the reviewed pairing: a tool paired with nothing is
        unsupported, and one paired with another tool needs a mapping written."""
        mapping = identity_mapping(self.profile, self.environment_tools)
        if mapping is None or self.pairing is None:
            return mapping
        if any(paired not in (None, name) for name, paired in self.pairing.items()):
            return None
        return {
            "tools": [
                entry
                if self.pairing.get(entry["agent_tool"])
                else {
                    "agent_tool": entry["agent_tool"],
                    "unsupported": "the reviewed pairing gives it no environment tool",
                }
                for entry in mapping["tools"]
            ]
        }

    def submit_binding(self, body: Mapping[str, Any]) -> tuple[bool, str]:
        if self.mode is not BindingMode.TOOL_ROUTED:
            return False, f"mode is {self.mode.value}: there is no binding to write"
        try:
            draft = parse_binding_draft(body)
        except (TypeError, ValueError) as exc:
            return False, str(exc)
        try:
            binding = seal_binding(
                self.profile,
                BindingMode.TOOL_ROUTED,
                draft.mapping,
                environment_root=self.target_root,
                environment_tools=self.environment_tools,
            )
        except ReleaseError as exc:
            return False, "\n".join(exc.detail.splitlines() or [exc.code])
        if self.pairing is not None and (
            unlike := pairing_problems(binding, self.pairing, self.environment_tools)
        ):
            return False, "not the reviewed pairing:\n" + "\n".join(unlike)
        # The harness writes every trajectory the binding determines, and those stand: a
        # written one is taken only for a scenario code could not derive.
        derived = derive_trajectories(
            binding,
            (
                (entry.key, scenario.solution, scenario.alternatives)
                for entry in self.base_suite.scenarios
                if (scenario := read_folder(self.suite_root, Path(entry.path).name))
                is not None
            ),
            self.environment_tools,
        )
        written = {
            key: calls
            for key, calls in draft.trajectories.items()
            if key not in derived.trajectories
        }
        replaced = sorted(set(draft.trajectories) & set(derived.trajectories))
        draft.trajectories = {**derived.trajectories, **written}
        problems: list[str] = []
        keys = {entry.key for entry in self.base_suite.scenarios}
        agent_tools = sorted(one["name"] for one in self.profile.agent_tools)
        for key, calls in written.items():
            if key not in keys:
                problems.append(f"trajectory {key}: the suite has no such scenario")
                continue
            for index, call in enumerate(calls, start=1):
                if not (
                    isinstance(call, Mapping)
                    and call.get("tool") in agent_tools
                    and isinstance(call.get("arguments"), Mapping)
                ):
                    problems.append(
                        f"trajectory {key} call {index} must be "
                        f"{{tool: one of {agent_tools}, arguments: object}}"
                    )
                # A call the agent's own schema refuses is not one it can make, so it proves
                # nothing about the agent.
                elif refused := agent_call_problems(
                    binding.agent_tools, call["tool"], call["arguments"]
                ):
                    problems.append(
                        f"trajectory {key} call {index}: the agent's {call['tool']} schema "
                        f"refuses it: {'; '.join(refused)}"
                    )
        problems.extend(
            f"extension candidate {one['agent_tool']}: not an admitted agent tool"
            for one in draft.extension_candidates
            if one["agent_tool"] not in agent_tools
        )
        if problems:
            return False, "\n".join(problems)
        self.draft, self.binding, self.proof, self.verdicts = draft, binding, None, {}
        self._save_state(raw=dict(body))
        still = {
            key: reason
            for key, reason in derived.undecided.items()
            if key not in written
        }
        # A scenario none of whose routes the binding reaches is unscorable unless a trajectory
        # written in the agent's own tools solves it; run_proof proves that one like any other.
        closed = sorted(key for key in derived.unreached if key not in written)
        lines = [
            f"accepted as {binding.digest}: {len(derived.trajectories)} trajectories derived "
            f"from the reference solutions, {len(written)} written by you"
            + (
                f" ({len(replaced)} you wrote were set aside for the derived ones: "
                f"{', '.join(replaced)})"
                if replaced
                else ""
            )
            + (
                f", {len(closed)} scenarios use a tool no entry reaches (unscorable unless "
                f"you write a trajectory that solves them another way: {', '.join(closed)})"
                if closed
                else ""
            )
            + "."
        ]
        if still:
            lines.append(
                f"{len(still)} scenarios still need a trajectory from you (resubmit with them):"
            )
            lines.extend(f"- {key}: {reason}" for key, reason in sorted(still.items()))
        lines.append("run_proof next")
        return True, "\n".join(lines)

    def run_proof(self) -> tuple[bool, str]:
        if self.mode is not BindingMode.TOOL_ROUTED:
            return False, f"mode is {self.mode.value}: there is no binding to prove"
        if self.draft is None:
            return False, "submit a binding first"
        suite_root, suite, _derived = self._suite_for_target()
        try:
            proof = prove_binding_on(
                suite_root,
                self.binding,
                self.draft.trajectories,
                environment_root=self.target_root,
                postgres_dsn=self.postgres_dsn,
            )
        except ReleaseError as exc:
            return False, f"{exc.code}: {exc.detail}"
        self.proof = proof.model_dump(mode="json")
        self.verdicts = binding_verdicts(proof)
        self._save_state()
        lines = [
            f"{key}: {verdict.status}"
            + (f": {verdict.reason}" if verdict.reason else "")
            for key, verdict in sorted(self.verdicts.items())
        ]
        passed = self._proved(suite.digest)
        return passed, ("Proved. " if passed else "Not proved. ") + "\n".join(lines)

    def _proved(self, suite_digest: str) -> bool:
        if self.proof is None or self.binding is None:
            return False
        return bool(
            self.proof.get("passed")
            and self.proof["binding"]["digest"] == self.binding.digest
            and self.proof["suite"]["digest"] == suite_digest
            and not any(v.status == "failed" for v in self.verdicts.values())
            and any(v.status == "proved" for v in self.verdicts.values())
        )

    def report_conflicts(self, items: Sequence[Any]) -> tuple[bool, str]:
        rules = {rule.id: rule for rule in self.policy.rules}
        problems: list[str] = []
        conflicts: list[dict[str, str]] = []
        for index, item in enumerate(items, start=1):
            if not isinstance(item, Mapping):
                problems.append(f"conflict {index}: must be an object")
                continue
            rule = rules.get(str(item.get("rule") or ""))
            agent = str(item.get("agent") or "").strip()
            if rule is None:
                problems.append(
                    f"conflict {index}: rule must be one of {sorted(rules)}"
                )
            if not agent:
                problems.append(
                    f"conflict {index}: quote what the agent's instructions say"
                )
            if rule is not None and agent:
                conflicts.append({"rule": rule.id, "policy": rule.text, "agent": agent})
        if problems:
            return False, "\n".join(problems)
        self.conflicts = conflicts
        self._save_state()
        return True, (
            f"{len(conflicts)} conflict(s) recorded"
            if conflicts
            else "no conflicts recorded"
        )

    # --- finishing ------------------------------------------------------------------------

    def unfinished(self) -> list[str]:
        missing: list[str] = []
        if self.mode is BindingMode.TOOL_ROUTED:
            if self.draft is None:
                missing.append("no binding has been submitted")
            else:
                _root, suite, _derived = self._suite_for_target()
                if not self._proved(suite.digest):
                    missing.append(
                        "the current binding has not passed run_proof: every scenario must be "
                        "proved or unscorable, and at least one proved"
                    )
        if self.conflicts is None:
            missing.append(
                "report_conflicts has not been called (an empty list when there are none)"
            )
        return missing

    def approval_question(self) -> str:
        agent = self.profile.target.get("name") or "the agent"
        environment = self.releases[-1]["name"]
        # One fact per line: the chat shows this as plain text, and a reviewer reads the
        # mapping first. Unscorable scenarios are counted, not listed; the environment's
        # composition lists each one.
        lines = [f"Use this binding for {agent} on {environment}?"]
        mapped, unsupported, _candidates, unscorable = self._outcome()
        if mapped:
            lines += ["", f"Mapped tools ({len(mapped)}):"]
            lines += [
                f"• {one['agent_tool']} → {' + '.join(one['steps'])}" for one in mapped
            ]
        if unsupported:
            lines += ["", f"Unsupported tools ({len(unsupported)}):"]
            lines += [f"• {one['agent_tool']}: {one['reason']}" for one in unsupported]
        if self.conflicts:
            lines += ["", f"Policy conflicts ({len(self.conflicts)}):"]
            lines += [
                f"• {one['rule']}: policy says '{one['policy']}', the agent says '{one['agent']}'"
                for one in self.conflicts
            ]
        total = len(self.base_suite.scenarios)
        lines += ["", f"Scenarios: {total - len(unscorable)} of {total} can be scored."]
        if unscorable:
            lines.append(
                f"{len(unscorable)} need a tool this agent doesn't have, so they are left "
                "out of its score (listed on the environment's Composition)."
            )
        lines.append("The test suite is unchanged.")
        return "\n".join(lines)

    def approval_data(self) -> dict[str, Any]:
        """What the approval question says, as data, for a page that shows it as a review."""
        mapped, unsupported, _candidates, unscorable = self._outcome()
        total = len(self.base_suite.scenarios)
        return {
            "mapped": [
                {"agent_tool": one["agent_tool"], "steps": list(one["steps"])}
                for one in mapped
            ],
            "unsupported": [
                {"agent_tool": one["agent_tool"], "reason": one["reason"]}
                for one in unsupported
            ],
            "unscorable": [
                {"scenario_key": one["scenario_key"], "reason": one["reason"]}
                for one in unscorable
            ],
            "conflicts": [dict(one) for one in self.conflicts or []],
            "total": total,
            "scorable": total - len(unscorable),
        }

    def _outcome(
        self,
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, str]],
        list[dict[str, str]],
    ]:
        if self.mode is not BindingMode.TOOL_ROUTED:
            return self.alignment["mapped"], [], [], self.alignment["unscorable"]
        mapped: list[dict[str, Any]] = []
        unsupported: list[dict[str, Any]] = []
        for entry in self.binding.tools:
            body = entry.model_dump(mode="json")
            if body.get("unsupported"):
                unsupported.append(
                    {"agent_tool": body["agent_tool"], "reason": body["unsupported"]}
                )
            else:
                mapped.append(
                    {
                        "agent_tool": body["agent_tool"],
                        "steps": [step["tool"] for step in body["steps"]],
                    }
                )
        names = {one["agent_tool"] for one in unsupported}
        candidates = [
            one
            for one in (self.draft.extension_candidates if self.draft else [])
            if one["agent_tool"] in names
        ]
        unscorable = [
            {"scenario_key": key, "reason": verdict.reason}
            for key, verdict in sorted(self.verdicts.items())
            if verdict.status == "unscorable"
        ]
        return mapped, unsupported, candidates, unscorable

    async def finish(self, approve: Approve) -> tuple[bool, str]:
        missing = self.unfinished()
        if missing:
            return False, "Not finished:\n- " + "\n- ".join(missing)
        answer = str(await approve(self.approval_question(), ["yes", "no"])).strip()
        if answer.lower() not in _YES:
            self.declined = answer or "no"
            self._save_state()
            return False, (
                f"The person did not approve ({self.declined!r}). Ask what they want changed "
                "and revise, or call stop_adaptation."
            )
        self.seal()
        return True, "Sealed. The adaptation is complete."

    def seal(self) -> None:
        _root, suite, derived = self._suite_for_target()
        mapped, unsupported, candidates, unscorable = self._outcome()
        if self.mode is BindingMode.TOOL_ROUTED:
            write_json(self.output / BINDING_PROOF, self.proof)
            write_json(
                self.output / TRAJECTORIES,
                {
                    key: calls
                    for key, calls in self.draft.trajectories.items()
                    if getattr(self.verdicts.get(key), "status", None) == "proved"
                },
            )
        write_json(
            self.output / BINDING, self.binding.model_dump(mode="json", by_alias=True)
        )
        self.alignment.update(
            mapped=mapped,
            unsupported=unsupported,
            extension_candidates=candidates,
            unscorable=unscorable,
            conflicts=list(self.conflicts or []),
        )
        self._finish(
            {
                "schema_version": RESULT_SCHEMA,
                "status": "completed",
                "failure": None,
                "environment": self.releases,
                "suite": {
                    "digest": suite.digest,
                    "name": suite.name,
                    "derived": derived,
                },
                "binding": {"digest": self.binding.digest, "mode": self.mode.value},
                # What a run's manifest freezes, so compose reads only this proof's statuses.
                "binding_proof": self.proof_ref(),
                "alignment": self.alignment,
            }
        )

    def proof_ref(self) -> dict[str, str] | None:
        if self.mode is not BindingMode.TOOL_ROUTED:
            return None
        from ..binding_proof import BindingProofV1, proof_digest

        return {"digest": proof_digest(BindingProofV1.model_validate(self.proof))}

    def fail(self, code: str, message: str) -> None:
        result = failed_result(code, message)
        result["environment"] = self.releases
        self._finish(result)

    def stop_code(self) -> str:
        return "adaptation_declined" if self.declined else "binding_unproved"

    def _finish(self, result: dict[str, Any]) -> None:
        write_json(self.output / RESULT, result)
        self.done = True
        if self.finished_marker is not None:
            write_json(self.finished_marker, {"status": result["status"]})

    # --- state ----------------------------------------------------------------------------

    def _save_state(self, *, raw: dict[str, Any] | None = None) -> None:
        state = self._read_state()
        if raw is not None:
            state["draft"] = raw
        state["proof"] = self.proof
        state["conflicts"] = self.conflicts
        state["declined"] = self.declined
        write_json(self.output / STATE, state)

    def _read_state(self) -> dict[str, Any]:
        try:
            body = json.loads((self.output / STATE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return body if isinstance(body, dict) else {}

    def _restore(self) -> None:
        state = self._read_state()
        self.done = (self.output / RESULT).is_file()
        if state.get("draft") and self.mode is BindingMode.TOOL_ROUTED:
            accepted, _said = self.submit_binding(state["draft"])
            proof = state.get("proof")
            if (
                accepted
                and isinstance(proof, dict)
                and (proof.get("binding", {}).get("digest") == self.binding.digest)
            ):
                from ..binding_proof import BindingProofV1

                self.proof = proof
                self.verdicts = binding_verdicts(BindingProofV1.model_validate(proof))
        if isinstance(state.get("conflicts"), list):
            self.conflicts = state["conflicts"]
        self.declined = str(state.get("declined") or "")

    def fingerprint(self) -> str:
        """Changes whenever the work moves: what the builder's idle check compares."""
        try:
            return (self.output / STATE).read_text(encoding="utf-8")
        except OSError:
            return ""


def binding_server(workbench: Workbench, approve: Approve) -> Any:
    """The tools the session drafts through; every one is checked here, not trusted."""
    import asyncio

    from ..backends import tool, tool_server
    from ..tools import schema

    @tool(
        "world_rows",
        "Rows of one world table (at most 200): the real formats of names, dates, identifiers "
        "and enum values.",
        schema({"table": str}, ["table"]),
    )
    async def world_rows(args: dict[str, Any]) -> dict[str, Any]:
        return _say(workbench.world_rows(str(args.get("table") or "")))

    @tool(
        "submit_binding",
        "Submit the complete binding. Trajectories are optional: the harness derives one for "
        "every scenario the binding inverts and says which still need yours. Each argument is "
        "JSON text.",
        {
            "type": "object",
            "properties": {
                "binding": {
                    "type": "string",
                    "description": "JSON object {tools, fixtures, aliases, rendering}",
                },
                "trajectories": {
                    "type": "string",
                    "description": "JSON object {scenario_key: [{tool, arguments}]}, only for "
                    "scenarios the harness could not derive (the harness's win where both exist)",
                },
                "extension_candidates": {
                    "type": "string",
                    "description": "JSON list [{agent_tool, reason}]: unsupported agent tools "
                    "that act on business state this environment lacks",
                },
            },
            "required": ["binding"],
        },
    )
    async def submit_binding(args: dict[str, Any]) -> dict[str, Any]:
        try:
            body = {
                "binding": _json_argument(args, "binding"),
                "trajectories": _json_argument(args, "trajectories"),
                "extension_candidates": _json_argument(
                    args, "extension_candidates", []
                ),
            }
        except ValueError as exc:
            return _say(f"not accepted: {exc}", error=True)
        accepted, said = await asyncio.to_thread(workbench.submit_binding, body)
        return _say(said if accepted else f"not accepted:\n{said}", error=not accepted)

    @tool(
        "run_proof",
        "Prove the submitted binding on a fresh PostgreSQL world for every scenario; returns "
        "each scenario's status and why.",
        schema({}, []),
    )
    async def run_proof(_args: dict[str, Any]) -> dict[str, Any]:
        try:
            passed, said = await asyncio.to_thread(workbench.run_proof)
        except AdaptationFailure as exc:
            return _say(f"{exc.code}: {exc.message}", error=True)
        return _say(said, error=not passed)

    @tool(
        "report_conflicts",
        "Every environment policy rule the agent's own instructions contradict or ignore: "
        "JSON list [{rule: <policy rule id>, agent: <what the agent's instructions say>}]. "
        "An empty list when there are none.",
        schema({"conflicts": str}, ["conflicts"]),
    )
    async def report_conflicts(args: dict[str, Any]) -> dict[str, Any]:
        try:
            items = _json_argument(args, "conflicts", [])
        except ValueError as exc:
            return _say(f"not recorded: {exc}", error=True)
        if not isinstance(items, list):
            return _say("not recorded: conflicts must be a JSON list", error=True)
        accepted, said = workbench.report_conflicts(items)
        return _say(said if accepted else f"not recorded:\n{said}", error=not accepted)

    @tool(
        "finish_adaptation",
        "Check the proof and conflicts, ask the person to approve the binding, and seal it.",
        schema({}, []),
    )
    async def finish_adaptation(_args: dict[str, Any]) -> dict[str, Any]:
        if workbench.done:
            return _say("The adaptation is already finished.")
        try:
            finished, said = await workbench.finish(approve)
        except AdaptationFailure as exc:
            return _say(f"{exc.code}: {exc.message}", error=True)
        return _say(said, error=not finished)

    @tool(
        "stop_adaptation",
        "End the adaptation without a binding, when it cannot be proved or the person does "
        "not want it. Give the reason.",
        schema({"reason": str}, ["reason"]),
    )
    async def stop_adaptation(args: dict[str, Any]) -> dict[str, Any]:
        if workbench.done:
            return _say("The adaptation is already finished.")
        reason = str(args.get("reason") or "").strip() or "stopped"
        await asyncio.to_thread(workbench.fail, workbench.stop_code(), reason)
        return _say("Stopped: the adaptation failed with that reason.")

    return tool_server(
        name="adaptation",
        version="0.1.0",
        tools=[
            world_rows,
            submit_binding,
            run_proof,
            report_conflicts,
            finish_adaptation,
            stop_adaptation,
        ],
    )


# --- helpers ------------------------------------------------------------------------------


def _say(text: str, *, error: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if error:
        body["is_error"] = True
    return body


def _json_argument(args: Mapping[str, Any], name: str, default: Any = None) -> Any:
    raw = args.get(name)
    if raw in (None, ""):
        return default
    if not isinstance(raw, str):
        return raw
    return json.loads(raw)


def _world_rows(database: Path, table: str) -> str:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        tables = {
            name
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if table not in tables:
            return f"no table {table!r}; tables: {', '.join(sorted(tables))}"
        cursor = connection.execute(f'SELECT * FROM "{table}" LIMIT {_ROW_LIMIT}')
        columns = [column[0] for column in cursor.description]
        return json.dumps(
            [dict(zip(columns, row)) for row in cursor.fetchall()], default=str
        )
    finally:
        connection.close()


def _tables(database: Path) -> str:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        names = [
            name
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        ]
        counts = {
            name: connection.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
            for name in names
        }
    finally:
        connection.close()
    return ", ".join(f"{name} ({count} rows)" for name, count in counts.items())


def _agent_summary(agent: Mapping[str, Any], limit: int = 8000) -> str:
    keep = {
        key: agent.get(key)
        for key in (
            "name",
            "begin_message",
            "general_prompt",
            "instructions",
            "first_message",
            "model",
            "states",
            "conversation_flow",
        )
        if agent.get(key) not in (None, "", [], {})
    }
    text = json.dumps(keep, indent=1, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "\n...(truncated)"
