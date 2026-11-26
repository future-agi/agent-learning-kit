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
    terms = set()
    for token in _WORDS.findall(str(value).casefold()):
        if len(token) < 3 and not (token.isdigit() and len(token) >= 2):
            continue
        # A caller can ask for one refund under a rule governing refunds.
        if len(token) > 4 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
            token = token[:-1]
        if token not in _STOP:
            terms.add(token)
    return terms


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
        topic.update(_tokens(str(fact.get("field") or "").rsplit("/", 1)[-1]))
        topic.update(_tokens(fact.get("value")))
    policy_tokens = _tokens(case.get("policy_reference"))
    strong.update(token for token in policy_tokens if any(c.isdigit() for c in token))
    topic.update(policy_tokens)
    return strong, topic


def _identifier_pattern(identifier: str) -> str:
    return r"(?<![\w-])" + re.escape(identifier.casefold()) + r"(?![\w-])"


_REFUSAL = (
    r"\b(?:den(?:y|ies|ied|ying)|reject(?:s|ed)?|refus(?:e|es|ed)|declin(?:e|es|ed)|"
    r"ineligible|too late|not eligible|cannot (?:return|refund))\b"
)
_APPROVAL = (
    r"\b(?:accept(?:s|ed)?|approv(?:e|es|ed)|eligible|"
    r"(?:issu(?:e|es|ed)|process(?:es|ed)?|grant(?:s|ed)?) (?:a |the )?(?:standard |full )?(?:return|refund))\b"
)
_ESCALATION = r"\b(?:escalat\w*|rout\w*|refer\w*|hand(?:s)? off)\b"
_FULL_REFUND = r"full refund|refund(?:ed)? in full|100\s*(?:%|percent) refund"
_PREPAID = r"(?:pre[- ]?paid|free) (?:return )?(?:shipping|label|postage)|return shipping at no cost"
_DAY_WORDS = (
    "zero one two three four five six seven eight nine ten eleven twelve "
    "thirteen fourteen fifteen sixteen seventeen eighteen nineteen"
).split()


def _affirmed(text: str, expression: str) -> bool:
    """Discard a required outcome if the test only says to withhold it."""
    for match in re.finditer(expression, text):
        before = text[:match.start()]
        if not re.search(
            r"(?:\b(?:no|not|never|without|cannot|deny|denies|refuses?|rejects?)|n['’]t) "
            r"(?:(?:give|offer|issue|provide|grant|approve|allow|process|authorize|apply|charge|to|a|an|the|any) )*$", before,
        ):
            return True
    return False


SOURCE_OUTCOME_GUIDANCE = (
    "Write the specific decision and every promised outcome in the test itself. "
    "Include the applicable fee, service, turnaround, escalation code and shipping terms when supplied. "
    "Looking up a record, starting a workflow or reciting a policy alone does not test its outcome. "
)


def _return_terms_match(policy: str, test: str, situation: str) -> bool:
    # Use the simulated day to select the applicable side of a return deadline.
    window = re.search(r"(?:through|up to) day (\d+).*?day (\d+) is late", policy)
    if window:
        days = re.findall(r"\bday\s+(\d+)|\b(\d+) days? (?:ago|after delivery)", situation)
        days = [int(left or right) for left, right in days]
        if days and min(days) >= int(window[2]):
            if not _affirmed(test, _REFUSAL):
                return False
        elif days and max(days) <= int(window[1]):
            if not _affirmed(test, _APPROVAL):
                return False
        elif not (
            re.search(rf"\b{window[1]}[ -]day|\bday {window[1]}\b", test)
            and (_affirmed(test, _REFUSAL) or _affirmed(test, _APPROVAL))
        ):
            return False
    fee = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent) restocking fee", policy)
    if fee:
        rate = rf"(?<![.\d]){re.escape(fee[1])}\s*(?:%|percent\b)"
        if "restocking" not in test or not _affirmed(test, rate):
            return False
    if _affirmed(policy, _FULL_REFUND) and not _affirmed(test, _FULL_REFUND):
        return False
    # Extracted PDF lines may split the last word of “prepaid return shipping”.
    if _affirmed(policy, r"pre[- ]?paid (?:return|shipping|postage|label)|free return shipping") and not _affirmed(test, _PREPAID):
        return False
    if "final sale unless" in policy:
        damaged = "damag" in situation and not re.search(r"undamaged|not damaged|without damage", situation)
        return bool(re.search(r"final sale unless|exception.*damag", test)
                    or (damaged and _affirmed(test, _APPROVAL))
                    or (not damaged and _affirmed(test, _REFUSAL)))
    return True


