"""Resolve opaque secret references only at the worker boundary."""

from __future__ import annotations

import os
from collections.abc import Mapping

from fi.simulate.runtime.spec import SecretRef


class SecretResolutionError(RuntimeError):
    pass


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
        # Runner-owned result delivery credentials, mounted independently of customer source.
        "HARNESS_PLATFORM_URL",
        "HARNESS_PLATFORM_API_KEY",
        "HARNESS_PLATFORM_SECRET_KEY",
        "FI_BASE_URL",
        "FI_API_KEY",
        "FI_SECRET_KEY",
    }
    child = {name: value for name, value in host.items() if name in allowed}
    child.update(resolved)
    return child


__all__ = ["SecretResolutionError", "resolve_worker_secrets", "worker_environment"]
