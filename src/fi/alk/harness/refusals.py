"""Whose problem each job-model refusal is: the caller's, or this sandbox's own.

``job.py`` refuses a job with a code in a ``ValueError``. The submit route answers:

- a **submission** refusal with a 422: the caller can fix it by changing what they sent;
- a **configuration** refusal with a 500 that names the code: nothing in the submission can
  fix it, but the sandbox is not down either, so the platform can say something true.

A 5xx is only for a request with nothing of its own at fault, so a request with both kinds is
a 422 for the caller's part, and ours is logged.

``JobRefused`` builds that answer from the models' errors, and the place functions below say
which request field an error without a code is about.

Each entry is the literal start of a code as ``job.py`` raises it: a code built with an
f-string (``provider_import_requires_{key}``) is listed by its fixed start. A test checks
that every code in ``job.py`` is in exactly one of the two lists.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal

from pydantic import ValidationError

logger = logging.getLogger(__name__)

SUBMISSION: frozenset[str] = frozenset(
    {
        # The source.
        "subfolder_requires_github_source",
        "github_ref_conflicts_with_repository_url",
        "source_locator_missing",
        "github_commit_sha_required",
        "github_commit_sha_invalid",
        "hosted_scenario_count_out_of_range",
        # The connector and its settings.
        "provider_phone_requires_connect_only_voice",
        "provider_phone_requires_e164_phone_number",
        "phone_dialer_config_is_platform_owned",
        "retell_chat_environment_backed_not_supported",
        "environment_backed_requires_vapi_or_retell",
        "provider_lifecycle_manifest_must_be_source_relative",
        "environment_backed_target_is_provision_output",
        "provider_import_requires_vapi_or_retell",
        "provider_import_requires_",
        "provider_import_",
        "phone_connect_only_requires_e164_phone_number",
        "phone_connect_only_requires_target_system_prompt",
        "livekit_connect_only_requires_agent_name",
        "livekit_connect_only_requires_target_system_prompt",
        "connect_only_requires_",
        "provider_mode_only_supported_for_vapi_retell_or_phone_connect_only",
        "target_model_must_be_nonempty_string",
        "direct_model_names_must_be_a_list_of_names",
        "resolved_secret_forbidden",
        # Secrets the caller referenced.
        "hosted_secret_manager_unsupported",
        "hosted_secret_purpose_invalid",
        # The actors switch the caller sent in metadata.
        "actors_share_invalid",
        # The scenario plan the caller sent in metadata.
        "execution_manifest_invalid",
        "execution_manifest_duplicate_key",
        "execution_manifest_count_mismatch",
    }
)

CONFIGURATION: frozenset[str] = frozenset(
    {
        # What submit sets itself for a hosted job: the runtime, its security and artifacts.
        "hosted_isolation_must_be_dedicated_vm",
        "hosted_parallelism_exceeds_cpu",
        "hosted_privileged_execution_forbidden",
        "hosted_runtime_control_forbidden",
        "hosted_source_must_be_read_only",
        "local_only_not_hosted",
        # Retry policy, schema version and admissions: never taken from the submission.
        "retry_domain_unsafe",
        "retry_backoff_invalid",
        "harness_job_version_unsupported",
        "public_material_admission_local_operator_required",
        # Where submit runs a job is its own choice from the source kind; these pairings are
        # ones it never makes, so reaching one is a fault here.
        "local_job_cannot_use_private_platform_github_source",
        "hosted_job_cannot_use_local_path",
        "image_source_not_hosted",
    }
)

_VALUE_ERROR = "Value error, "


def refusal_kind(message: str) -> Literal["submission", "configuration"] | None:
    """The kind of a validation error's message, by the longest listed code it starts with,
    or None when it names no listed code (a field-type error, say). Whose an uncoded error is
    depends on the field it is about, which the caller decides."""
    code = message.removeprefix(_VALUE_ERROR)
    configuration = max(
        (len(c) for c in CONFIGURATION if code.startswith(c)), default=-1
    )
    submission = max((len(c) for c in SUBMISSION if code.startswith(c)), default=-1)
    if configuration == submission == -1:
        return None
    return "configuration" if configuration > submission else "submission"


# Where each job-model field comes from in the request, so an error without a code can name
# the field the caller sent. A field missing here is one submit fills itself.
SOURCE_FIELDS = {
    "repository": "github_repository",
    "ref": "github_ref",
    "commit_sha": "github_commit_sha",
    "visibility": "github_visibility",
    "installation_id": "github_installation_id",
    "subfolder": "github_subfolder",
    "archive_artifact_id": "source_id",
    "local_path": "source_path",
}
AGENT_FIELDS = {
    "connector": "connector",
    "mode": "connector_mode",
    "config": "connector_config",
    "secret_refs": "secret_refs",
}
_JOB_FIELDS = {"scenario_count", "seed", "platform_run_id"}
# Metadata entries submit writes itself, over whatever the request's metadata held.
_SUBMIT_METADATA = {
    "agent_name",
    "source_kind",
    "environment_value_names",
    "secret_file_names",
}

Place = Callable[[tuple[str | int, ...]], list[str | int] | None]


def by_field(fields: Mapping[str, str]) -> Place:
    """A part's error at the request field it came from, by ``fields``; None for a field the
    part has that the caller did not send."""

    def place(loc: tuple[str | int, ...]) -> list[str | int] | None:
        if loc and loc[0] in fields:
            return ["body", fields[str(loc[0])], *loc[1:]]
        return None

    return place


def in_job(loc: tuple[str | int, ...]) -> list[str | int] | None:
    """A job error at the request field it came from: a top-level job field, or a metadata
    entry the caller sent. None for anything submit sets itself."""
    if loc and loc[0] in _JOB_FIELDS:
        return ["body", *loc]
    if len(loc) > 1 and loc[0] == "metadata" and loc[1] not in _SUBMIT_METADATA:
        return ["body", *loc]
    return None


def in_plan(loc: tuple[str | int, ...]) -> list[str | int] | None:
    """An entry's own error in the scenario plan, at the entry the caller sent. The plan's
    other faults carry a code, so they need no place."""
    if len(loc) > 1 and loc[0] == "entries":
        return ["body", "metadata", "execution_manifest", *loc[1:]]
    return None


class JobRefused(Exception):
    """The job models refused a submission, answered in the shape of the route's own 422: one
    entry per error, and no ``input`` or ``ctx``, since either can carry a submitted value.

    A 5xx is only for a request with nothing of its own at fault, so any fault of the
    caller's makes it a 422 that lists the caller's entries:

    - an error with a code (``Value error, <code>``) has ``loc`` ["body"], since the code says
      what is wrong;
    - an error without one (a type or length error) keeps its place, mapped onto the request
      field it is about, such as ["body", "metadata", "execution_manifest", 0, "trial_index"].

    The rest are this sandbox's own configuration: a configuration code, or an error without a
    code in a value submit fills itself. Each is logged as an error with the job id, so ours is
    never lost behind theirs. A request refused only for those is a 500 that names them,
    ``type`` "sandbox_configuration" and ``loc`` ["sandbox"]."""

    def __init__(
        self, refusals: Sequence[tuple[ValidationError, Place | None]], *, job_id: str
    ):
        theirs: list[dict[str, Any]] = []
        ours: list[str] = []
        for error, place in refusals:
            for item in error.errors(
                include_url=False, include_input=False, include_context=False
            ):
                kind, msg = item["type"], item["msg"]
                coded = refusal_kind(msg)
                where = place(tuple(item["loc"])) if place is not None else None
                if coded == "submission":
                    theirs.append({"type": kind, "loc": ["body"], "msg": msg})
                elif coded is None and where is not None:
                    theirs.append({"type": kind, "loc": where, "msg": msg})
                else:
                    ours.append(msg)
        for msg in ours:
            logger.error(
                "job_refused_for_sandbox_configuration: job_id=%s %s", job_id, msg
            )
        if theirs:
            self.status_code = 422
            self.detail = theirs
        else:
            self.status_code = 500
            self.detail = [
                {"type": "sandbox_configuration", "loc": ["sandbox"], "msg": msg}
                for msg in ours
            ]
        super().__init__("; ".join(entry["msg"] for entry in self.detail))