def _policy_assertion_matches(test_text: str, situation: str, case: Mapping[str, Any]) -> bool:
    named_codes = re.findall(
        r"\b(?:code|label)\s+(?:is\s+)?([A-Z]{2,}-[0-9]+)\b",
        str(case.get("policy_reference") or ""),
        flags=re.IGNORECASE,
    )
    if any(not re.search(_identifier_pattern(code), test_text) for code in named_codes):
        return False
    score = re.search(
        r"\brelevance score of (\d+)\b", str(case.get("policy_reference") or ""), re.I
    )
    if score and not re.search(
        rf"\bscore\b\s*(?:of\s+|is\s+|[:=]\s*)?{score[1]}\b(?![.,]\d)",
        test_text,
    ):
        return False
    # An ordinary success inside the window also passes when a forbidden
    # extension is granted. The assertion must distinguish those behaviors.
    policy = str(case.get("policy_reference") or "").casefold()
    if re.search(r"\bno (?:general )?extensions?\b", policy):
        if not re.search(
            r"\b(?:den(?:y|ies|ied|ying)|reject(?:s|ed)?|refus(?:e|es|ed|ing)|declin(?:e|es|ed|ing)|"
            r"ineligible|too late|no (?:general )?(?:exceptions?|extensions?)|cannot extend|"
            r"(?:does not|doesn't|will not|won't|cannot) (?:approve|grant|extend))\b",
            test_text,
        ):
            return False
    return _return_terms_match(" ".join(policy.split()), test_text, situation)


def _record_fields(case: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(fact.get("field", "")).rsplit("/", 1)[-1]: fact.get("value")
        for fact in case.get("starting_facts") or ()
        if isinstance(fact, Mapping)
    }


def _action_codes(fields: Mapping[str, Any]) -> list[str]:
    return re.findall(r"(?<![A-Z0-9])[A-Z][A-Z0-9]+-[0-9]+(?![A-Z0-9])",
                      str(fields.get("expected_action") or ""))


def reviewed_action_arguments(case: Mapping[str, Any], arguments: Mapping[str, Any]) -> set[str]:
    fields = _record_fields(case)
    codes = set(_action_codes(fields))
    if not codes:
        return set()
    action = fields["expected_action"]
    return {
        key for key, value in arguments.items() if isinstance(value, str)
        and (value in codes or value == action)
        and (key in {"action", "expected_action", "code", "identifier"}
             or key.endswith(("_code", "_identifier")))
    }


def action_code_guidance(cases: list[dict[str, Any]]) -> str:
    examples = dict.fromkeys(
        str(fields["expected_action"]) for case in cases
        if _action_codes(fields := _record_fields(case))
    )
    if not examples:
        return ""
    return ("Preserve expected_action values as written: " + ", ".join(list(examples)[:12])
            + ". Treat an action suffix as its identifier. Keep it in an action or code argument "
              "(for escalation, give the tool an escalation_code argument) and assert that identifier in the test. The outcome check must reject a different code: compare the stored result against the approved record or fixed reference arguments. Only assign another role if its field "
              "explicitly supplies that value.")


def record_role_problem(text: str, case: Mapping[str, Any], steps=()) -> str:
    fields = _record_fields(case)
    normalized = text.casefold().replace("_", " ")
    for code in _action_codes(fields):
        exact = _identifier_pattern(code)
        for match in re.finditer(exact, normalized):
            before = normalized[:match.start()].rstrip(" `*'\"")
            after = normalized[match.end():].lstrip(" `*'\"")
            for role in ("queue", "priority", "reason", "destination", "team"):
                if any(role in key.split("_") and str(value).casefold() == code.casefold()
                       for key, value in fields.items()):
                    continue
                if (re.search(r"\b" + role + r"s?\s*(?:[ (:,=]*(?:named|is|of|like|such as|e\.g\.)?[ (:,=]*)$", before)
                    or re.match(role + r"\b", after)
                    or (role == "destination" and re.search(r"\bto$", before)
                        and not any(str(fields.get(k, "")).casefold() == code.casefold()
                                    for k in ("queue", "team", "destination")))):
                    return f"Use {code} as the identifier of expected_action {fields['expected_action']}. The record does not identify it as a {role}."
        for step in steps or ():
            if not isinstance(step, Mapping):
                continue
            for key, value in (step.get("arguments") or {}).items():
                if (str(value).casefold() == code.casefold()
                    and key not in ("action", "expected_action", "code", "identifier")
                    and not key.endswith(("_code", "_identifier"))
                    and str(fields.get(key, "")).casefold() != code.casefold()):
                    return f"Carry {code} in the action or code argument. The record does not give it as {key}."
    return ""


