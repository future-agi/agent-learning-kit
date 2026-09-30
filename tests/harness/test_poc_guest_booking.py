from __future__ import annotations

from fi.alk.harness.job import (
    AgentConnection,
    ExecutionMode,
    HarnessJob,
    ProviderExecutionMode,
    RepositorySource,
    SourceKind,
)
from fi.alk.harness.poc_guest_booking import (
    PIN_ENV,
    TARGET_PHONE_ENV,
    guest_booking_pin_guidance,
    guest_booking_pin_scenario_problem,
)


TARGET = "+15551234567"


def _job(*, phone_number: str = TARGET, connector: str = "phone") -> HarnessJob:
    return HarnessJob(
        job_id="job",
        run_id="run",
        execution=ExecutionMode.LOCAL,
        source=RepositorySource(
            kind=SourceKind.LOCAL_REPOSITORY, local_path="/tmp/source"
        ),
        agent=AgentConnection(
            connector=connector,
            mode=(ProviderExecutionMode.CONNECT_ONLY if connector == "phone" else None),
            config=(
                {
                    "phone_number": phone_number,
                    "target_system_prompt": "You are a guest ride booking agent.",
                }
                if connector == "phone"
                else {}
            ),
        ),
        scenario_count=200,
    )


def test_policy_is_disabled_without_private_target_configuration() -> None:
    assert guest_booking_pin_guidance(_job(), scenario_count=200, environ={}) == ""


def test_policy_is_gated_to_the_exact_phone_target() -> None:
    guidance = guest_booking_pin_guidance(
        _job(phone_number="+15557654321"),
        scenario_count=200,
        environ={TARGET_PHONE_ENV: TARGET},
    )

    assert guidance == ""


def test_policy_authors_exact_500_scenario_mix_with_default_pin() -> None:
    guidance = guest_booking_pin_guidance(
        _job(), scenario_count=500, environ={TARGET_PHONE_ENV: TARGET}
    )

    assert "`valid`: 400 scenarios (80%)" in guidance
    assert "`wrong`: 50 scenarios (10%)" in guidance
    assert "`missing`: 25 scenarios (5%)" in guidance
    assert "`wrong_then_correct`: 25 scenarios (5%)" in guidance
    assert "caller knows `7682`" in guidance
    assert "must never volunteer a PIN before the agent asks" in guidance
    assert "do not repeat it on unrelated turns" in guidance


def test_policy_accepts_private_pin_override_and_rejects_invalid_pin() -> None:
    overridden = guest_booking_pin_guidance(
        _job(),
        scenario_count=20,
        environ={TARGET_PHONE_ENV: TARGET, PIN_ENV: "1234"},
    )
    invalid = guest_booking_pin_guidance(
        _job(),
        scenario_count=20,
        environ={TARGET_PHONE_ENV: TARGET, PIN_ENV: "12"},
    )

    assert "caller knows `1234`" in overridden
    assert invalid == ""


def test_ten_scenario_brief_uses_exact_integer_mix() -> None:
    guidance = guest_booking_pin_guidance(
        _job(),
        scenario_count=10,
        environ={TARGET_PHONE_ENV: TARGET, PIN_ENV: "7682"},
    )

    assert "`valid`: 8 scenarios (80%)" in guidance
    assert "`wrong`: 1 scenarios (10%)" in guidance
    assert "`missing`: 1 scenarios (5%)" in guidance
    assert "`wrong_then_correct`: 0 scenarios (5%)" in guidance
    assert "caller knows `7682`" in guidance


def test_wrong_pin_scenario_cannot_name_valid_pin_even_to_forbid_it() -> None:
    problem = guest_booking_pin_scenario_problem(
        _job(),
        {
            "instruction": "Speak 4821 when asked. Do not guess or invent 7682.",
            "fixture": {"guest_pin_case": "wrong", "guest_pin": "4821"},
        },
        environ={TARGET_PHONE_ENV: TARGET, PIN_ENV: "7682"},
    )

    assert "must not contain the configured valid PIN" in problem
    assert "7682" not in problem


def test_wrong_pin_scenario_without_valid_pin_is_accepted() -> None:
    assert guest_booking_pin_scenario_problem(
        _job(),
        {
            "instruction": "Speak 4821 when asked; you do not know another PIN.",
            "fixture": {"guest_pin_case": "wrong", "guest_pin": "4821"},
        },
        environ={TARGET_PHONE_ENV: TARGET, PIN_ENV: "7682"},
    ) == ""


def test_missing_pin_scenario_cannot_hide_valid_pin_in_persona() -> None:
    assert guest_booking_pin_scenario_problem(
        _job(),
        {
            "persona": {"initial_message": "My PIN might be 7682"},
            "fixture": {"guest_pin_case": "missing"},
        },
        environ={TARGET_PHONE_ENV: TARGET, PIN_ENV: "7682"},
    )


def test_guest_pin_scenario_guard_does_not_affect_other_phone_targets() -> None:
    assert guest_booking_pin_scenario_problem(
        _job(phone_number="+15557654321"),
        {"instruction": "Do not guess 7682", "fixture": {"guest_pin_case": "wrong"}},
        environ={TARGET_PHONE_ENV: TARGET, PIN_ENV: "7682"},
    ) == ""


def test_policy_is_not_applied_to_other_connector_types() -> None:
    assert (
        guest_booking_pin_guidance(
            _job(connector="auto"),
            scenario_count=200,
            environ={TARGET_PHONE_ENV: TARGET},
        )
        == ""
    )
