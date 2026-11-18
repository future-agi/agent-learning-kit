"""Whether what the agent or the simulated people see gives a scenario's answer away.

Structural, with no phrase list. Grader-only text (what the checks and judges expect) is compared
with the text each audience is given: an 8-word span repeated, a literal a check compares against
that the world does not hold, or a sub-goal's multi-word name. Whether the agent's process can
*open* the grader files is a separate question, answered by the runtime boundary, not here.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .actors import evaluator_only_paths
from .catalogue import Catalogue
from .contract import AgentContract
from .proof_records import LeakRecord
from .scenario import Scenario

SHINGLE = 8
EXCERPT = 120


@dataclass
class Leak:
    who: str
    field: str
    grader_field: str
    excerpt: str

    def as_dict(self) -> LeakRecord:
        return LeakRecord(who=self.who, field=self.field, grader_field=self.grader_field, excerpt=self.excerpt)


def normalise(text: str) -> str:
    text = str(text or "").casefold().replace("_", " ").replace("-", " ")
    text = re.sub(r"[^\w\s.]", " ", text)
    # A decimal point inside a number is kept; any other full stop is punctuation.
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> list[str]:
    return normalise(text).split()


def _contains(haystack: list[str], needle: list[str]) -> int:
    """Index of `needle` as a contiguous token run in `haystack`, or -1."""
    if not needle or len(needle) > len(haystack):
        return -1
    for at in range(len(haystack) - len(needle) + 1):
        if haystack[at : at + len(needle)] == needle:
            return at
    return -1


def _excerpt(tokens: list[str]) -> str:
    return " ".join(tokens)[:EXCERPT]


def _returned(value: ast.expr) -> list[ast.expr]:
    """The expressions a return can hand back, looking through conditional expressions."""
    if isinstance(value, ast.IfExp):
        return _returned(value.body) + _returned(value.orelse)
    if isinstance(value, ast.BoolOp):
        return [leaf for one in value.values for leaf in _returned(one)]
    return [value]


def _check_literals(source: str) -> set[str]:
    """String literals with a digit or 3+ tokens, and decimal numbers, from comparison and argument
    positions of a check. Never from a return, a raise or an f-string: a check returns a sentence
    saying what was wrong, and that sentence is ordinary instruction text."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    excluded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Raise, ast.JoinedStr)):
            excluded.update(id(inner) for inner in ast.walk(node))
        elif isinstance(node, ast.Return) and node.value is not None:
            # `return None if world... == 42.5 else "wrong"`: the condition is a comparison and
            # its literals count; only what the check can hand back is a sentence.
            for value in _returned(node.value):
                excluded.update(id(inner) for inner in ast.walk(value))
    positions: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            positions.append(node.left)
            positions.extend(node.comparators)
        elif isinstance(node, ast.Call):
            positions.extend(node.args)
            positions.extend(keyword.value for keyword in node.keywords)
        elif isinstance(node, ast.Subscript):
            positions.append(node.slice)
    found: set[str] = set()
    for position in positions:
        for inner in ast.walk(position):
            if id(inner) in excluded or not isinstance(inner, ast.Constant):
                continue
            value = inner.value
            if isinstance(value, bool):
                continue
            if isinstance(value, str):
                if any(ch.isdigit() for ch in value) or len(_tokens(value)) >= 3:
                    found.add(value)
            elif isinstance(value, float):
                found.add(f"{value:.2f}" if round(value, 2) == value else repr(value))
    return found


def _solution_literals(scenario: Scenario) -> set[str]:
    """Argument values of the reference steps the simulated person would not know: strings with a digit or 3+
    tokens, and decimal numbers. Values the simulated person already holds are dropped by the reader."""
    found: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, bool):
            return
        if isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item)
        elif isinstance(value, str) and not value.startswith("$"):
            if any(ch.isdigit() for ch in value) or len(_tokens(value)) >= 3:
                found.add(value)
        elif isinstance(value, float):
            # As a person would write it: 37.5 and 37.50 are the same answer.
            found.add(repr(value))
            if round(value, 2) == value:
                found.add(f"{value:.2f}")

    for step in getattr(scenario, "solution", None) or []:
        visit(dict(getattr(step, "arguments", None) or {}))
    return found


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        yield str(value)


def _in_world(literal: str, world_text: list[str]) -> bool:
    needle = _tokens(literal)
    return any(_contains(_tokens(text), needle) >= 0 for text in world_text)