def _record_assertion_matches(test_text: str, instruction: str, case: Mapping[str, Any]) -> bool:
    fields = _record_fields(case)
    authored = test_text + " " + instruction
    action = str(fields.get("expected_action") or "")
    for identifier in _action_codes(fields):
        if not re.search(_identifier_pattern(identifier), test_text.replace("_", " ")):
            return False
    if action and not case.get("policy_reference"):
        # Elapsed days describe this record. A numerical policy window needs
        # its own source field, even when the correct action is a late denial.
        limits = re.finditer(
            r"\b(\d+)[ -]days? (?:return )?(?:window|deadline|limit)\b"
            r"|\b(?:past|beyond|outside|exceeds?) (\d+)[ -]days?\b",
            authored,
        )
        for limit in limits:
            days = limit[1] or limit[2]
            if not any(
                (str(value) == days and re.search(r"window|limit|deadline", key, re.I))
                or limit[0] in str(value).casefold()
                for key, value in fields.items()
            ):
                return False
    service = fields.get("service")
    if isinstance(service, str) and not all(
        _affirmed(test_text, _identifier_pattern(word)) for word in _WORDS.findall(service.casefold())
    ):
        return False
    eta = fields.get("eta")
    if eta and not _affirmed(test_text, _identifier_pattern(str(eta))):
        return False
    processing = fields.get("processing_days")
    if processing is not None and str(processing).isdigit():
        days = int(processing)
        numbers = f"{days}|{_DAY_WORDS[days]}" if days < len(_DAY_WORDS) else str(days)
        duration = rf"(?<![.\d])\b(?:{numbers})[ -](?:(?:business|working|calendar) )?days?\b"
        if not _affirmed(test_text, duration):
            return False
    if action:
        action_words = action.casefold().replace("_", " ")
        verb = action_words.partition(" ")[0]
        outcome = {"deny": _REFUSAL, "approve": _APPROVAL, "escalate": _ESCALATION}.get(verb)
        if outcome and not _affirmed(test_text, outcome):
            return False
        if not _return_terms_match(action_words, test_text, instruction):
            return False
    if "queue" in fields:
        check_words = _WORDS.findall(test_text)
        for key in ("queue", "priority", "code"):
            if fields.get(key) is None:
                continue
            expected = _WORDS.findall(str(fields[key]).casefold())
            if expected and not any(
                check_words[i : i + len(expected)] == expected
                for i in range(len(check_words) - len(expected) + 1)
            ):
                return False
    return True


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
    if not (_tokens(authored_values["instruction"]) & (strong | topic)):
        return False
    test_text = authored_values["tests"].casefold()
    situation = authored_values["instruction"].casefold()
    if not _policy_assertion_matches(test_text, situation, case) or not _record_assertion_matches(test_text, situation, case):
        return False
    if record_role_problem(test_text + " " + authored_values["instruction"], case, args.get("solution")):
        return False
    checks = _tokens(authored_values["tests"])
    return bool(checks & topic) and (bool(authored & strong) or len(authored & topic) >= 2)


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


def bind_source_case(
    args: Mapping[str, Any], case: Mapping[str, Any], *, require_relevance: bool = True
) -> dict[str, Any]:
    if require_relevance:
        fields = _record_fields(case)
        codes = set(_action_codes(fields))
        for field in ("code", "service_code", "escalation_code", "qualification_label"):
            codes.update(
                re.findall(
                    r"\b[A-Z][A-Z0-9]+-\d+\b", str(fields.get(field) or ""), re.I
                )
            )
        codes.update(
            re.findall(
                r"\b(?:code|label)\s+(?:is\s+)?([A-Z][A-Z0-9]+-\d+)\b",
                str(case.get("policy_reference") or ""),
                re.I,
            )
        )
        visible = " ".join(
            str(args.get(k) or "") for k in ("name", "instruction", "use_case")
        )
        if fields.get("condition") and fields.get("queue"):
            queue = r"[ _-]+".join(
                re.escape(word) for word in re.split(r"[-_ ]+", str(fields["queue"]))
            )
            if re.search(r"(?<![a-z0-9])" + queue + r"(?![a-z0-9])", visible, re.I):
                raise ValueError("source_case_answer_in_situation")
            priority = re.escape(str(fields.get("priority") or ""))
            if priority and re.search(
                r"\b(?:"
                + priority
                + r"[ -]+priority|priority\s*[:=]?\s*"
                + priority
                + r")\b",
                visible,
                re.I,
            ):
                raise ValueError("source_case_answer_in_situation")
        score = re.search(
            r"\brelevance score of (\d+)\b",
            str(case.get("policy_reference") or ""),
            re.I,
        )
        if score and re.search(
            r"\bscore\s*(?:of|is|=|:)?\s*" + score[1] + r"\b", visible, re.I
        ):
            raise ValueError("source_case_answer_in_situation")
        for code in codes:
            letters, digits = code.rsplit("-", 1)
            if re.search(
                r"(?<![A-Za-z0-9])"
                + re.escape(letters)
                + r"[-_ ]?"
                + re.escape(digits)
                + r"(?![A-Za-z0-9])",
                visible,
                re.I,
            ):
                raise ValueError("source_case_answer_in_situation")
    if require_relevance and not scenario_relates_to_case(args, case):
        raise ValueError("source_case_unrelated")
    bound = dict(args)
    if bound.get("solution"):
        steps = []
        for step in bound["solution"]:
            marked = (
                reviewed_action_arguments(case, step.get("arguments") or {})
                if isinstance(step, Mapping)
                else set()
            )
            if marked:
                fixed = dict(step.get("fixed") or {})
                for key in marked:
                    fixed[key] = "record: approved expected_action"
                step = {**step, "fixed": fixed}
            steps.append(step)
        bound["solution"] = steps
    context = safe_source_context(case)
    variables = dict(bound.get("variables") or {})
    variables["reviewed_source_context"] = context
    bound["variables"] = variables
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
