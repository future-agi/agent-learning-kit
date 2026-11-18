"""Whose problem each job-model refusal is: the caller's, or this sandbox's own.

``job.py`` refuses a job with a code in a ``ValueError``. The submit route answers:

- a **submission** refusal with a 422: the caller can fix it by changing what they sent;
- a **configuration** refusal with a 500 that names the code: nothing in the submission can
  fix it, but the sandbox is not down either, so the platform can say something true.

A 5xx is only for a request with nothing of its own at fault, so a request with both kinds is
a 422 for the caller's part, and ours is logged.

Each entry is the literal start of a code as ``job.py`` raises it: a code built with an
f-string (``provider_import_requires_{key}``) is listed by its fixed start. A test checks
that every code in ``job.py`` is in exactly one of the two lists.
"""

from __future__ import annotations

from typing import Literal

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
        "resolved_secret_forbidden",
        # Secrets the caller referenced.
        "hosted_secret_manager_unsupported",
        "hosted_secret_purpose_invalid",
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
