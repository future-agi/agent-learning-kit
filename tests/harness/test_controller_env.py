"""TH-9550: a repository's .env must not add to the sandbox controller's environment."""

import json
import os
import subprocess
import urllib.request
from pathlib import Path

import pytest

from fi.alk.harness import provision
from fi.alk.harness.cli import _load_connection_env

_MANIFEST = "ALK_RUNTIME_CONFIGURATION_NAMES"
_GOOGLE = {
    "GOOGLE_APPLICATION_CREDENTIALS": "/host/google.json",
    "GOOGLE_CLOUD_PROJECT": "host-project",
    "GOOGLE_CLOUD_LOCATION": "host-location",
}


@pytest.fixture
def clean_env(monkeypatch):
    """Snapshot os.environ (the loader writes it directly) and drop names these tests set."""
    saved = dict(os.environ)
    for name in list(os.environ):
        if name.startswith(("ALK_RUNTIME_", "ALK_TARGET_")) or name in {
            "HARNESS_VOICE_RUNNER", "LD_PRELOAD", "SSL_CERT_DIR", "BASH_ENV", "http_proxy",
            "no_proxy", "LIVEKIT_URL", "VAPI_API_BASE_URL", "ACCEPTANCE_LIVEKIT_URL",
            "VOICE_MAX_SECONDS", "MY_SETTING", "DOCKER_CONFIG", "ALK_SANDBOX_TOKEN",
        }:
            monkeypatch.delenv(name, raising=False)
    yield monkeypatch
    os.environ.clear()
    os.environ.update(saved)


def _env(tmp_path: Path, text: str) -> Path:
    (tmp_path / ".env").write_text(text, encoding="utf-8")
    return tmp_path


def _manifest():
    return [n for n in os.environ.get(_MANIFEST, "").split(",") if n]


def test_controller_env_never_makes_harness_or_loader_names_controller_variables(
    clean_env, tmp_path
):
    clean_env.setenv(_MANIFEST, "")
    source = _env(
        tmp_path,
        "HARNESS_VOICE_RUNNER=/planted.py\nexport LD_PRELOAD=/x.so\nSSL_CERT_DIR =x\n"
        "BASH_ENV=/x\nhttp_proxy=http://canary-proxy.invalid\nLIVEKIT_URL=wss://canary.invalid\n",
    )

    _load_connection_env(source)

    for name in ("HARNESS_VOICE_RUNNER", "LD_PRELOAD", "SSL_CERT_DIR", "BASH_ENV", "http_proxy"):
        assert name not in os.environ
    assert os.environ["LIVEKIT_URL"] == "wss://canary.invalid"
    # Client names are skipped outright; an owned name reaches only the runtime namespace.
    assert [n for n in os.environ if n.startswith("ALK_RUNTIME_VALUE_")] == [
        "ALK_RUNTIME_VALUE_HARNESS_VOICE_RUNNER"
    ]
    assert _manifest() == ["HARNESS_VOICE_RUNNER"]


def test_owned_names_the_agent_may_need_reach_the_runtime(clean_env, tmp_path):
    clean_env.setenv(_MANIFEST, "")
    names = ("NODE_ENV", "PYTHONUNBUFFERED", "GOOGLE_CLOUD_PROJECT", "ANTHROPIC_API_KEY", "CARTESIA_API_KEY")
    for name in names:
        clean_env.delenv(name, raising=False)
    source = _env(tmp_path, "".join(f"{name}=job-{name}\n" for name in names))

    _load_connection_env(source)

    for name in names:
        assert name not in os.environ
        assert os.environ[f"ALK_RUNTIME_VALUE_{name}"] == f"job-{name}"
    assert sorted(_manifest()) == sorted(names)


def test_controller_env_sends_other_names_to_the_runtime_namespace(clean_env, tmp_path):
    clean_env.setenv(_MANIFEST, "")
    source = _env(
        tmp_path,
        "VAPI_API_BASE_URL=https://canary.invalid\nACCEPTANCE_LIVEKIT_URL=wss://a.invalid\n"
        "VOICE_MAX_SECONDS=5\nMY_SETTING=mine\n",
    )

    _load_connection_env(source)

    for name, value in (
        ("VAPI_API_BASE_URL", "https://canary.invalid"),
        ("ACCEPTANCE_LIVEKIT_URL", "wss://a.invalid"),
        ("VOICE_MAX_SECONDS", "5"),
        ("MY_SETTING", "mine"),
    ):
        assert name not in os.environ
        assert os.environ[f"ALK_RUNTIME_VALUE_{name}"] == value
    assert set(_manifest()) == {
        "VAPI_API_BASE_URL", "ACCEPTANCE_LIVEKIT_URL", "VOICE_MAX_SECONDS", "MY_SETTING"
    }


