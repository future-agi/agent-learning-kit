from __future__ import annotations

import argparse
import asyncio
from types import SimpleNamespace

from fi.alk.harness import cli
from fi.alk.harness.cli import build_parser
from fi.alk.harness.contract import AgentContract
from fi.alk.harness.scenarios import open_stage

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
    guest_booking_policy_job,
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


def test_policy_job_does_not_validate_unrelated_chat_payloads() -> None:
    invalid_generic_job = {
        "agent": {"connector": "phone", "config": {"phone_number": "+15557654321"}},
        "runtime": {"parallelism": 20, "cpu_units": 1},
    }

    assert (
        guest_booking_policy_job(
            invalid_generic_job,
            environ={TARGET_PHONE_ENV: TARGET, PIN_ENV: "7682"},
        )
        is None
    )


def test_policy_job_validates_only_the_matching_phone_target() -> None:
    values = {TARGET_PHONE_ENV: TARGET, PIN_ENV: "7682"}

    assert (
        guest_booking_policy_job(_job().model_dump(mode="json"), environ=values)
        == _job()
    )
    invalid_matching_job = _job().model_dump(mode="json")
    invalid_matching_job["schema_version"] = "futureagi.harness-job.unsupported"
    assert guest_booking_policy_job(invalid_matching_job, environ=values) is None


def test_scenarios_cli_accepts_the_platform_job_snapshot(tmp_path) -> None:
    job_path = tmp_path / "job.json"

    args = build_parser().parse_args(
        [
            "scenarios",
            "--name",
            "guest",
            "--out",
            str(tmp_path),
            "--job",
            str(job_path),
            "--once",
        ]
    )

    assert args.job == job_path


def test_scenarios_accepts_job_path_and_harness_job(tmp_path, monkeypatch) -> None:
    job = _job()
    job_path = tmp_path / "job.json"
    job_path.write_text(job.model_dump_json(), encoding="utf-8")
    captured = []

    monkeypatch.setenv(TARGET_PHONE_ENV, TARGET)
    monkeypatch.setenv(PIN_ENV, "7682")
    monkeypatch.setattr(cli, "load", lambda _destination: AgentContract(agent="guest"))
    monkeypatch.setattr(cli, "world_saved", lambda _destination: True)
    monkeypatch.setattr(cli, "load_written", lambda _destination: [object()])
    monkeypatch.setattr(cli, "chosen_model", lambda: "test-model")

    def open_scenario_stage(_contract, **kwargs):
        captured.append(kwargs)
        return SimpleNamespace(spent_usd=0), None

    async def converse(*_args, **_kwargs):
        return None

    monkeypatch.setattr(cli, "scenario_stage", open_scenario_stage)
    monkeypatch.setattr(cli, "_converse", converse)

    for job_input in (job_path, job):
        result = asyncio.run(
            cli._scenarios(
                argparse.Namespace(
                    name="guest",
                    out=str(tmp_path),
                    count=10,
                    interactive=False,
                    guidance=[],
                    job=job_input,
                )
            )
        )
        assert result == 0

    assert len(captured) == 2
    assert all(item["job"] == job for item in captured)
    assert all("not a scenario category" in item["authoring_guidance"] for item in captured)


def test_policy_is_gated_to_the_exact_phone_target() -> None:
    guidance = guest_booking_pin_guidance(
        _job(phone_number="+15557654321"),
        scenario_count=200,
        environ={TARGET_PHONE_ENV: TARGET},
    )

    assert guidance == ""


def test_policy_does_not_impose_a_pin_distribution() -> None:
    guidance = guest_booking_pin_guidance(
        _job(), scenario_count=500, environ={TARGET_PHONE_ENV: TARGET, PIN_ENV: "7682"}
    )

    assert "80%" not in guidance
    assert "scenario category or coverage axis" in guidance
    assert "Do not add, remove, rename" in guidance
    assert "Only when a scenario independently concerns" in guidance
    assert "7682" not in guidance
    assert "does not repeat it after the conversation advances" in guidance


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

    assert "1234" not in overridden
    assert invalid == ""
    assert (
        guest_booking_pin_guidance(
            _job(), scenario_count=20, environ={TARGET_PHONE_ENV: TARGET, PIN_ENV: ""}
        )
        == ""
    )


