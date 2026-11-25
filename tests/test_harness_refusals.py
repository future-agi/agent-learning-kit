"""Where a job-model refusal lands in the answer, and the scenario plan judged whole."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fi.alk.harness.job import (
    AgentConnection,
    ExecutionMode,
    HarnessJob,
    RepositorySource,
    SourceKind,
    parse_execution_manifest,
)
from fi.alk.harness.refusals import (
    AGENT_FIELDS,
    SOURCE_FIELDS,
    by_field,
    in_job,
    in_plan,
)


def _entry(key: str, **changes: object) -> dict[str, object]:
    return {
        "execution_key": key,
        "scenario_key": "s",
        "scenario_id": "x",
        "trial_index": 1,
        "call_execution_id": "c",
        **changes,
    }


def _faults(manifest: object, scenario_count: int) -> list[tuple[tuple, str]]:
    with pytest.raises(ValidationError) as refused:
        parse_execution_manifest(manifest, scenario_count)
    return [(tuple(e["loc"]), e["msg"]) for e in refused.value.errors()]


def test_a_source_error_lands_on_the_request_field_it_came_from():
    place = by_field(SOURCE_FIELDS)

    assert place(("commit_sha",)) == ["body", "github_commit_sha"]
    assert place(("archive_artifact_id",)) == ["body", "source_id"]
    assert place(("local_path",)) == ["body", "source_path"]
    # A field the caller never sends has no place of theirs.
    assert place(("kind",)) is None
    assert place(()) is None


def test_an_agent_error_keeps_the_rest_of_its_place():
    place = by_field(AGENT_FIELDS)

    assert place(("config", "assistant_id")) == [
        "body",
        "connector_config",
        "assistant_id",
    ]
    assert place(("mode",)) == ["body", "connector_mode"]
    assert place(("secret_refs", "GOOD", "key")) == [
        "body",
        "secret_refs",
        "GOOD",
        "key",
    ]
    assert place(("name",)) is None


def test_a_job_error_is_the_callers_only_for_what_the_caller_sent():
    assert in_job(("scenario_count",)) == ["body", "scenario_count"]
    assert in_job(("seed",)) == ["body", "seed"]
    assert in_job(("platform_run_id",)) == ["body", "platform_run_id"]
    assert in_job(("metadata", "customer_label")) == [
        "body",
        "metadata",
        "customer_label",
    ]
    # Submit writes these itself, over whatever the caller sent.
    for written in (
        "agent_name",
        "source_kind",
        "environment_value_names",
        "secret_file_names",
    ):
        assert in_job(("metadata", written)) is None
    assert in_job(("metadata",)) is None
    assert in_job(("runtime", "parallelism")) is None
    assert in_job(()) is None


def test_a_plan_entry_error_lands_on_the_entry_the_caller_sent():
    assert in_plan(("entries", 0, "trial_index")) == [
        "body",
        "metadata",
        "execution_manifest",
        0,
        "trial_index",
    ]
    assert in_plan(("entries", 3)) == ["body", "metadata", "execution_manifest", 3]
    # The plan's other faults carry a code, which says what is wrong without a place.
    assert in_plan(("entries",)) is None
    assert in_plan(("execution_keys",)) is None
    assert in_plan(("entry_count",)) is None


def test_a_plan_that_fits_is_read_entry_by_entry():
    entries = parse_execution_manifest([_entry("a"), _entry("b", trial_index="2")], 2)

    assert [(e.execution_key, e.trial_index) for e in entries] == [("a", 1), ("b", 2)]


def test_every_fault_of_a_plan_is_reported_at_once():
    faults = _faults([_entry("a", trial_index="two"), _entry("a"), "not-an-entry"], 2)

    assert [loc for loc, _ in faults] == [
        ("entries", 0, "trial_index"),
        ("entries", 2),
        ("execution_keys",),
        ("entry_count",),
    ]
    assert faults[2][1] == "Value error, execution_manifest_duplicate_key"
    assert faults[3][1] == "Value error, execution_manifest_count_mismatch"


@pytest.mark.parametrize("manifest", ["not-a-list", [], {"execution_key": "a"}])
def test_a_plan_that_is_not_a_list_of_entries_is_refused_for_that_alone(manifest):
    assert _faults(manifest, 1) == [
        (("entries",), "Value error, execution_manifest_invalid")
    ]


def test_the_job_holds_the_plan_to_the_same_rule():
    with pytest.raises(ValidationError, match="execution_manifest_count_mismatch"):
        HarnessJob(
            job_id="job-1",
            run_id="run-1",
            execution=ExecutionMode.LOCAL,
            source=RepositorySource(
                kind=SourceKind.LOCAL_REPOSITORY, local_path="/agent"
            ),
            agent=AgentConnection(connector="local"),
            scenario_count=2,
            metadata={"execution_manifest": [_entry("a")]},
        )
