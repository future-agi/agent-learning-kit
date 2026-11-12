"""Resolve opaque secret references only at the worker boundary."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping

from fi.simulate.runtime.spec import SecretRef

from .backends import GOOGLE_ADK_APPROVAL_ENV


class SecretResolutionError(RuntimeError):
    pass


_RUNTIME_CONFIGURATION_PREFIX = "ALK_RUNTIME_VALUE_"


WORKER_ENVIRONMENT_NAMES: frozenset[str] = frozenset(
    {
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
        "ALK_DOCKER_NETWORK",
        "ALK_DOCKER_PUBLISHED_HOST",
        "ALK_RUNNER_CONTAINER",
        "HARNESS_WEBHOOK_HOST",
        "HARNESS_WEBHOOK_PORT",
        "HARNESS_WEBHOOK_URL",
        "HARNESS_RUNTIME_WEBHOOK_URL",
        "HARNESS_VOICE_INFRA_RETRIES",
        # Runner-owned model configuration. Uploaded agent values with these names remain in the
        # runtime namespace and cannot replace controller credentials.
        "ALK_AGENT_MODEL",
        # The backend choice travels with the model it names: without it the worker
        # falls back to the default backend and hands it a model it cannot drive.
        "ALK_HARNESS",
        GOOGLE_ADK_APPROVAL_ENV,
        "ALK_HARNESS_MODEL",
        "ALK_HARNESS_THINKING",
        "AGENTCC_API_KEY",
        "AGENTCC_BASE_URL",
        "ALK_JUDGE_MODEL",
        "ALK_USER_MODEL",
        # Backend-specific configuration. A hosted run pinned to a region falls back to
        # global without this, which is a quiet change of provider endpoint.
        "ALK_VERTEX_LOCATION",
        # The simulated caller's voice. Without it every persona shares one voice and the run
        # stops exercising voice variation, which it reports as a log line rather than a failure.
        "CARTESIA_API_KEY",
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
    }
)

# Runner-owned names a job may never set, as a variable or as a secret-ref alias. Both the sandbox
# server's submit-time checks and the controller's ``.env`` loader read this one set.
RUNNER_RESERVED_ENVIRONMENT: frozenset[str] = frozenset(
    {
        "DOCKER_HOST",
        "FI_API_KEY",
        "FI_BASE_URL",
        "FI_SECRET_KEY",
        "HARNESS_PLATFORM_API_KEY",
        "HARNESS_PLATFORM_SECRET_KEY",
        "HARNESS_PLATFORM_URL",
        "HARNESS_WEBHOOK_HOST",
        "HARNESS_WEBHOOK_PORT",
        "HOME",
        "PATH",
        "PYTHONPATH",
    }
)

# The names the call stage and the endpoint originators read from the controller's own environment
# (never a base URL, never a host-owned name). Each is read by the code named beside it.
CONTROLLER_CONNECTION_NAMES: frozenset[str] = frozenset(
    {
        "LIVEKIT_URL",  # run/sdk_voice.py:60, harness/contract.py:1419
        "LIVEKIT_API_KEY",  # simulate/voice.py:204
        "LIVEKIT_API_SECRET",  # simulate/voice.py:205
        "LIVEKIT_INBOUND_DID",  # simulate/endpoints/retell.py:191
        "LIVEKIT_INBOUND_TRUNK_ID",  # simulate/simulation/engines/livekit.py:5557
        "DEEPGRAM_API_KEY",  # simulate/simulation/livekit_models.py:120
        "GEMINI_API_KEY",  # simulate/simulation/livekit_models.py:247
        "OPENAI_API_KEY",  # simulate/endpoints/actor_sources.py:176
        "VAPI_API_KEY",  # run/live.py:222
        "VAPI_PUBLIC_API_KEY",  # simulate/endpoints/vapi.py:101
        "VAPI_ASSISTANT_ID",  # run/live.py:221, simulate/endpoints/vapi.py
        "VAPI_PHONE_NUMBER_ID",  # simulate/endpoints/vapi.py:87
        "RETELL_API_KEY",  # simulate/endpoints/retell.py:190
        "RETELL_AGENT_ID",  # simulate/endpoints/retell.py:185
        "RETELL_FROM_NUMBER",  # simulate/endpoints/retell.py:189
        "ELEVEN_API_KEY",  # simulate/simulation/livekit_models.py:98, :162
        "ELEVENLABS_API_KEY",  # simulate/simulation/livekit_models.py:98, :162
    }
)

_CLIENT_PREFIXES = (
    "LD_",
    "DYLD_",
    "DOCKER_",
    "COMPOSE_",
    "BUILDKIT_",
    "BUILDX_",
    "GIT_",
    "CLAUDE_CODE_",
    # Git asks SSH_ASKPASS for a credential even with GIT_TERMINAL_PROMPT=0, and XDG_CONFIG_HOME
    # moves its global config, credential.helper included: either runs a job's own code.
    "SSH_",
    "XDG_",
)
_CLIENT_EXACT = frozenset(
    {
        # The worker allow-list's client settings.
        "PATH",
        "HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "DOCKER_HOST",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        # Resolver, TLS and shell hooks.
        "NO_PROXY",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "LOCALDOMAIN",
        "RES_OPTIONS",
        "HOSTALIASES",
        "GCONV_PATH",
        "BASH_ENV",
        "ENV",
        "OPENSSL_CONF",
        "SSLKEYLOGFILE",
        # Runtime and library hooks that load or run code, or pick a module to import.
        "GODEBUG",
        "GLIBC_TUNABLES",
        "LOCPATH",
        "NLSPATH",
        "OPENSSL_MODULES",
        "OPENSSL_ENGINES",
        "BROWSER",
        "PYTHONWARNINGS",
        "PYTHONINSPECT",
        # Code-loading names.
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "PYTHONUSERBASE",
        "PYTHONBREAKPOINT",
        "PYTHONPLATLIBDIR",
        "NODE_OPTIONS",
        "NODE_PATH",
        "NODE_REPL_EXTERNAL_MODULE",
        "NODE_EXTRA_CA_CERTS",
        "NODE_TLS_REJECT_UNAUTHORIZED",
        "PERL5OPT",
        "PERL5LIB",
        "RUBYOPT",
        "RUBYLIB",
    }
)
_OWNED_PREFIXES = (
    "ALK_",
    "AGENTCC_",
    "HARNESS_",
    "FI_",
    "SIMULATOR_",
    "ANTHROPIC_",
    "GOOGLE_",
    "VERTEX",
    "PYTHON",
    "NODE_",
)


def client_name_reserved(name: str) -> bool:
    """True for a name that steers a client process (loader, resolver, proxy, TLS, shell).

    Compared stripped; upper-cased for the lists and prefixes, lower-cased for ``_proxy``.
    """
    stripped = str(name).strip()
    key = stripped.upper()
    return (
        key in _CLIENT_EXACT
        or key.startswith(_CLIENT_PREFIXES)
        or stripped.lower().endswith("_proxy")
    )


def controller_owned_name(name: str) -> bool:
    """True for a name the runner owns: its credentials, topology and model settings."""
    key = str(name).strip().upper()
    return (
        key in RUNNER_RESERVED_ENVIRONMENT
        or key in WORKER_ENVIRONMENT_NAMES
        or key.startswith(_OWNED_PREFIXES)
    )


def controller_name_reserved(name: str) -> bool:
    """True when a job must not be able to set ``name`` in the sandbox controller's environment."""
    return client_name_reserved(name) or controller_owned_name(name)


