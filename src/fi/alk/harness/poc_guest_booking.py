"""Temporary authoring policy for a guest-booking phone POC.

The policy is deliberately opt-in through a platform-owned target number.  A customer job can
name a phone target, but it cannot activate this policy unless that target exactly matches the
deployment setting delivered through the simulator-secret channel.

This module briefs and validates scenario authoring. It does not rewrite saved scenarios or
intercept live caller turns.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Mapping

from .job import HarnessJob, ProviderExecutionMode

TARGET_PHONE_ENV = "ALK_CAB_GUEST_POC_TARGET_PHONE_NUMBER"
PIN_ENV = "ALK_CAB_GUEST_POC_PIN"
_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
_PIN = re.compile(r"^\d{4}$")
_PIN_FIELDS = ("guest_pin", "initial_guest_pin", "corrected_guest_pin")
_DIGIT_WORDS = {
    word: str(value)
    for value, word in enumerate(
        ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
    )
}
_CASES = frozenset({"valid", "wrong", "missing", "wrong_then_correct"})
logger = logging.getLogger(__name__)


def _authored_pin_literal(scenario: Mapping[str, object]) -> bool:
    """Whether authoring wrote a four-digit value specifically as a PIN.

    Addresses, times and phone numbers are legitimate scenario details. Only a
    four-digit token tied directly to the word PIN is a private-fixture leak.
    """
    searchable = " ".join(
        json.dumps(scenario.get(key), ensure_ascii=False, default=str)
        for key in ("name", "use_case", "branch", "tests", "instruction", "keywords")
    ).lower()
    searchable = re.sub(
        r"(?<!\w)\+?\d[\d\s().-]*\d(?!\w)",
        lambda match: (
            " "
            if sum(char.isdigit() for char in match.group()) >= 10
            else match.group()
        ),
        searchable,
    )
    return bool(
        re.search(r"\bpin\b[^\d\n]{0,24}\b\d{4}\b", searchable)
        or re.search(r"\b\d{4}\b[^\d\n]{0,24}\bpin\b", searchable)
    )


def _missing_case_later_provides_pin(scenario: Mapping[str, object]) -> bool:
    searchable = " ".join(
        json.dumps(scenario.get(key), ensure_ascii=False, default=str)
        for key in ("branch", "tests", "instruction", "keywords")
    ).lower()
    return bool(
        re.search(
            r"\b(?:provide|give|share|speak|say|read)\b.{0,32}\b(?:your|the|a|my)?\s*"
            r"(?:4[ -]?digit\s+)?pin\b",
            searchable,
        )
    )


def _scenario_pin_case(scenario: Mapping[str, object]) -> str:
    """Read an authored PIN condition without making PIN a suite-planning axis."""
    fixture = scenario.get("fixture")
    raw = fixture.get("guest_pin_case") if isinstance(fixture, Mapping) else None
    if isinstance(raw, str) and raw.strip():
        return raw.strip().lower()

    searchable = " ".join(
        json.dumps(scenario.get(key), ensure_ascii=False, default=str)
        for key in ("name", "use_case", "branch", "tests", "instruction", "keywords")
    ).lower()
    compact = searchable.replace("-", " ").replace("_", " ")
    wrong = bool(
        re.search(r"\b(?:wrong|incorrect|invalid)\b.{0,24}\bpin\b", compact)
        or re.search(r"\bpin\b.{0,24}\b(?:wrong|incorrect|invalid)\b", compact)
    )
    corrected = bool(
        re.search(r"\b(?:correct|corrected|correction|retry|second attempt)\b", compact)
    )
    if wrong and corrected:
        return "wrong_then_correct"
    missing = bool(
        re.search(
            r"\b(?:missing|forgot|forgotten|unknown|no|does not know|doesn't know|"
            r"cannot find|can't find)\b.{0,28}\bpin\b",
            compact,
        )
        or re.search(
            r"\bwithout\b\s+(?:(?:a|the|my|their|guest|4[ -]?digit)\s+){0,2}\bpin\b",
            compact,
        )
        or re.search(
            r"\bpin\b.{0,28}\b(?:missing|forgot|forgotten|unknown|unavailable|not known)\b",
            compact,
        )
    )
    if missing:
        return "missing"
    if wrong:
        return "wrong"
    return "valid"


def _wrong_pin(pin: str, scenario: Mapping[str, object]) -> str:
    """Choose a stable plausible wrong PIN without exposing or deriving from the real one."""
    seed = str(scenario.get("name") or scenario.get("instruction") or "guest-pin")
    candidate = 1000 + int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) % 9000
    if str(candidate) == pin:
        candidate = 1000 + (candidate - 999) % 9000
    return str(candidate)


def _active_pin(job: HarnessJob | None, values: Mapping[str, str]) -> str | None:
    if job is None:
        return None
    configured_target = str(values.get(TARGET_PHONE_ENV) or "").strip()
    if not _E164.fullmatch(configured_target):
        return None
    if job.agent.connector.strip().lower() != "phone":
        return None
    if job.agent.mode is not ProviderExecutionMode.CONNECT_ONLY:
        return None
    submitted_target = str(job.agent.config.get("phone_number") or "").strip()
    if submitted_target != configured_target:
        return None
    pin = str(values.get(PIN_ENV) or "").strip()
    return pin if _PIN.fullmatch(pin) else None


def guest_booking_policy_job(
    raw_job: HarnessJob | Mapping[str, object] | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> HarnessJob | None:
    """Validate a job only when the private phone-target policy could apply.

    Hosted chat accepts some platform payloads that the standalone SDK schema may reject. Those
    payloads must keep working for every ordinary target, so inspect the connector and target
    before model validation and fail open (policy disabled) if the matching POC payload is stale.
    """
    values = os.environ if environ is None else environ
    target = str(values.get(TARGET_PHONE_ENV) or "").strip()
    pin = str(values.get(PIN_ENV) or "").strip()
    if not _E164.fullmatch(target) or not _PIN.fullmatch(pin):
        return None
    if isinstance(raw_job, HarnessJob):
        return raw_job if _active_pin(raw_job, values) is not None else None
    if not isinstance(raw_job, Mapping):
        return None
    agent = raw_job.get("agent")
    if not isinstance(agent, Mapping):
        return None
    config = agent.get("config")
    if (
        str(agent.get("connector") or "").strip().lower() != "phone"
        or not isinstance(config, Mapping)
        or str(config.get("phone_number") or "").strip() != target
    ):
        return None
    try:
        job = HarnessJob.model_validate(raw_job)
    except ValueError as exc:
        logger.warning(
            "private guest PIN policy disabled for an incompatible matching job: %s",
            type(exc).__name__,
        )
        return None
    return job if _active_pin(job, values) is not None else None


def _mentions_pin(value: object, pin: str) -> bool:
    """Find numeric or spoken PINs without mistaking a formatted phone number for one."""
    text = json.dumps(value, ensure_ascii=False, default=str)
    # Phone-shaped values are context, not a caller's four-digit PIN. Count digits rather than
    # punctuation width so a short number padded with spaces is not mistaken for a phone number.
    text = re.sub(
        r"(?<!\w)\+?\d[\d\s().-]*\d(?!\w)",
        lambda match: (
            " "
            if sum(char.isdigit() for char in match.group()) >= 10
            else match.group()
        ),
        text,
    )
    tokens = list(re.finditer(r"\d|[a-z]+", text.lower()))
    digits = [_DIGIT_WORDS.get(match.group(), match.group()) for match in tokens]
    for index in range(len(tokens) - 3):
        if "".join(digits[index : index + 4]) == pin:
            # Only an immediately touching numeric character makes this part of a longer number.
            # A digit word or a separated number before/after the PIN is independent context.
            if (
                index
                and tokens[index - 1].group().isdigit()
                and tokens[index].group().isdigit()
                and tokens[index - 1].end() == tokens[index].start()
            ):
                continue
            if (
                index + 4 < len(tokens)
                and tokens[index + 3].group().isdigit()
                and tokens[index + 4].group().isdigit()
                and tokens[index + 3].end() == tokens[index + 4].start()
            ):
                continue
            return True
    return False


def guest_booking_pin_scenario_problem(
    job: HarnessJob | None,
    scenario: Mapping[str, object],
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Attach private PIN facts without changing what scenarios the suite contains."""
    pin = _active_pin(job, os.environ if environ is None else environ)
    if pin is None:
        return ""
    if not isinstance(scenario, dict):
        return "Not kept. The private PIN policy requires a scenario object."
    fixture = scenario.get("fixture")
    if not isinstance(fixture, dict):
        fixture = {}
        scenario["fixture"] = fixture
    raw_case = fixture.get("guest_pin_case")
    case = _scenario_pin_case(scenario)
    if case not in _CASES:
        return "Not kept. Set fixture.guest_pin_case to valid, wrong, missing, or wrong_then_correct."
    if isinstance(raw_case, str) and raw_case.strip() and raw_case != case:
        return "Not kept. Use the lowercase guest_pin_case label without surrounding spaces."
    if _authored_pin_literal(scenario):
        return (
            "Not kept. Do not write a PIN value in scenario prose. Describe when the caller "
            "shares or corrects the PIN; the private fixture supplies the exact value."
        )
    if case == "missing" and _missing_case_later_provides_pin(scenario):
        return (
            "Not kept. This scenario marks the caller's PIN as missing but later instructs "
            "them to provide a PIN. Keep the PIN missing, or use wrong_then_correct when the "
            "caller later finds the correct PIN."
        )
    if case in {"wrong", "missing"} and _mentions_pin(scenario, pin):
        return (
            "Not kept. A wrong/missing-PIN scenario must not contain the configured valid "
            "PIN anywhere, including in a negated instruction. Remove it entirely and say "
            "'another PIN' without naming it."
        )
    for key in _PIN_FIELDS:
        fixture.pop(key, None)
    fixture["guest_pin_case"] = case
    if case == "valid":
        fixture["guest_pin"] = pin
    elif case == "wrong":
        fixture["guest_pin"] = _wrong_pin(pin, scenario)
    elif case == "wrong_then_correct":
        fixture["initial_guest_pin"] = _wrong_pin(pin, scenario)
        fixture["corrected_guest_pin"] = pin
    return ""


