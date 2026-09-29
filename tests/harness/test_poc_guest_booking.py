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


def test_policy_is_not_applied_to_other_connector_types() -> None:
    assert (
        guest_booking_pin_guidance(
            _job(connector="auto"),
            scenario_count=200,
            environ={TARGET_PHONE_ENV: TARGET},
        )
        == ""
    )