def _evaluator_only_texts(world_root: Path | None) -> list[tuple[str, str]]:
    """The text of each attached file only the evaluator may read (`knowledge/roles.json`), as grader material."""
    if world_root is None:
        return []

    texts = []
    for path in evaluator_only_paths(Path(world_root)):
        try:
            texts.append((f"evaluator_only:{path}", (Path(world_root) / path).read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            continue
    return texts


def _takes_part(scenario: Scenario, actor: Mapping[str, Any]) -> bool:
    actor_id = actor.get("id")
    start = getattr(scenario, "starts_with", None)
    if isinstance(start, dict) and start.get("actor") == actor_id:
        return True
    return any(isinstance(one, dict) and one.get("id") == actor_id for one in getattr(scenario, "actors", None) or [])


def leaks(
    scenario: Scenario,
    catalogue: Catalogue,
    contract: AgentContract | None = None,
    world_state: Mapping[str, Any] | None = None,
    actors: Sequence[Mapping[str, Any]] | None = None,
    *,
    world_root: Path | None = None,
) -> list[Leak]:
    """Every place a visible field repeats grader-only material. `world_state` None skips rule (b)."""
    grader: list[tuple[str, str]] = []
    literals: set[str] = set()
    names: list[str] = []
    for name in getattr(scenario, "sub_goals", None) or []:
        sub_goal = catalogue.named(name)
        if sub_goal is None:
            continue
        names.append(name)
        for field_name in ("what", "judged", "reference", "verifier", "comparison"):
            text = getattr(sub_goal, field_name, "") or ""
            if text.strip():
                grader.append((f"sub_goal:{name}.{field_name}", text))
        if sub_goal.check:
            literals |= _check_literals(sub_goal.check)
    if str(getattr(scenario, "tests", "") or "").strip():
        grader.append(("tests", scenario.tests))
    for control in getattr(scenario, "negative_controls", None) or []:
        grader.append((f"negative_control:{control.name}.why", control.why))
    grader.extend(_evaluator_only_texts(world_root))

    rule_b = world_state is not None
    if rule_b:
        world_text = list(_strings(world_state)) + [str(getattr(scenario, "setup_code", "") or "")]
        literals = {one for one in literals if not _in_world(one, world_text)}

    persona = getattr(scenario, "persona", None)
    persona_values = persona.model_dump() if hasattr(persona, "model_dump") else (persona or {})
    variables = getattr(scenario, "variables", None) or {}
    instruction = str(getattr(scenario, "instruction", "") or "")
    initial = str((persona_values or {}).get("initial_message") or "")
    # The simulated person's own facts (their variables, persona, instruction and the records the world holds)
    # are theirs to say; any other value the reference sends is the answer.
    person_text = [instruction, *_strings(variables)]
    person_text += [
        text for key, value in (persona_values or {}).items() if key != "initial_message" for text in _strings(value)
    ]
    if rule_b:
        person_text += world_text
    answers = {one for one in _solution_literals(scenario) if not _in_world(one, person_text)}
    visible: list[tuple[str, str, str]] = [("simulated person", "instruction", instruction)]
    visible += [("simulated person", f"variables.{key}", text) for key, value in variables.items() for text in _strings(value)]
    visible += [
        ("simulated person", f"persona.{key}", text)
        for key, value in (persona_values or {}).items()
        for text in _strings(value)
    ]
    conversational = getattr(contract, "conversational", True) if contract is not None else True
    agent_fields = [("persona.initial_message", initial)]
    agent_fields += [(f"variables.{key}", text) for key, value in variables.items() for text in _strings(value)]
    if not conversational:
        agent_fields.append(("instruction", instruction))
    visible += [("tested agent", field_name, text) for field_name, text in agent_fields]
    for actor in actors or []:
        if not isinstance(actor, dict) or not _takes_part(scenario, actor):
            continue
        label = str(actor.get("name") or actor.get("id") or "actor")
        for key in ("instructions", "instruction", "brief", "knowledge"):
            for text in _strings(actor.get(key)):
                visible.append(("actor", f"{label}.{key}", text))

    found: list[Leak] = []
    seen: set[tuple[str, str, str]] = set()

    def add(who: str, field_name: str, grader_field: str, excerpt: str) -> None:
        if (who, field_name, grader_field) not in seen:
            seen.add((who, field_name, grader_field))
            found.append(Leak(who, field_name, grader_field, excerpt))

    for who, field_name, text in visible:
        tokens = _tokens(text)
        if not tokens:
            continue
        sources = list(grader)
        if who == "tested agent" and conversational and field_name == "persona.initial_message":
            # On a conversational agent the instruction is the simulated person's; repeating it as the
            # opening message hands the whole hidden instruction to the tested agent.
            sources.append(("instruction", instruction))
        for grader_field, grader_text in sources:
            grader_tokens = _tokens(grader_text)
            for at in range(len(grader_tokens) - SHINGLE + 1):
                span = grader_tokens[at : at + SHINGLE]
                if _contains(tokens, span) >= 0:
                    add(who, field_name, grader_field, _excerpt(span))
                    break
        if rule_b:
            for literal in sorted(literals):
                needle = _tokens(literal)
                if _contains(tokens, needle) >= 0:
                    add(who, field_name, "check literal", _excerpt(needle))
        for answer in sorted(answers):
            needle = _tokens(answer)
            if _contains(tokens, needle) >= 0:
                add(who, field_name, "scenario.solution", _excerpt(needle))
        for name in names:
            needle = _tokens(name)
            if len(needle) >= 2 and _contains(tokens, needle) >= 0:
                add(who, field_name, f"sub_goal:{name}.name", _excerpt(needle))
    return found
