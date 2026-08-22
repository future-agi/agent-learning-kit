import pytest

from fi.alk.harness.secrets import (
    SecretResolutionError,
    resolve_worker_secrets,
    worker_environment,
)
from fi.simulate.runtime.spec import SecretRef


def _ref(manager="futureagi", key="secret-livekit"):
    return SecretRef(manager=manager, key=key, purpose="voice connection")


def test_resolves_reference_to_alias_only_at_worker_boundary():
    resolved = resolve_worker_secrets(
        {"LIVEKIT_API_KEY": _ref()},
        environment={"secret-livekit": "resolved-value"},
    )

    assert resolved == {"LIVEKIT_API_KEY": "resolved-value"}


def test_missing_or_unsupported_secret_reference_fails_closed():
    with pytest.raises(SecretResolutionError, match="unavailable"):
        resolve_worker_secrets({"LIVEKIT_API_KEY": _ref()}, environment={})
    with pytest.raises(SecretResolutionError, match="unsupported"):
        resolve_worker_secrets(
            {"LIVEKIT_API_KEY": _ref(manager="unknown")},
            environment={"secret-livekit": "value"},
        )


def test_worker_does_not_inherit_unrelated_host_secrets():
    child = worker_environment(
        {"LIVEKIT_API_KEY": "job-value"},
        host_environment={
            "PATH": "/bin",
            "AWS_SECRET_ACCESS_KEY": "host-secret",
            "UNRELATED_CUSTOMER_TOKEN": "other-secret",
        },
    )

    assert child == {"PATH": "/bin", "LIVEKIT_API_KEY": "job-value"}
