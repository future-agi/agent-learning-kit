"""Scenarios a suite rebuild dropped because the agent lost a capability they need.

Each drop is kept in ``dropped.json`` beside the scenarios, so it travels with every suite version:
what the scenario needed (tools its reference solution calls, sub-goals it names), what was gone,
the agent version that removed it, the reason, the last suite version that held it, and the
scenario's last content. A later suite rebuild in which everything it needed is back puts it in the
suite again under the same key, from that content, and proves it like any other scenario. One that
cannot be proved goes back to dropped and is reported with the reason.

A scenario dropped for any other reason is not recorded: only a missing capability brings one back.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, TypedDict

from .contract import AgentContract
from .proof_records import ProofEntry
from .proofs import utc_stamp
from .prove import NO_NEGATIVE_CONTROL
from .scenario import Scenario, scenario_slug

DROPPED = "dropped.json"
FORMAT = 1


class Capabilities(TypedDict):
    """What a scenario needs, or what of it is gone: tools its reference solution calls, sub-goals it names."""

    tools: list[str]
    sub_goals: list[str]


class _Dropped(TypedDict):
    name: str
    capabilities: Capabilities
    missing: Capabilities
    agent_version: str
    reason: str
    last_suite_version: str
    # The scenario's last content, as its scenario file holds it.
    content: dict[str, Any]


class DropRecord(_Dropped, total=False):
    """One dropped scenario in ``dropped.json``: what it needed, what was gone, why, and its last content."""

    restored_at: str
    restore_failed: str


class Settled(TypedDict, total=False):
    """A restore as the rebuild's result lists it."""

    key: str
    name: str
    why: str
    proof: str