def test_controller_env_skips_runner_names_entirely(clean_env, tmp_path):
    clean_env.setenv(_MANIFEST, "")
    source = _env(
        tmp_path,
        "export PATH=/repo/bin\nHOME =x\nALK_SANDBOX_TOKEN=x\nDOCKER_CONFIG=x\nFI_BASE_URL=https://x.invalid\n"
        "MY_SETTING=ok\n",
    )
    clean_env.delenv("FI_BASE_URL", raising=False)
    path_before, home_before = os.environ["PATH"], os.environ.get("HOME")

    _load_connection_env(source)

    assert os.environ["PATH"] == path_before
    assert os.environ.get("HOME") == home_before
    assert "ALK_SANDBOX_TOKEN" not in os.environ
    assert "DOCKER_CONFIG" not in os.environ
    assert "FI_BASE_URL" not in os.environ
    # FI_BASE_URL is runner-reserved but is neither a client name nor under ALK_.
    for name in ("PATH", "HOME", "ALK_SANDBOX_TOKEN", "DOCKER_CONFIG", "FI_BASE_URL"):
        assert f"ALK_RUNTIME_VALUE_{name}" not in os.environ
        assert name not in _manifest()
    assert _manifest() == ["MY_SETTING"]


def test_the_manifest_counts_by_presence_not_by_truthiness(clean_env, tmp_path):
    clean_env.setenv(_MANIFEST, "")
    source = _env(tmp_path, "BASH_ENV=/planted.sh\nMY_SETTING=mine\n")

    _load_connection_env(source)

    assert "BASH_ENV" not in os.environ
    assert os.environ["ALK_RUNTIME_VALUE_MY_SETTING"] == "mine"


def test_names_are_judged_after_normalisation(clean_env, tmp_path):
    clean_env.setenv(_MANIFEST, "")
    source = _env(tmp_path, "export   LD_PRELOAD  =/x.so\n  export HARNESS_VOICE_RUNNER=/p.py\n")

    _load_connection_env(source)

    assert "LD_PRELOAD" not in os.environ
    assert "HARNESS_VOICE_RUNNER" not in os.environ
    assert not [n for n in os.environ if "LD_PRELOAD" in n]
    assert [n for n in os.environ if "VOICE_RUNNER" in n] == ["ALK_RUNTIME_VALUE_HARNESS_VOICE_RUNNER"]


def test_existing_values_win_inside_the_controller(clean_env, tmp_path):
    clean_env.setenv(_MANIFEST, "")
    clean_env.setenv("LIVEKIT_URL", "wss://host.invalid")
    clean_env.setenv("MY_SETTING", "host")
    clean_env.setenv("ALK_RUNTIME_VALUE_VOICE_MAX_SECONDS", "9")
    source = _env(tmp_path, "LIVEKIT_URL=wss://repo.invalid\nMY_SETTING=repo\nVOICE_MAX_SECONDS=1\n")

    _load_connection_env(source)

    assert os.environ["LIVEKIT_URL"] == "wss://host.invalid"
    assert "ALK_RUNTIME_VALUE_MY_SETTING" not in os.environ
    assert os.environ["ALK_RUNTIME_VALUE_VOICE_MAX_SECONDS"] == "9"


def test_outside_the_controller_every_name_loads_as_before(clean_env, tmp_path):
    clean_env.delenv(_MANIFEST, raising=False)
    source = _env(
        tmp_path,
        "HARNESS_VOICE_RUNNER=/x.py\nhttp_proxy=http://p.invalid\nMY_SETTING=mine\n",
    )

    loaded = _load_connection_env(source)

    assert set(loaded) == {"HARNESS_VOICE_RUNNER", "http_proxy", "MY_SETTING"}
    assert os.environ["MY_SETTING"] == "mine"
    assert _MANIFEST not in os.environ


