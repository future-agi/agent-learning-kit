"""Temporary authoring policy for a guest-booking phone POC.

The policy is deliberately opt-in through a platform-owned target number.  A customer job can
name a phone target, but it cannot activate this policy unless that target exactly matches the
deployment setting delivered through the simulator-secret channel.

This module briefs and validates scenario authoring. It does not rewrite saved scenarios or
intercept live caller turns.
"""

from __future__ import annotations

import json
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
_CASES: tuple[tuple[str, int], ...] = (
    ("valid", 80),
    ("wrong", 10),
    ("missing", 5),
    ("wrong_then_correct", 5),
)


def _case_counts(total: int) -> dict[str, int]:
    """Allocate integer counts with largest remainders; exact for multiples of twenty."""
    total = max(int(total), 0)
    counts = {name: total * percent // 100 for name, percent in _CASES}
    remaining = total - sum(counts.values())
    remainders = sorted(
        _CASES,
        key=lambda item: (-(total * item[1] % 100), -item[1]),
    )
    for name, _percent in remainders[:remaining]:
        counts[name] += 1
    return counts


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


def _mentions_pin(value: object, pin: str) -> bool:
    """Find numeric or spoken PINs without mistaking a formatted phone number for one."""
    text = json.dumps(value, ensure_ascii=False, default=str)
    # Phone-shaped values are context, not a caller's four-digit PIN.
    text = re.sub(r"(?<!\w)(?:\+?\d[\d\s().-]{8,}\d)(?!\w)", " ", text)
    tokens = re.findall(r"\d|[a-z]+", text.lower())
    digits = [_DIGIT_WORDS.get(token, token) for token in tokens]
    for index in range(len(digits) - 3):
        if "".join(digits[index : index + 4]) == pin:
            # Adjacent digits are one longer number, not an isolated PIN.
            if index and digits[index - 1].isdigit():
                continue
            if index + 4 < len(digits) and digits[index + 4].isdigit():
                continue
            return True
    return False


def guest_booking_pin_scenario_problem(
    job: HarnessJob | None,
    scenario: Mapping[str, object],
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Reject POC scenarios that reveal the valid PIN to wrong/missing callers."""
    pin = _active_pin(job, os.environ if environ is None else environ)
    if pin is None:
        return ""
    fixture = scenario.get("fixture")
    if not isinstance(fixture, dict):
        return (
            "Not kept. The private PIN policy requires a fixture with guest_pin_case."
        )
    raw_case = fixture.get("guest_pin_case")
    case = raw_case.strip().lower() if isinstance(raw_case, str) else ""
    if case not in {name for name, _ in _CASES}:
        return "Not kept. Set fixture.guest_pin_case to valid, wrong, missing, or wrong_then_correct."
    if raw_case != case:
        return "Not kept. Use the lowercase guest_pin_case label without surrounding spaces."
    if case in {"wrong", "missing"} and _mentions_pin(scenario, pin):
        return (
            "Not kept. A wrong/missing-PIN scenario must not contain the configured valid "
            "PIN anywhere, including in a negated instruction. Remove it entirely and say "
            "'another PIN' without naming it."
        )
    if case == "missing" and any(key in fixture for key in _PIN_FIELDS):
        return (
            "Not kept. A missing-PIN scenario must not supply any PIN in its fixture."
        )
    if case == "valid" and (
        fixture.get("guest_pin") != pin
        or any(key in fixture for key in _PIN_FIELDS[1:])
    ):
        return "Not kept. A valid-PIN scenario must supply the configured PIN in fixture.guest_pin."
    if case == "wrong" and not (
        isinstance(fixture.get("guest_pin"), str)
        and _PIN.fullmatch(fixture["guest_pin"])
        and fixture["guest_pin"] != pin
        and not any(key in fixture for key in _PIN_FIELDS[1:])
    ):
        return "Not kept. A wrong-PIN scenario must supply a different four-digit fixture.guest_pin."
    if case == "wrong_then_correct" and not (
        isinstance(fixture.get("initial_guest_pin"), str)
        and _PIN.fullmatch(fixture["initial_guest_pin"])
        and fixture["initial_guest_pin"] != pin
        and fixture.get("corrected_guest_pin") == pin
        and "guest_pin" not in fixture
    ):
        return "Not kept. Supply an incorrect initial_guest_pin and the configured corrected_guest_pin."
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

    counts = _case_counts(scenario_count)
    return f"""
## Temporary guest-booking POC: caller PIN behavior

This private policy applies to this target only. Treat PIN behavior as an orthogonal caller fact,
not as the subject of every scenario: preserve broad coverage of the agent prompt and let the
primary ride-booking or robustness flow continue after the PIN exchange.

Across the complete saved suite of {scenario_count} scenarios, author exactly this allocation:
- `valid`: {counts["valid"]} scenarios ({_CASES[0][1]}%). The caller knows `{pin}`.
- `wrong`: {counts["wrong"]} scenarios ({_CASES[1][1]}%). The caller supplies one plausible but
  incorrect four-digit PIN and must not later invent the valid PIN. Do not put the valid PIN in
  this scenario's instruction, persona, fixture, variables, checks or any other field, even in
  a negative sentence like "do not guess [the valid PIN]"; say "another PIN" instead.
- `missing`: {counts["missing"]} scenarios ({_CASES[2][1]}%). The caller does not know the PIN and
  says so naturally when asked; it must not infer one from any phone number. Do not put the valid
  PIN anywhere in this scenario either.
- `wrong_then_correct`: {counts["wrong_then_correct"]} scenarios ({_CASES[3][1]}%). The caller first
  supplies a plausible incorrect four-digit PIN, then supplies `{pin}` only after the agent rejects
  it or explicitly asks the caller to try again.

Before dispatching scenario writers, allocate these exact case totals across their slices and put
each slice's local case counts in that writer's brief. The slice allocations must sum to the suite
totals above. A writer follows its local allocation and reports the case count it actually authored;
it must not try to create the whole-suite totals inside its own slice.

Every scenario must declare its case in `fixture.guest_pin_case`. Put the applicable PIN fact(s) in
the fixture as `guest_pin`, or as `initial_guest_pin` and `corrected_guest_pin`; do not put a PIN in
the fixture for `missing`. Incorrect values must be four digits and must not equal `{pin}`.

The simulated caller must never volunteer a PIN before the agent asks. Once a PIN has been heard
and the conversation advances, do not repeat it on unrelated turns. A valid-PIN scenario should say
`{pin}` once on the first explicit request, repeating it only if the agent clearly says it did not
hear it or explicitly requests it again. These rules belong in the scenario's natural circumstance
and fixture, not in a scripted list of lines for the caller to recite.
""".strip()


__all__ = [
    "PIN_ENV",
    "TARGET_PHONE_ENV",
    "guest_booking_pin_guidance",
    "guest_booking_pin_scenario_problem",
]
