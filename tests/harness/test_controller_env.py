"""TH-9550: a repository's .env must not add to the sandbox controller's environment."""

import os
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
        if name.startswith("ALK_RUNTIME_") or name in {
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
    # Reserved names are skipped outright, not renamed into the runtime namespace.
    assert not [n for n in os.environ if n.startswith("ALK_RUNTIME_VALUE_")]
    assert _manifest() == []


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
        "export PATH=/repo/bin\nHOME =x\nALK_SANDBOX_TOKEN=x\nDOCKER_CONFIG=x\nMY_SETTING=ok\n",
    )
    path_before, home_before = os.environ["PATH"], os.environ.get("HOME")

    _load_connection_env(source)

    assert os.environ["PATH"] == path_before
    assert os.environ.get("HOME") == home_before
    assert "ALK_SANDBOX_TOKEN" not in os.environ
    assert "DOCKER_CONFIG" not in os.environ
    for name in ("PATH", "HOME", "ALK_SANDBOX_TOKEN", "DOCKER_CONFIG"):
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
    assert not [n for n in os.environ if "LD_PRELOAD" in n or "VOICE_RUNNER" in n]


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