PROCESS_SECRET_FILE_ENVIRONMENT = {
    "GOOGLE_APPLICATION_CREDENTIALS_JSON": "GOOGLE_APPLICATION_CREDENTIALS",
}


def process_secret_environment_names(injected_names: Iterable[str]) -> set[str]:
    """Project injected names through the runtime's existing secret-file materializers."""
    return {PROCESS_SECRET_FILE_ENVIRONMENT.get(name, name) for name in injected_names}


def target_provider_environment(values: Mapping[str, str]) -> dict[str, str]:
    """Project target Vertex settings into SDK aliases without changing explicit choices."""
    projected = dict(values)
    for canonical, primary, fallback in (
        ("GOOGLE_CLOUD_LOCATION", "VERTEXAI_LOCATION", "VERTEX_LOCATION"),
        ("GOOGLE_CLOUD_PROJECT", "VERTEXAI_PROJECT", "VERTEX_PROJECT"),
    ):
        if values.get(canonical) and primary not in values and fallback not in values:
            projected[primary] = values[canonical]
    return projected


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
    allowed = WORKER_ENVIRONMENT_NAMES
    child = {name: value for name, value in host.items() if name in allowed}
    reserved = {
        "ALK_HARNESS",
        GOOGLE_ADK_APPROVAL_ENV,
        "ALK_HARNESS_MODEL",
        "ALK_HARNESS_THINKING",
        "AGENTCC_API_KEY",
        "AGENTCC_BASE_URL",
        "ALK_VERTEX_LOCATION",
        "CARTESIA_API_KEY",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_VERTEX_PROJECT_ID",
        "CLAUDE_CODE_USE_VERTEX",
        "CLOUD_ML_REGION",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_PROJECT",
        "ALK_DOCKER_NETWORK",
        "ALK_DOCKER_BIND_HOST",
        "ALK_DOCKER_PUBLISHED_HOST",
        "ALK_RUNNER_CONTAINER",
        "HARNESS_WEBHOOK_HOST",
        "HARNESS_WEBHOOK_PORT",
        "HARNESS_WEBHOOK_URL",
        "HARNESS_RUNTIME_WEBHOOK_URL",
        "HARNESS_VOICE_INFRA_RETRIES",
        "ALK_AGENT_MODEL",
        "ALK_JUDGE_MODEL",
        "ALK_USER_MODEL",
    }
    child.update(
        {
            name: value
            for name, value in resolved.items()
            if name not in reserved and not controller_name_reserved(name)
        }
    )
    child.update(runtime_configuration_environment(runtime_configuration or {}))
    return child


__all__ = [
    "CONTROLLER_CONNECTION_NAMES",
    "RUNNER_RESERVED_ENVIRONMENT",
    "SecretResolutionError",
    "WORKER_ENVIRONMENT_NAMES",
    "client_name_reserved",
    "controller_name_reserved",
    "controller_owned_name",
    "resolve_worker_secrets",
    "runtime_configuration_environment",
    "runtime_configuration_value",
    "worker_environment",
]
