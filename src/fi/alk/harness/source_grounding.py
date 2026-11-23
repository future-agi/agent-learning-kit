"""Semantic binding between an approved public source case and a native scenario."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping

from fi.alk.studio._environment_input_types import _canonical_json, _canonical_value

from .scenario import SOURCE_FIXTURE, Scenario

_WORDS = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_STOP = {
    "about",
    "approved",
    "available",
    "before",
    "case",
    "check",
    "compliant",
    "confirms",
    "described",
    "from",
    "into",
    "order",
    "outcome",
    "policy",
    "request",
    "resolution",
    "reports",
    "source",
    "status",
    "test",
    "tests",
    "that",
    "their",
    "this",
    "using",
    "with",
}
_SEMANTIC_FIELDS = (
    "instruction",
    "tests",
    "setup_code",
    "ready_code",
    "solution",
    "alternatives",
    "sub_goals",
    "variables",
)


def _tokens(value: object) -> set[str]:
    return {
        token.casefold()
        for token in _WORDS.findall(str(value))
        if len(token) >= 3 and token.casefold() not in _STOP
    }


def _case_terms(case: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    strong: set[str] = set()
    topic: set[str] = set()
    for fact in case.get("starting_facts") or ():
        if not isinstance(fact, Mapping):
            continue
        record = str(fact.get("record_id") or "")
        record_tokens = _tokens(record)
        if any(character.isdigit() for character in record):
            strong.update(record_tokens)
        topic.update(_tokens(fact.get("field")))
        source_tokens = _tokens(fact.get("source_id"))
        strong.update(source_tokens)
        topic.update(source_tokens)
        topic.update(_tokens(fact.get("value")))
    for citation in case.get("citations") or ():
        if isinstance(citation, Mapping):
            source_tokens = _tokens(citation.get("source_id"))
            strong.update(source_tokens)
            topic.update(source_tokens)
    policy_tokens = _tokens(case.get("policy_reference"))
    strong.update(token for token in policy_tokens if any(c.isdigit() for c in token))
    topic.update(policy_tokens)
    return strong, topic


def scenario_relates_to_case(args: Mapping[str, Any], case: Mapping[str, Any]) -> bool:
    authored_values = dict(args)
    authored_values["instruction"] = str(args.get("instruction") or "").split(
        "\n\nReviewed context:", 1
    )[0]
    authored_values["tests"] = str(args.get("tests") or "").split(
        "\n\nReviewed source expectations:", 1
    )[0]
    variables = dict(args.get("variables") or {})
    variables.pop("reviewed_source_context", None)
    authored_values["variables"] = variables
    authored = _tokens(
        [
            authored_values.get(field)
            for field in _SEMANTIC_FIELDS
            if authored_values.get(field) not in (None, "", [], {})
        ]
    )
    strong, topic = _case_terms(case)
    return bool(authored & strong) or len(authored & topic) >= 2


def safe_source_context(case: Mapping[str, Any]) -> str:
    records = sorted(
        {
            str(fact.get("record_id"))
            for fact in case.get("starting_facts") or ()
            if isinstance(fact, Mapping) and fact.get("record_id")
        }
    )
    sources = sorted(
        {
            str(citation.get("source_id"))
            for citation in case.get("citations") or ()
            if isinstance(citation, Mapping) and citation.get("source_id")
        }
    )
    parts = []
    if records:
        parts.append("record " + ", ".join(records))
    if sources:
        parts.append("approved source " + ", ".join(sources))
    return "; ".join(parts) or "the approved source"


def expected_source_semantics(case: Mapping[str, Any]) -> str:
    public = {
        key: case.get(key)
        for key in ("category", "starting_facts", "policy_reference", "citations")
    }
    return "Reviewed source expectations: " + _canonical_json(public)


def bind_source_case(
    args: Mapping[str, Any], case: Mapping[str, Any], *, require_relevance: bool = True
) -> dict[str, Any]:
    if require_relevance and not scenario_relates_to_case(args, case):
        raise ValueError("source_case_unrelated")
    bound = dict(args)
    context = safe_source_context(case)
    instruction = str(bound.get("instruction") or "").strip()
    marker = f"Reviewed context: {context}."
    if marker not in instruction:
        bound["instruction"] = (instruction + "\n\n" + marker).strip()
    variables = dict(bound.get("variables") or {})
    variables["reviewed_source_context"] = context
    bound["variables"] = variables
    tests = str(bound.get("tests") or "").strip()
    expected = expected_source_semantics(case)
    if expected not in tests:
        bound["tests"] = (tests + "\n\n" + expected).strip()
    fixture = dict(bound.get("fixture") or {})
    fixture[SOURCE_FIXTURE] = _canonical_value(case)
    bound["fixture"] = fixture
    return bound


def semantic_source_digest(
    case: Mapping[str, Any], scenario: Scenario | Mapping[str, Any]
) -> str:
    values = (
        scenario.model_dump(mode="json")
        if isinstance(scenario, Scenario)
        else dict(scenario)
    )
    setup = str(values.get("setup_code") or "")
    ready = str(values.get("ready_code") or "")
    if "This scenario runs on the base world unchanged." in setup:
        setup = ""
    if "Nothing beyond the base world is presumed." in ready:
        ready = ""
    body = {
        "case": _canonical_value(case),
        "scenario": {
            **{field: values.get(field) for field in _SEMANTIC_FIELDS},
            "setup_code": setup,
            "ready_code": ready,
        },
    }
    return "sha256:" + hashlib.sha256(_canonical_json(body).encode()).hexdigest()


def validate_bound_scenario(scenario: Scenario, case: Mapping[str, Any]) -> bool:
    values = scenario.model_dump(mode="json")
    if not scenario_relates_to_case(values, case):
        return False
    expected = bind_source_case(values, case, require_relevance=False)
    return all(
        values.get(field) == expected.get(field) for field in _SEMANTIC_FIELDS
    )
