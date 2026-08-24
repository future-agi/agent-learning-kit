"""Resolve opaque secret references only at the worker boundary."""

from __future__ import annotations

import os
from collections.abc import Mapping

from fi.simulate.runtime.spec import SecretRef


class SecretResolutionError(RuntimeError):
    pass


_RUNTIME_CONFIGURATION_PREFIX = "ALK_RUNTIME_VALUE_"


def runtime_configuration_environment(values: Mapping[str, str]) -> dict[str, str]:
    """Namespace submitted-agent values away from runner/controller credentials."""
    return {
        f"{_RUNTIME_CONFIGURATION_PREFIX}{name}": value
        for name, value in values.items()
    }


def runtime_configuration_value(
    name: str,
    *,
    environment: Mapping[str, str] | None = None,
) -> str:
    """Read a submitted-agent value without falling back to a controller secret."""
    source = environment if environment is not None else os.environ
    namespaced = source.get(f"{_RUNTIME_CONFIGURATION_PREFIX}{name}", "")
    if namespaced:
        return namespaced
    # The local CLI historically receives agent configuration directly from its shell. Hosted
    # workers always define the names manifest (even when empty), so they never fall back to a
    # runner credential with the same name.
    if "ALK_RUNTIME_CONFIGURATION_NAMES" not in source:
        return source.get(name, "")
    return ""


def resolve_worker_secrets(
    references: Mapping[str, SecretRef],
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Resolve mounted references without persisting or logging their values.

    In local development ``environment`` reads the developer shell. In a hosted provider,
    the secret manager mounts only job-authorized keys into the worker supervisor and this
    same adapter reads those mounts. The job and platform continue to carry references only.
    """
    source = environment if environment is not None else os.environ
    resolved: dict[str, str] = {}
    for alias, reference in references.items():
        if reference.manager not in {"environment", "futureagi", "mounted"}:
            raise SecretResolutionError(
                f"secret_manager_unsupported: {reference.manager}"
            )
        value = source.get(reference.key)
        if not value:
            raise SecretResolutionError(f"secret_reference_unavailable: {alias}")
        resolved[str(alias)] = value
    return resolved


def worker_environment(
    resolved: Mapping[str, str],
    *,
    runtime_configuration: Mapping[str, str] | None = None,
    host_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Create a least-privilege child environment instead of inheriting every host secret."""
    host = host_environment if host_environment is not None else os.environ
    allowed = {
        "DOCKER_HOST",
        "HOME",
        "LANG",
        "LC_ALL",
        "NO_PROXY",
        "PATH",
        "PYTHONPATH",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TMPDIR",
        # Runner topology. Hosted workers need these to reach sibling services privately and to
        # expose their per-call webhook, but submitted values must never control them.
        "ALK_DOCKER_BIND_HOST",
        "ALK_DOCKER_PUBLISHED_HOST",
        "ALK_RUNNER_CONTAINER",
        "HARNESS_WEBHOOK_HOST",
        "HARNESS_WEBHOOK_PORT",
        # Runner-owned model configuration. Uploaded agent values with these names remain in the
        # runtime namespace and cannot replace controller credentials.
        "ALK_HARNESS_MODEL",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_VERTEX_PROJECT_ID",
        "CLAUDE_CODE_USE_VERTEX",
        "CLOUD_ML_REGION",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_PROJECT",
        # Runner-owned result delivery credentials, mounted independently of customer source.
        "HARNESS_PLATFORM_URL",
        "HARNESS_PLATFORM_API_KEY",
        "HARNESS_PLATFORM_SECRET_KEY",
        "FI_BASE_URL",
        "FI_API_KEY",
        "FI_SECRET_KEY",
        # Runner-owned Docker runtime + voice configuration. The harness starts store and agent
        # containers on the host daemon and must reach them: ALK_DOCKER_NETWORK lets the store be
        # reached by container name on a shared network, ALK_DOCKER_PUBLISHED_HOST/BIND_HOST give
        # the host published services are on. Without these the child defaults to 127.0.0.1 --
        # its own loopback inside the sandbox -- and every store probe is refused.
        "ALK_DOCKER_NETWORK",
        "ALK_DOCKER_PUBLISHED_HOST",
        "ALK_DOCKER_BIND_HOST",
        "ALK_RUNNER_CONTAINER",
        "ALK_HARNESS_MODEL",
        "ALK_AGENT_MODEL",
        "ALK_JUDGE_MODEL",
        "ALK_USER_MODEL",
        "CLOUD_ML_REGION",
        "HARNESS_WEBHOOK_HOST",
        "HARNESS_WEBHOOK_PORT",
        "HARNESS_WEBHOOK_URL",
        "HARNESS_RUNTIME_WEBHOOK_URL",
        "HARNESS_VOICE_CASE",
        "HARNESS_VOICE_INFRA_RETRIES",
        "LIVEKIT_TARGET_AGENT_NAME",
        # Local-dev convenience: let the developer's provider creds from their local environment
        # reach the worker directly. A hosted provider supplies these through secret_refs instead.
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "ACCEPTANCE_LIVEKIT_URL",
        "DEEPGRAM_API_KEY",
        "CARTESIA_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
    }
    child = {name: value for name, value in host.items() if name in allowed}
    reserved = {
        "ALK_HARNESS_MODEL",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_VERTEX_PROJECT_ID",
        "CLAUDE_CODE_USE_VERTEX",
        "CLOUD_ML_REGION",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_PROJECT",
        "ALK_DOCKER_BIND_HOST",
        "ALK_DOCKER_PUBLISHED_HOST",
        "ALK_RUNNER_CONTAINER",
        "HARNESS_WEBHOOK_HOST",
        "HARNESS_WEBHOOK_PORT",
    }
    child.update(
        {name: value for name, value in resolved.items() if name not in reserved}
    )
    child.update(runtime_configuration_environment(runtime_configuration or {}))
    return child


__all__ = [
    "SecretResolutionError",
    "resolve_worker_secrets",
    "runtime_configuration_environment",
    "runtime_configuration_value",
    "worker_environment",
]