def test_a_planted_voice_runner_is_not_run_by_place_the_call(clean_env, tmp_path, monkeypatch):
    from fi.alk.harness.run import call

    clean_env.setenv(_MANIFEST, "")
    planted = tmp_path / "planted.py"
    planted.write_text("raise SystemExit(99)\n", encoding="utf-8")
    _load_connection_env(_env(tmp_path, f"HARNESS_VOICE_RUNNER={planted}\n"))
    commands = []
    monkeypatch.setattr(call.subprocess, "call", lambda command: commands.append(command) or 0)

    assert call.place_the_call("case") == 0

    assert commands == [[call.sys.executable, str(call.VOICE_RUNNER), "case"]]


def test_lowercase_proxies_in_a_controller_env_are_never_read_by_a_client(clean_env, tmp_path):
    clean_env.setenv(_MANIFEST, "")
    for name in ("http_proxy", "https_proxy", "no_proxy", "all_proxy"):
        clean_env.delenv(name, raising=False)
        clean_env.delenv(name.upper(), raising=False)
    source = _env(
        tmp_path,
        "http_proxy=http://canary-proxy.invalid:3128\nhttps_proxy=http://canary-proxy.invalid:3128\n"
        "no_proxy=canary.invalid\n",
    )

    _load_connection_env(source)

    assert urllib.request.getproxies_environment() == {}


def test_activation_copies_only_connection_names(clean_env, monkeypatch):
    for name, value in _GOOGLE.items():
        if name != "GOOGLE_CLOUD_LOCATION":  # left unset: a job must not fill it in
            clean_env.setenv(name, value)
    clean_env.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    for name in ("LIVEKIT_URL", "CARTESIA_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY",
                 "LIVEKIT_TARGET_AGENT_NAME"):
        clean_env.delenv(name, raising=False)
    values = {
        # The repository's .env (compose-resolved), a job's environment_values and config.
        "GOOGLE_CLOUD_LOCATION": "job-location",
        "GOOGLE_API_KEY": "job-google-key",
        "CARTESIA_API_KEY": "job-cartesia",
        "GOOGLE_APPLICATION_CREDENTIALS": "/job/google.json",
        "LIVEKIT_URL": "wss://job.invalid",
    }
    monkeypatch.setattr(provision, "runtime_environment", lambda _destination: dict(values))
    monkeypatch.setattr(provision, "infer_livekit_agent_name", lambda _destination: "")

    activated = provision.activate_voice_environment("/unused")

    assert activated == {"LIVEKIT_URL": "wss://job.invalid"}
    assert os.environ["LIVEKIT_URL"] == "wss://job.invalid"
    assert "GOOGLE_CLOUD_LOCATION" not in os.environ
    assert "GOOGLE_API_KEY" not in os.environ
    assert "CARTESIA_API_KEY" not in os.environ
    assert os.environ["GOOGLE_APPLICATION_CREDENTIALS"] == "/host/google.json"


# --- amendment 4.1: a customer's own model key, in the runtime namespace, must not come back ---

_PROVIDER_NAMES = [
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
]
# Exception 1a: an environment value for a controller connection name also reaches the controller.
_ONE_A = {"OPENAI_API_KEY", "GEMINI_API_KEY"}
_ALIASES = "ALK_TARGET_PROVIDER_ALIASES"


@pytest.fixture
def worker_runtime(clean_env, tmp_path):
    """A worker whose runtime namespace holds a customer's seven model values.

    build(form) takes "target_provider" (the caller sent them as target_provider refs) or
    "environment_value". Returns (session, canaries, docker_calls); docker_calls records
    (argv, environment) of every docker invocation at provision's subprocess.run, the boundary of
    this process. The Compose service names all seven, rendered with the values as Compose does.
    """

    def build(form):
        canaries = {name: f"canary-{name.lower().replace('_', '-')}-1" for name in _PROVIDER_NAMES}
        for name, canary in canaries.items():
            clean_env.delenv(name, raising=False)
            clean_env.setenv(f"ALK_RUNTIME_VALUE_{name}", canary)
        clean_env.setenv(_MANIFEST, ",".join(_PROVIDER_NAMES))
        clean_env.setenv(_ALIASES, ",".join(_PROVIDER_NAMES) if form == "target_provider" else "")
        source = tmp_path / "agent"
        source.mkdir()
        compose = source / "compose.yml"
        compose.write_text("services:\n  agent-runtime:\n    image: example\n", encoding="utf-8")
        session = tmp_path / "session"
        provision.ProvisionedEnvironment(
            source=str(source),
            compose_file=str(compose),
            project="fagi-harness-leakback",
            runtime_services=["agent-runtime"],
            runtime_configuration_names=provision._contract_runtime_configuration_names(None),
            running=True,
        ).save(session)
        calls = []

        def fake_run(command, **kwargs):
            calls.append((list(command), dict(kwargs["env"])))
            config = {
                "services": {
                    "agent-runtime": {"image": "example", "environment": dict(canaries)}
                }
            }
            stdout = json.dumps(config) if "config" in command else ""
            return subprocess.CompletedProcess(command, 0, stdout, "")

        clean_env.setattr(provision.subprocess, "run", fake_run)
        clean_env.setattr(
            provision, "_docker", lambda *a, **_k: "running healthy" if a and a[0] == "inspect" else ""
        )
        return session, canaries, calls

    return build


