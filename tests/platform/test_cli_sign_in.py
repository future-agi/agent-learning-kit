"""TH-8848: sign in to the platform from `agent-learn` and from Python."""

from __future__ import annotations

import pytest

from fi.alk.platform import Client, PlatformError

pytestmark = pytest.mark.live_platform

UNKNOWN_WORKSPACE = "00000000-0000-4000-8000-000000000000"
UNREACHABLE = "http://127.0.0.1:9"  # nothing listens on the discard port


def _login(cli, platform, *extra, base_url=None, api_key=None, secret_key=None):
    return cli(
        "auth",
        "login",
        "--base-url",
        base_url or platform.base_url,
        "--non-interactive",
        "--output",
        "json",
        *extra,
        env={
            "FI_API_KEY": api_key or platform.api_key,
            "FI_SECRET_KEY": secret_key or platform.secret_key,
        },
    )


def test_login_then_doctor_pass_and_never_show_the_secret(cli, platform):
    login = _login(cli, platform)
    status = cli("auth", "status", "--output", "json")
    doctor = cli("doctor", "--output", "json")

    assert (login.code, status.code, doctor.code) == (0, 0, 0), doctor.stdout
    shown = status.json()
    assert shown["user"]["email"]
    assert shown["workspace"]["id"]
    assert shown["api_key"] == "…" + platform.api_key[-4:]
    checks = {check["id"]: check["status"] for check in doctor.json()["checks"]}
    assert checks == {
        "signed_in": "passed",
        "platform_reachable": "passed",
        "workspace": "passed",
        "cli_version": "info",
    }
    printed = login.stdout + status.stdout + doctor.stdout + status.stderr
    assert platform.secret_key not in printed
    assert platform.api_key not in printed


@pytest.mark.parametrize(
    ("failure", "category", "exit_code"),
    [
        ("bad_key", "auth", 4),
        # The platform answers an unusable workspace with its default one; acting
        # there instead would be worse than failing.
        ("unknown_workspace", "not_found", 7),
        ("unreachable", "infrastructure", 9),
    ],
)
def test_each_failure_has_one_shape_in_the_cli_and_the_sdk(
    cli, platform, failure, category, exit_code
):
    given = {
        "bad_key": {"api_key": "not-a-key", "secret_key": "not-a-secret"},
        "unknown_workspace": {"workspace_id": UNKNOWN_WORKSPACE},
        "unreachable": {"base_url": UNREACHABLE},
    }[failure]
    extra = ("--workspace", UNKNOWN_WORKSPACE) if failure == "unknown_workspace" else ()

    login = _login(
        cli,
        platform,
        *extra,
        base_url=given.get("base_url"),
        api_key=given.get("api_key"),
        secret_key=given.get("secret_key"),
    )

    assert login.code == exit_code
    error = login.json()["error"]
    assert set(error) == {"category", "code", "message", "fix", "status"}
    assert error["category"] == category
    assert (
        cli("auth", "status", "--output", "json").json()["error"]["code"]
        == "not_signed_in"
    )

    sdk = Client(
        base_url=given.get("base_url", platform.base_url),
        api_key=given.get("api_key", platform.api_key),
        secret_key=given.get("secret_key", platform.secret_key),
        workspace_id=given.get("workspace_id"),
    )
    with pytest.raises(PlatformError) as raised, sdk:
        sdk.whoami()
    assert (raised.value.category, raised.value.exit_code) == (category, exit_code)


def test_logging_out_erases_the_secret_from_disk(cli, platform):
    assert _login(cli, platform).code == 0

    assert cli("auth", "logout").code == 0

    assert (
        cli("auth", "status", "--output", "json").json()["error"]["code"]
        == "not_signed_in"
    )
    on_disk = "".join(
        path.read_text(errors="ignore")
        for path in cli.home.rglob("*")
        if path.is_file()
    )
    assert platform.secret_key not in on_disk