def load(workspace: Path) -> dict[str, DropRecord]:
    """Each dropped scenario's record, by its key; empty when nothing was dropped."""
    try:
        body = json.loads((Path(workspace) / DROPPED).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    held = body.get("scenarios") if isinstance(body, dict) else None
    if not isinstance(held, dict):
        return {}
    return {str(key): dict(one) for key, one in held.items() if isinstance(one, dict)}


def save(workspace: Path, records: Mapping[str, DropRecord]) -> None:
    """Write the records; with none left the file goes, so an untouched suite stays unchanged."""
    path = Path(workspace) / DROPPED
    if not records:
        path.unlink(missing_ok=True)
        return
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "format": FORMAT,
                "scenarios": {key: dict(records[key]) for key in sorted(records)},
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def needs(scenario: Scenario) -> Capabilities:
    """The capabilities a scenario needs: the tools its reference solution calls and its sub-goals."""
    return {
        "tools": sorted({step.tool for step in scenario.solution if step.tool}),
        "sub_goals": sorted(set(scenario.sub_goals)),
    }


def missing(
    needed: Mapping[str, Iterable[str]], tools: set[str], sub_goals: set[str]
) -> Capabilities:
    """What of ``needed`` the contract's tools and the catalogue's sub-goals no longer hold."""
    return {
        "tools": sorted(one for one in needed.get("tools") or [] if one not in tools),
        "sub_goals": sorted(
            one for one in needed.get("sub_goals") or [] if one not in sub_goals
        ),
    }


def reason(gone: Mapping[str, Iterable[str]], agent_version: str = "") -> str:
    """Why the scenario was dropped, for a person: "agent v5 no longer has `<tool>`"."""
    agent = f"agent {agent_version}" if agent_version else "the agent"
    parts = []
    tools = list(gone.get("tools") or [])
    sub_goals = list(gone.get("sub_goals") or [])
    if tools:
        parts.append(f"{agent} no longer has " + ", ".join(f"`{one}`" for one in tools))
    if sub_goals:
        parts.append(
            ("the environment" if tools else f"for {agent} the environment")
            + " retired "
            + ", ".join(f"`{one}`" for one in sub_goals)
        )
    return "; ".join(parts)


def record_drops(
    workspace: Path,
    before: Mapping[str, Mapping[str, Any]],
    present: set[str],
    *,
    tools: set[str],
    sub_goals: set[str],
    agent_version: str = "",
    suite_version: str = "",
) -> dict[str, DropRecord]:
    """Record each scenario of ``before`` the suite no longer holds because a capability it needs
    is gone, and return those records by key. ``before`` is each scenario's content when the turn
    began; ``present`` the keys the suite holds now."""
    records = load(workspace)
    # One recorded by an earlier report of this turn and kept again after it is not dropped.
    kept_again = {key for key in set(before) & present if key in records}
    recorded: dict[str, DropRecord] = {}
    for key in sorted(set(before) - present):
        try:
            scenario = Scenario.model_validate(dict(before[key]))
        except ValueError:
            continue
        needed = needs(scenario)
        gone = missing(needed, tools, sub_goals)
        if not gone["tools"] and not gone["sub_goals"]:
            continue
        recorded[key] = {
            "name": scenario.name,
            "capabilities": needed,
            "missing": gone,
            "agent_version": agent_version,
            "reason": reason(gone, agent_version),
            "last_suite_version": suite_version,
            "content": dict(before[key]),
        }
    if recorded or kept_again:
        save(
            workspace,
            {
                **{key: one for key, one in records.items() if key not in kept_again},
                **recorded,
            },
        )
    return recorded


def restorable(
    records: Mapping[str, DropRecord], tools: set[str], sub_goals: set[str]
) -> list[str]:
    """The dropped keys whose every needed capability is back."""
    return sorted(
        key
        for key, one in records.items()
        if not any(missing(one.get("capabilities") or {}, tools, sub_goals).values())
        and isinstance(one.get("content"), dict)
    )


def argument_changes(scenario: Scenario, contract: AgentContract | None) -> list[str]:
    """Steps of the reference solution passing arguments the tool no longer takes, or naming a
    tool the contract lacks."""
    if contract is None:
        return []
    by_name = {tool.name: tool for tool in contract.tools}
    said = []
    for position, step in enumerate(scenario.solution, start=1):
        tool = by_name.get(step.tool)
        if tool is None:
            said.append(f"step {position} calls {step.tool}, which the contract lacks")
            continue
        unknown = sorted(set(step.arguments) - set(tool.args)) if tool.args else []
        if unknown:
            said.append(
                f"step {position} {step.tool} no longer takes {', '.join(unknown)}"
            )
    return said


def restore(
    workspace: Path,
    kept: list[Scenario],
    *,
    tools: set[str],
    sub_goals: set[str],
) -> tuple[dict[str, DropRecord], dict[str, str]]:
    """Put each dropped scenario whose capabilities are all back into ``kept`` under its own key,
    from its last content: ({key: its record} for those restored, {key: why not} for those that
    could not be put back).

    ``kept`` is changed in place; the caller writes it. A restored record loses an earlier failed
    attempt's reason, so the end of this rebuild decides it again.
    """
    records = load(workspace)
    keys = {one.scenario_key for one in kept}
    names = {one.name: one.scenario_key for one in kept}
    restored: dict[str, DropRecord] = {}
    refused: dict[str, str] = {}
    for key in restorable(records, tools, sub_goals):
        if key in keys:
            continue
        try:
            scenario = Scenario.model_validate(records[key]["content"])
        except ValueError as exc:
            refused[key] = f"its last content no longer reads as a scenario: {exc}"[
                :500
            ]
            continue
        scenario.scenario_key = key
        if scenario.name in names:
            refused[key] = f"its name {scenario.name} is held by {names[scenario.name]}"
            continue
        kept.append(scenario)
        keys.add(key)
        names[scenario.name] = key
        records[key].pop("restore_failed", None)
        # A restore counts only on a proof made after it, against this rebuild's agent and catalogue.
        records[key]["restored_at"] = utc_stamp()
        restored[key] = dict(records[key])
    for key, why in refused.items():
        records[key]["restore_failed"] = why
    if restored or refused:
        save(workspace, records)
    return restored, refused


def _unproved(record: DropRecord, proof: ProofEntry | None) -> str:
    """Why a restored scenario's proof does not bring it back, or "" when it does: proved, or kept as written
    because this lane could not run a step. Only a proof made since the restore counts."""
    since = str(record.get("restored_at") or "")
    # Compared as text: both are `utc_stamp`s, which sort as the times do.
    if not proof or str(proof.get("proved_at") or "") < since:
        return "not proved again in this rebuild"
    if proof.get("proved") is True or proof.get("kept_as_written") is True:
        return ""
    if proof.get("blocked_reason") == NO_NEGATIVE_CONTROL:
        return "no negative control: write one that does the task wrong and fails its checks"
    return "could not be proved again: " + str(proof.get("why") or proof.get("blocked_reason") or "not proved")


def finish_restore(
    workspace: Path,
    restored: Mapping[str, DropRecord],
    problems: list[dict[str, Any]],
    present: set[str],
    proofs: Mapping[str, ProofEntry] | None = None,
) -> tuple[list[Settled], list[Settled], list[dict[str, Any]]]:
    """Settle what this rebuild restored: (restored, failed, the gate's other problems).

    ``restored`` is each restored scenario's record as ``restore`` returned it, so settling again
    after a later report decides from the same records. A restored scenario whose proof since the
    restore (``proofs``, by key) is proved, or kept as written because this lane could not run a
    step, is in the suite again and its record goes; it is listed with which. One with a problem,
    with no proof since the restore, with a proof that did not hold (a missing negative control
    included), or no longer in the suite, stays dropped with the reason; it is reported as a failed
    restore instead of failing the rebuild. The caller removes the failed ones from the suite.
    """
    records = load(workspace)
    why_by_key = {
        str(one.get("item")): str(one.get("message") or "")
        for one in problems
        if one.get("kind") == "scenario"
    }
    done: list[Settled] = []
    failed: list[Settled] = []
    for key in sorted(restored):
        record = dict(restored[key])
        proof = (proofs or {}).get(key)
        if key in why_by_key:
            why = "could not be proved again: " + why_by_key[key]
        elif key not in present:
            why = "dropped again in this rebuild"
        elif proofs is not None and (why := _unproved(record, proof)):
            pass
        else:
            row = {"key": key, "name": str(record.get("name") or key)}
            if proof is not None:
                row["proof"] = "proved" if proof.get("proved") is True else "kept_as_written"
                if row["proof"] == "kept_as_written":
                    row["why"] = str(proof.get("why") or "")
            done.append(row)
            records.pop(key, None)
            continue
        records[key] = {**record, "restore_failed": why}
        failed.append({"key": key, "why": why})
    save(workspace, records)
    others = [
        one
        for one in problems
        if not (one.get("kind") == "scenario" and str(one.get("item")) in restored)
    ]
    return done, failed, others


def failed_restores(workspace: Path, keys: Iterable[str]) -> list[Settled]:
    """The failed restore of each of ``keys`` that has one, as the result lists it."""
    records = load(workspace)
    return [
        {"key": key, "why": str(records[key]["restore_failed"])}
        for key in keys
        if key in records and records[key].get("restore_failed")
    ]


def submit_refusal(
    workspace: Path, payload: Mapping[str, Any], present: set[str]
) -> str:
    """Why a rebuild may not submit a new scenario under a dropped key, or "".

    A dropped key comes back only with its own scenario, once what it needs is back; a restored
    one is already in the suite, so submitting it again is an edit and is allowed.
    """
    name = str(payload.get("name") or "").strip()
    key = str(payload.get("scenario_key") or "").strip() or scenario_slug(name)
    if key in present:
        return ""
    record = load(workspace).get(key)
    if record is None:
        return ""
    return (
        f"Not kept: {key} was dropped ({record.get('reason') or 'a capability it needs is gone'}). "
        "It comes back with its own content once what it needs is back; give a new scenario "
        "a new name."
    )


def restore_note(workspace: Path, contract: AgentContract | None, sub_goals: set[str]) -> str:
    """The rebuild message's line on the scenarios this rebuild restores, or ""."""
    tools = contract.tool_names() if contract is not None else set()
    records = load(workspace)
    keys = restorable(records, tools, sub_goals)
    if not keys:
        return ""
    changes = []
    uncontrolled = []
    for key in keys:
        try:
            scenario = Scenario.model_validate(records[key]["content"])
        except ValueError:
            continue
        said = argument_changes(scenario, contract)
        if said:
            changes.append(f"{key}: " + "; ".join(said))
        if not scenario.negative_controls:
            uncontrolled.append(key)
    return (
        "\n\nRestored, because what they need is back (same key, their last content): "
        + ", ".join(keys)
        + ". Keep each as it was; change a step only where its tool or arguments changed"
        + (" (" + " | ".join(changes) + ")" if changes else "")
        + (
            ". These have no negative control, and a restored scenario comes back only once proved: write each "
            "one's own (the task done wrong, which its checks must fail): " + ", ".join(uncontrolled)
            if uncontrolled
            else ""
        )
        + ", then prove them like any other scenario. One that cannot be proved goes back to "
        "dropped and is listed with the reason; never drop or rename one to pass."
    )
