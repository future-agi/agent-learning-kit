"""A browser agent runs only where the job gives it a browser and its sites."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fi.alk.harness import cli
from fi.alk.harness.agent_type_check import EXIT_REFUSED
from fi.alk.harness.browser_environment import (
    BROWSER_ENVIRONMENT_REQUIRED,
    BROWSER_SITE_NOT_IN_EGRESS,
    BROWSER_SITES_INVALID,
    SitesProblem,
    allowed_sites,
    environment_failure,
    job_failure,
)
from fi.alk.harness.contract import AgentContract
from fi.alk.harness.failure_file import FAILURE_FILE
from fi.alk.harness.hosted_chat_entrypoint import HostedChatRuntime

_BROWSER = {
    "agent": "shopper",
    "one_liner": "Buys things on websites.",
    "modality": "chat",
    "environment_needs": ["browser"],
}
_CHAT = {"agent": "helper", "one_liner": "Answers questions.", "modality": "chat"}
_GITHUB = {
    "kind": "github",
    "visibility": "public",
    "repository": "owner/repo",
    "commit_sha": "0123456789abcdef0123456789abcdef01234567",
}


def _problem(metadata) -> SitesProblem:
    with pytest.raises(SitesProblem) as raised:
        allowed_sites(metadata)
    return raised.value


# The job's list


@pytest.mark.parametrize("metadata", [None, {}, {"egress_allowed_hosts": ["a.com"]}, "not a map"])
def test_no_list_is_no_browser(metadata):
    assert allowed_sites(metadata) is None
    assert job_failure(metadata) is None


@pytest.mark.parametrize(
    "value, reason",
    [([], "empty"), ("shop.example.com", "not_a_list"), (None, "not_a_list"), ({}, "not_a_list")],
)
def test_a_list_present_and_empty_or_not_a_list_fails(value, reason):
    failure = _problem({"browser_allowed_sites": value}).failure
    assert failure.code == BROWSER_SITES_INVALID
    assert failure.details == {"reason": reason}
    assert failure.retryable is False
    assert job_failure({"browser_allowed_sites": value}) == failure


@pytest.mark.parametrize(
    "site",
    [
        "https://shop.example.com",
        "shop.example.com:443",
        "shop.example.com/cart",
        "user:pw@shop.example.com",
        "127.0.0.1",
        "10.0.0.8",
        "localhost",
        "LOCALHOST",
        "app.localhost",
        "::1",
        "",
        " shop.example.com",
        "-bad.example.com",
        "*.",
        "a." * 130 + "com",
        7,
        None,
    ],
)
def test_a_site_that_is_not_a_host_name_fails_by_position_only(site):
    failure = _problem({"browser_allowed_sites": ["ok.example.com", site]}).failure
    assert failure.code == BROWSER_SITES_INVALID
    # The value itself is never repeated: it may be a URL holding a password.
    assert failure.details == {"reason": "not_a_host_name", "positions": [1]}
    assert str(site) not in failure.message or site in ("", None, 7)


def test_host_names_are_kept_lower_case_once_each():
    sites = allowed_sites(
        {"browser_allowed_sites": ["Shop.Example.COM", "*.docs.example.com", "shop.example.com"]}
    )
    assert sites == ("shop.example.com", "*.docs.example.com")


def test_each_site_must_be_one_the_egress_allows():
    metadata = {
        "browser_allowed_sites": ["shop.example.com", "pay.other.com", "*.docs.example.com"],
        "egress_allowed_hosts": ["*.example.com", "pypi.org"],
    }
    failure = _problem(metadata).failure
    assert failure.code == BROWSER_SITE_NOT_IN_EGRESS
    assert failure.details == {"sites": ["pay.other.com"]}
    assert failure.message == "The run's egress doesn't allow these browser sites: pay.other.com."
    metadata["browser_allowed_sites"] = ["shop.example.com", "example.com", "*.docs.example.com"]
    assert allowed_sites(metadata) == ("shop.example.com", "example.com", "*.docs.example.com")


def test_loopback_in_the_egress_does_not_admit_a_site():
    # The egress always admits loopback; a site never is loopback, so it never rides on that.
    failure = _problem({"browser_allowed_sites": ["localhost"], "egress_allowed_hosts": []}).failure
    assert failure.code == BROWSER_SITES_INVALID


# The contract against the job


def test_an_agent_that_needs_no_browser_is_never_refused_here():
    contract = AgentContract.model_validate(_CHAT)
    assert environment_failure(contract, {}) is None
    # Not even with a broken list: no browser is started, and that list is the job's own check.
    assert environment_failure(contract, {"browser_allowed_sites": []}) is None


def test_a_browser_agent_with_no_sites_is_refused():
    failure = environment_failure(AgentContract.model_validate(_BROWSER), {})
    assert failure.code == BROWSER_ENVIRONMENT_REQUIRED
    assert failure.domain.value == "environment"
    assert failure.retryable is False
    assert failure.details == {"needs": ["browser"]}


def test_a_browser_agent_with_a_broken_list_is_refused_for_the_list():
    failure = environment_failure(
        AgentContract.model_validate(_BROWSER), {"browser_allowed_sites": []}
    )
    assert failure.code == BROWSER_SITES_INVALID


def test_a_browser_agent_with_sites_is_kept():
    contract = AgentContract.model_validate(_BROWSER)
    assert environment_failure(contract, {"browser_allowed_sites": ["shop.example.com"]}) is None


def test_an_older_contract_stored_as_browser_is_left_as_it_was():
    contract = AgentContract.model_validate({**_CHAT, "modality": "browser"})
    assert environment_failure(contract, {}) is None


# Where a build refuses


def _write(folder: Path, body: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "contract.json").write_text(json.dumps(body), encoding="utf-8")


def _failure(folder: Path) -> dict:
    return json.loads((folder / FAILURE_FILE).read_text(encoding="utf-8"))


def test_the_refusal_is_written_where_the_build_reads_it(tmp_path):
    _write(tmp_path, _BROWSER)
    job = SimpleNamespace(metadata={})
    assert cli.environment_refusal(tmp_path, job) == BROWSER_ENVIRONMENT_REQUIRED
    assert _failure(tmp_path)["code"] == BROWSER_ENVIRONMENT_REQUIRED


@pytest.mark.parametrize("body", [None, _CHAT])
def test_nothing_is_written_without_a_need(tmp_path, body):
    if body is not None:
        _write(tmp_path, body)
    assert cli.environment_refusal(tmp_path, SimpleNamespace(metadata={})) is None
    assert not (tmp_path / FAILURE_FILE).exists()


def _auto(tmp_path, monkeypatch, body, metadata):
    from fi.alk.harness.job import HarnessJob

    source = tmp_path / "agent"
    source.mkdir()
    output = tmp_path / "authoring"
    ran: list[str] = []

    async def understand(_args) -> int:
        ran.append("understand")
        _write(output, body)
        return 0

    async def later(label):
        ran.append(label)
        return 0

    monkeypatch.setattr(cli, "_understand", understand)
    monkeypatch.setattr(cli, "_build", lambda _a: later("environment"))
    monkeypatch.setattr(cli, "_scenarios", lambda _a: later("scenarios"))
    monkeypatch.setattr("fi.alk.harness.provision.stop", lambda _destination: None)
    job = HarnessJob(
        job_id="job",
        run_id="run",
        execution="hosted",
        source=_GITHUB,
        runtime={"isolation": "dedicated_vm"},
        agent={"connector": "auto"},
        metadata=metadata,
    )
    status = asyncio.run(
        cli._auto(
            SimpleNamespace(
                path=str(source),
                name="agent",
                kind="repo",
                out=str(output),
                count=1,
                model=None,
                run_model=None,
                authoring_only=True,
                job=job,
            )
        )
    )
    return status, ran, output


def test_a_build_stops_after_a_reading_that_needs_a_browser_it_isnt_given(tmp_path, monkeypatch):
    status, ran, output = _auto(tmp_path, monkeypatch, _BROWSER, {})
    assert status == EXIT_REFUSED
    assert ran == ["understand"]
    assert _failure(output)["code"] == BROWSER_ENVIRONMENT_REQUIRED


def test_a_build_given_sites_goes_on(tmp_path, monkeypatch):
    status, ran, output = _auto(
        tmp_path, monkeypatch, _BROWSER, {"browser_allowed_sites": ["shop.example.com"]}
    )
    # Past the check: what the later stages make of a fake stage's empty output is theirs.
    assert status != EXIT_REFUSED
    assert ran[:2] == ["understand", "environment"]
    assert not (output / FAILURE_FILE).exists()


# The hosted build, once its reading saves


def _hosted(workspace: Path, metadata: dict) -> HostedChatRuntime:
    runtime = object.__new__(HostedChatRuntime)
    runtime.workspace = workspace
    runtime.job = {
        "job_id": "job",
        "run_id": "run",
        "execution": "hosted",
        "source": _GITHUB,
        "runtime": {"isolation": "dedicated_vm"},
        "agent": {"connector": "auto"},
        "metadata": metadata,
        "scenario_count": 2,
    }
    runtime.client = SimpleNamespace(
        turn_context={"capabilities": {"control_only": True}}, command_watermark=3
    )
    runtime.conversation = SimpleNamespace(stage_name="builder", stage=None)
    runtime._nudge = None
    runtime._from_platform = False
    runtime._last_progress, runtime._idle_turns, runtime._opened_with = None, 0, None
    runtime._what_exists = lambda: ((workspace / "contract.json").is_file(), False)
    runtime._after_saved = lambda: None
    runtime.handled = []

    async def handle(command):
        runtime.handled.append(command)

    runtime._handle = handle
    return runtime


def test_the_hosted_build_stops_once_a_reading_needs_a_browser_it_isnt_given(tmp_path):
    _write(tmp_path, _BROWSER)
    runtime = _hosted(tmp_path, {})
    assert asyncio.run(runtime._carry_on()) is False
    assert runtime.handled == []
    assert _failure(tmp_path)["code"] == BROWSER_ENVIRONMENT_REQUIRED
    # And it stays stopped: the build is over.
    assert runtime._build_is_over()


def test_the_hosted_build_carries_on_with_sites(tmp_path):
    _write(tmp_path, _BROWSER)
    runtime = _hosted(tmp_path, {"browser_allowed_sites": ["shop.example.com"]})
    assert asyncio.run(runtime._carry_on()) is True
    assert len(runtime.handled) == 1
    assert not (tmp_path / FAILURE_FILE).exists()


def test_the_hosted_check_needs_only_the_jobs_metadata(tmp_path):
    # A job this process can't validate still has its metadata read: it never passes unchecked.
    _write(tmp_path, _BROWSER)
    runtime = _hosted(tmp_path, {})
    runtime.job = {"scenario_count": 2, "metadata": {}}
    assert asyncio.run(runtime._carry_on()) is False
    assert _failure(tmp_path)["code"] == BROWSER_ENVIRONMENT_REQUIRED