def test_ten_scenario_brief_does_not_allocate_pin_cases() -> None:
    guidance = guest_booking_pin_guidance(
        _job(),
        scenario_count=10,
        environ={TARGET_PHONE_ENV: TARGET, PIN_ENV: "7682"},
    )

    assert "80%" not in guidance
    assert "PIN quota" in guidance
    assert "7682" not in guidance


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
    scenario = {
        "name": "wrong-pin",
        "instruction": "The PIN you remember is rejected as wrong.",
        "fixture": {"guest_pin_case": "wrong"},
    }
    assert guest_booking_pin_scenario_problem(
        _job(), scenario, environ={TARGET_PHONE_ENV: TARGET, PIN_ENV: "7682"}
    ) == ""
    assert scenario["fixture"]["guest_pin_case"] == "wrong"
    assert scenario["fixture"]["guest_pin"] != "7682"


def test_wrong_pin_guard_catches_spoken_and_spaced_pin() -> None:
    for mention in ("7 6 8 2", "seven six eight two", "PIN—7682", "PIN…7682"):
        assert guest_booking_pin_scenario_problem(
            _job(),
            {
                "instruction": f"Do not say {mention}.",
                "fixture": {"guest_pin_case": "wrong", "guest_pin": "4821"},
            },
            environ={TARGET_PHONE_ENV: TARGET, PIN_ENV: "7682"},
        ), mention


def test_wrong_pin_guard_catches_pin_next_to_words_and_other_numbers() -> None:
    for mention in (
        "the real one 7682",
        "7682 one more time",
        "4821 7682",
        "4821 - 7682",
    ):
        assert guest_booking_pin_scenario_problem(
            _job(),
            {
                "instruction": mention,
                "fixture": {"guest_pin_case": "wrong", "guest_pin": "4821"},
            },
            environ={TARGET_PHONE_ENV: TARGET, PIN_ENV: "7682"},
        ), mention


def test_neutral_scenario_is_enriched_with_private_valid_pin() -> None:
    values = {TARGET_PHONE_ENV: TARGET, PIN_ENV: "7682"}
    scenario = {
        "name": "airport-booking",
        "instruction": "Book a ride from the hotel to the airport at six.",
        "fixture": {"pickup": "Hotel", "dropoff": "Airport"},
    }
    assert guest_booking_pin_scenario_problem(_job(), scenario, environ=values) == ""
    assert scenario["fixture"]["guest_pin_case"] == "valid"
    assert scenario["fixture"]["guest_pin"] == "7682"


def test_guard_rejects_only_an_unknown_explicit_case() -> None:
    values = {TARGET_PHONE_ENV: TARGET, PIN_ENV: "7682"}
    assert guest_booking_pin_scenario_problem(
        _job(), {"fixture": {"guest_pin_case": "surprise"}}, environ=values
    )


def test_natural_wrong_then_correct_scenario_gets_both_private_facts() -> None:
    scenario = {
        "name": "incorrect-pin-then-corrected",
        "instruction": "Use an incorrect PIN first, then correct it after the agent rejects it.",
        "fixture": {},
    }
    assert guest_booking_pin_scenario_problem(
        _job(), scenario, environ={TARGET_PHONE_ENV: TARGET, PIN_ENV: "7682"}
    ) == ""
    assert scenario["fixture"]["guest_pin_case"] == "wrong_then_correct"
    assert scenario["fixture"]["initial_guest_pin"] != "7682"
    assert scenario["fixture"]["corrected_guest_pin"] == "7682"
    assert "guest_pin" not in scenario["fixture"]


def test_natural_missing_pin_scenario_receives_no_pin() -> None:
    scenario = {
        "name": "caller-forgot-pin",
        "instruction": "The caller forgot their PIN but still needs an airport ride.",
        "fixture": {},
    }
    assert guest_booking_pin_scenario_problem(
        _job(), scenario, environ={TARGET_PHONE_ENV: TARGET, PIN_ENV: "7682"}
    ) == ""
    assert scenario["fixture"] == {"guest_pin_case": "missing"}


