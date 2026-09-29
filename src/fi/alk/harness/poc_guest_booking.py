"""Temporary authoring policy for the Uber Guest Booking POC.

The policy is deliberately opt-in through a platform-owned target number.  A customer job can
name a phone target, but it cannot activate this policy unless that target exactly matches the
deployment setting delivered through the simulator-secret channel.

This module only briefs scenario authoring.  It does not rewrite saved scenarios or intercept a
live caller's turns, which keeps the simulator free to behave naturally from each authored
persona's facts.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

from .job import HarnessJob, ProviderExecutionMode

TARGET_PHONE_ENV = "ALK_UBER_GUEST_POC_TARGET_PHONE_NUMBER"
PIN_ENV = "ALK_UBER_GUEST_POC_PIN"
DEFAULT_PIN = "7682"

_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
_PIN = re.compile(r"^\d{4}$")
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


def guest_booking_pin_guidance(
    job: HarnessJob | None,
    *,
    scenario_count: int,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return the POC-only scenario brief, or an empty string when the target does not match."""
    if job is None:
        return ""
    values = os.environ if environ is None else environ
    configured_target = str(values.get(TARGET_PHONE_ENV) or "").strip()
    if not _E164.fullmatch(configured_target):
        return ""
    if job.agent.connector.strip().lower() != "phone":
        return ""
    if job.agent.mode is not ProviderExecutionMode.CONNECT_ONLY:
        return ""
    submitted_target = str(job.agent.config.get("phone_number") or "").strip()
    if submitted_target != configured_target:
        return ""

    pin = str(values.get(PIN_ENV) or DEFAULT_PIN).strip()
    if not _PIN.fullmatch(pin):
        # A malformed private deployment setting must fail closed rather than leaking a partial
        # credential into authored caller facts.
        return ""

    counts = _case_counts(scenario_count)
    return f"""
## Temporary Uber Guest Booking POC: caller PIN behavior

This private policy applies to this target only. Treat PIN behavior as an orthogonal caller fact,
not as the subject of every scenario: preserve broad coverage of the agent prompt and let the
primary ride-booking or robustness flow continue after the PIN exchange.

Across the complete saved suite of {scenario_count} scenarios, author exactly this allocation:
- `valid`: {counts["valid"]} scenarios ({_CASES[0][1]}%). The caller knows `{pin}`.
- `wrong`: {counts["wrong"]} scenarios ({_CASES[1][1]}%). The caller supplies one plausible but
  incorrect four-digit PIN and must not later invent the valid PIN.
- `missing`: {counts["missing"]} scenarios ({_CASES[2][1]}%). The caller does not know the PIN and
  says so naturally when asked; it must not infer one from any phone number.
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
    "DEFAULT_PIN",
    "PIN_ENV",
    "TARGET_PHONE_ENV",
    "guest_booking_pin_guidance",
]