def guest_booking_pin_guidance(
    job: HarnessJob | None,
    *,
    scenario_count: int,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return the POC-only scenario brief, or an empty string when the target does not match."""
    values = os.environ if environ is None else environ
    pin = _active_pin(job, values)
    if pin is None:
        return ""

    return """
## Temporary guest-booking POC: caller PIN behavior

This private policy supplies caller credentials; it is not a scenario category or coverage axis.
Plan and write the same natural distribution of ride-booking, feature, language, audio and
robustness scenarios you would write if this policy did not exist. Do not add, remove, rename,
rewrite or rebalance scenarios to achieve a PIN quota, and do not make PIN the primary subject of
an otherwise unrelated scenario.

Only when a scenario independently concerns a caller whose PIN is wrong, missing, or corrected
after rejection, mark `fixture.guest_pin_case` as `wrong`, `missing`, or `wrong_then_correct`.
Otherwise omit that field. Do not invent or write PIN values: the platform attaches the appropriate
private fact after submission. The simulated caller reveals that fact only when the agent asks and
does not repeat it after the conversation advances unless the agent says it was not heard or asks
again explicitly.
""".strip()


__all__ = [
    "PIN_ENV",
    "TARGET_PHONE_ENV",
    "guest_booking_policy_job",
    "guest_booking_pin_guidance",
    "guest_booking_pin_scenario_problem",
]