def _only_1a_names_are_in_the_controller(form, canaries):
    for name, canary in canaries.items():
        if form == "environment_value" and name in _ONE_A:
            assert os.environ.get(name) == canary, name
        else:
            assert name not in os.environ, name
        # The runtime namespace is the other place the value sits; no other variable holds it.
        holders = {key for key, value in os.environ.items() if value == canary}
        assert holders - {name} == {f"ALK_RUNTIME_VALUE_{name}"}, name


@pytest.mark.parametrize("form", ["target_provider", "environment_value"])
def test_activation_gives_the_controller_only_the_environment_values_of_1a(worker_runtime, form):
    session, canaries, _ = worker_runtime(form)
    # The values are really in what activation reads, so the test is not vacuous.
    assert provision.runtime_environment(session) == canaries

    activated = provision.activate_voice_environment(session)

    assert set(activated) & set(_PROVIDER_NAMES) == (_ONE_A if form == "environment_value" else set())
    _only_1a_names_are_in_the_controller(form, canaries)


@pytest.mark.parametrize("form", ["target_provider", "environment_value"])
def test_the_compose_run_does_not_give_the_controller_a_customers_model_key(worker_runtime, form):
    session, canaries, calls = worker_runtime(form)
    environment = provision.ProvisionedEnvironment.load(session)
    before = dict(os.environ)

    provision._run(environment, "ps")

    (argv, docker_environment), = calls
    # Compose gets every value, under its own name, in its own copy of the environment...
    for name, canary in canaries.items():
        assert docker_environment[name] == canary
    assert not any(canary in " ".join(argv) for canary in canaries.values())
    # ...and the controller's environment is untouched.
    assert dict(os.environ) == before


@pytest.mark.parametrize("form", ["target_provider", "environment_value"])
def test_the_runtime_env_flag_does_not_give_the_controller_a_customers_model_key(
    worker_runtime, form
):
    session, canaries, calls = worker_runtime(form)
    before = dict(os.environ)

    provision.start_runtime(session, stable_seconds=0.25)

    argv, docker_environment = next(c for c in calls if "run" in c[0] and "config" not in c[0])
    # The agent gets each by name: `--env NAME` inherits from docker's own environment copy.
    for name, canary in canaries.items():
        assert any(argv[i : i + 2] == ["--env", name] for i in range(len(argv) - 1)), name
        assert docker_environment[name] == canary
    assert not any(canary in " ".join(argv) for canary in canaries.values())
    assert dict(os.environ) == before


def test_a_worker_without_the_target_alias_variable_activates_nothing_from_its_values(
    worker_runtime, clean_env
):
    # The server always defines the variable. If it is missing in a worker that has the names
    # manifest, nothing the caller supplied may be promoted: the failure must not open.
    session, canaries, _ = worker_runtime("environment_value")
    clean_env.delenv(_ALIASES)

    activated = provision.activate_voice_environment(session)

    assert not set(activated) & set(_PROVIDER_NAMES)
    for name in _PROVIDER_NAMES:
        assert name not in os.environ


def test_a_local_cli_without_either_variable_still_activates_its_connection_names(
    worker_runtime, clean_env
):
    session, canaries, _ = worker_runtime("environment_value")
    clean_env.delenv(_ALIASES)
    clean_env.delenv(_MANIFEST)

    activated = provision.activate_voice_environment(session)

    assert {n for n in activated if n in _PROVIDER_NAMES} == _ONE_A