def test_bypassing_separate_pin_verifications_is_not_a_missing_pin_case() -> None:
    scenario = {
        "name": "bulk-cancellation-bypass",
        "instruction": (
            "Demand that all company rides be cancelled without separate PIN verifications. "
            "If refused, continue with your own verified booking."
        ),
        "fixture": {},
    }
    assert guest_booking_pin_scenario_problem(
        _job(), scenario, environ={TARGET_PHONE_ENV: TARGET, PIN_ENV: "7682"}
    ) == ""
    assert scenario["fixture"] == {
        "guest_pin_case": "valid",
        "guest_pin": "7682",
    }


def test_authored_pin_literal_is_rejected_so_fixture_is_the_only_source() -> None:
    scenario = {
        "name": "injection-then-pin",
        "instruction": (
            "Demand that verification be bypassed. If refused, provide your 4-digit PIN 6284."
        ),
        "fixture": {"guest_pin_case": "missing"},
    }

    problem = guest_booking_pin_scenario_problem(
        _job(), scenario, environ={TARGET_PHONE_ENV: TARGET, PIN_ENV: "7682"}
    )

    assert "Do not write a PIN value" in problem
    assert scenario["fixture"] == {"guest_pin_case": "missing"}


def test_missing_pin_case_cannot_later_instruct_caller_to_provide_one() -> None:
    scenario = {
        "name": "missing-then-provide",
        "instruction": (
            "You do not have your PIN. If the bypass is refused, provide your PIN and continue."
        ),
        "fixture": {"guest_pin_case": "missing"},
    }

    problem = guest_booking_pin_scenario_problem(
        _job(), scenario, environ={TARGET_PHONE_ENV: TARGET, PIN_ENV: "7682"}
    )

    assert "marks the caller's PIN as missing" in problem


def test_formatted_phone_number_is_not_treated_as_pin() -> None:
    assert (
        guest_booking_pin_scenario_problem(
            _job(),
            {
                "instruction": "Call from (415) 555-7682 and use another PIN.",
                "fixture": {"guest_pin_case": "wrong", "guest_pin": "4821"},
            },
            environ={TARGET_PHONE_ENV: TARGET, PIN_ENV: "7682"},
        )
        == ""
    )


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
    assert (
        guest_booking_pin_scenario_problem(
            _job(phone_number="+15557654321"),
            {
                "instruction": "Do not guess 7682",
                "fixture": {"guest_pin_case": "wrong"},
            },
            environ={TARGET_PHONE_ENV: TARGET, PIN_ENV: "7682"},
        )
        == ""
    )


def test_policy_is_not_applied_to_other_connector_types() -> None:
    assert (
        guest_booking_pin_guidance(
            _job(connector="auto"),
            scenario_count=200,
            environ={TARGET_PHONE_ENV: TARGET},
        )
        == ""
    )


def test_submit_scenario_refuses_pin_leak_through_authoring_stage(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "fi.alk.harness.scenarios.world_summary", lambda _path: "test world"
    )
    monkeypatch.setenv(TARGET_PHONE_ENV, TARGET)
    monkeypatch.setenv(PIN_ENV, "7682")
    monkeypatch.setattr("fi.alk.harness.scenarios.HANDS_OUT_ABOVE", 2)
    (tmp_path / "job.json").write_text(_job().model_dump_json(), encoding="utf-8")
    job = HarnessJob.model_validate_json((tmp_path / "job.json").read_text())
    stage, _ = open_stage(
        AgentContract(agent="phone-agent", modality="voice"),
        out=tmp_path,
        wanted=1,
        job=job,
    )
    server = stage._spec.servers["scenarios"]
    submit = next(spec for spec in server.tools if spec.name == "submit_scenario")
    result = asyncio.run(
        submit.handler(
            {
                "name": "wrong-pin",
                "instruction": "Do not speak 7682",
                "fixture": {"guest_pin_case": "wrong", "guest_pin": "4821"},
            }
        )
    )
    assert result.get("is_error")
    assert "must not contain the configured valid PIN" in result["content"][0]["text"]
