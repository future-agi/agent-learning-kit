"""How a world's processes meet the kit's browser: started for a browser agent, given to its process
alone, started anew with the world, and closed with it."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path

import pytest
from test_process_runtime import (
    FakeHandle,
    _fake_user_resolver,
    _frozen_template_database,
    _manifest,
    _provision_dirs,
    _provision_one,
    _sql_spy_provider,
)

from fi.alk.harness import browser_runtime as br
from fi.alk.harness import kit_browser
from fi.alk.harness import process_runtime as pr
from fi.alk.harness.kit_browser import BROWSER_CDP_URL_ENV


class _FakeBrowser:
    def __init__(self, world_index: int, log) -> None:
        self.world_index = world_index
        self.log = log
        self.starts = 0
        self.stops = 0
        self.closed = False

    def environment(self) -> dict[str, str]:
        return {BROWSER_CDP_URL_ENV: f"http://127.0.0.1:9{self.world_index}22"}

    def metadata(self) -> dict[str, str]:
        return {
            "state": "started",
            "state_path": f"/kit/browser-w{self.world_index}.json",
        }

    def start(self) -> None:
        self.starts += 1

    def stop(self) -> None:
        self.stops += 1

    def close(self) -> None:
        self.closed = True


def _browser_world(tmp_path: Path, *, needs_browser: bool = True):
    manifest = _manifest(lambda body: {**body, "readiness": []})
    freeze, context, _ = _frozen_template_database(manifest, tmp_path)
    made: list[_FakeBrowser] = []
    environments: dict[str, dict[str, str]] = {}

    def runner(argv, *, cwd, env, log_path, user=None, group=None, **_):
        environments[argv[0]] = dict(env)
        return FakeHandle()

    def factory(world_index, log):
        made.append(_FakeBrowser(world_index, log))
        return made[-1]

    context = dataclasses.replace(
        context,
        runner=runner,
        browser_allowed_sites=("shop.example.com",),
        needs_browser=needs_browser,
        browser_factory=factory,
    )
    return manifest, freeze, context, made, environments


def _spawn(manifest, freeze, context, existing=None, *, keep_processes=False):
    return pr._clone_or_reset_world(
        manifest,
        0,
        context=context,
        baseline=freeze.build_output,
        job_shared_handles=freeze.job_shared_handles,
        existing_handles=existing or {},
        keep_processes=keep_processes,
    )


def test_a_browser_agent_s_process_alone_is_given_the_world_s_browser(tmp_path) -> None:
    manifest, freeze, context, made, environments = _browser_world(tmp_path)
    _spawn(manifest, freeze, context)
    [browser] = made
    assert browser.starts == 1
    # The control process is the agent (svc-agent); tools-api runs the world's tools (svc-tools).
    assert environments["python3"][BROWSER_CDP_URL_ENV] == "http://127.0.0.1:9022"
    assert BROWSER_CDP_URL_ENV not in environments["node"]


def test_each_spawn_of_the_world_starts_a_new_browser_and_a_reset_never_keeps_the_agent_s_process(
    tmp_path,
) -> None:
    manifest, freeze, context, made, _ = _browser_world(tmp_path)
    first = _spawn(manifest, freeze, context)
    agent = first.handles["agent"].handle
    _spawn(manifest, freeze, context, first.handles, keep_processes=True)
    [browser] = made
    assert browser.starts == 2
    # An agent holding a dead connection to the last browser would start the trial broken.
    assert agent.terminated


def test_a_reset_stops_the_last_browser_before_the_world_s_records_are_sealed_again(tmp_path, monkeypatch) -> None:
    # A page still open in the last trial's browser must not reach the world once its records are sealed again.
    manifest, freeze, context, made, _ = _browser_world(tmp_path)
    first = _spawn(manifest, freeze, context)
    [browser] = made
    order: list[str] = []
    stop = browser.stop
    monkeypatch.setattr(browser, "stop", lambda: (order.append("browser stopped"), stop())[1])
    seal = pr._seal_world_store
    monkeypatch.setattr(pr, "_seal_world_store", lambda *a, **k: (order.append("sealed"), seal(*a, **k))[1])
    _spawn(manifest, freeze, context, first.handles, keep_processes=True)
    assert order and order[0] == "browser stopped"


@pytest.mark.parametrize("error", [OSError("no such file"), RuntimeError("did not open its DevTools port")])
def test_a_browser_that_does_not_start_fails_the_spawn(tmp_path, error) -> None:
    manifest, freeze, context, _, _ = _browser_world(tmp_path)

    class _Broken:
        def start(self) -> None:
            raise error

    context = dataclasses.replace(context, browser_factory=lambda world_index, log: _Broken())
    with pytest.raises(pr.ProcessRuntimeError) as raised:
        _spawn(manifest, freeze, context)
    assert (raised.value.code, raised.value.domain) == ("spawn_failed", pr.FailureDomain.INFRASTRUCTURE)


def test_a_browser_that_cannot_be_made_fails_the_spawn_as_the_platform_s(tmp_path) -> None:
    manifest, freeze, context, _, _ = _browser_world(tmp_path)

    def make(world_index, log):
        raise OSError("no port for the browser's relay")

    context = dataclasses.replace(context, browser_factory=make)
    with pytest.raises(pr.ProcessRuntimeError) as raised:
        _spawn(manifest, freeze, context)
    assert (raised.value.code, raised.value.domain) == ("spawn_failed", pr.FailureDomain.INFRASTRUCTURE)


def test_a_contract_that_cannot_be_read_is_logged_not_taken_silently_for_no_browser(tmp_path, caplog) -> None:
    (tmp_path / "contract.json").write_text("{not json")
    assert br.contract_needs_browser(tmp_path) is False
    assert "contract" in caplog.text


@pytest.mark.parametrize(
    "text",
    [
        '{"agent": "MARKER-AGENT", "one_liner": "MARKER-LINE", "modality": "chat", "environment_needs": "MARKER-NEED"}',
        '{"agent": "MARKER-AGENT", "prompt_excerpt": "MARKER-PROMPT',
    ],
    ids=["wrong-type", "broken-json"],
)
def test_a_contract_that_cannot_be_read_never_puts_its_content_in_the_log(tmp_path, caplog, text) -> None:
    # The contract is the customer's text: the log says where it is and which fields failed, never what they hold.
    (tmp_path / "contract.json").write_text(text)
    assert br.contract_needs_browser(tmp_path) is False
    assert "contract" in caplog.text
    assert "MARKER" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_sites_for_an_agent_that_needs_no_browser_start_none(tmp_path) -> None:
    manifest, freeze, context, made, environments = _browser_world(
        tmp_path, needs_browser=False
    )
    _spawn(manifest, freeze, context)
    assert made == []
    assert BROWSER_CDP_URL_ENV not in environments["python3"]


def test_the_world_s_runtime_says_what_became_of_its_browser(tmp_path) -> None:
    manifest, freeze, context, made, _ = _browser_world(tmp_path)
    _spawn(manifest, freeze, context)
    assert br.browser_metadata(context, 0) == {"browser": made[0].metadata()}
    no_need = dataclasses.replace(context, needs_browser=False)
    assert br.browser_metadata(no_need, 0) == {"browser": {"state": "not_started"}}
    assert (
        br.browser_metadata(
            dataclasses.replace(context, browser_allowed_sites=None), 0
        )
        == {}
    )


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({}, None),
        (
            {"browser_allowed_sites": ["shop.example.com", "*.cdn.example.com"]},
            ("shop.example.com", "*.cdn.example.com"),
        ),
    ],
)
def test_the_job_s_browser_sites_are_read_for_the_spawn(
    tmp_path, metadata, expected
) -> None:
    (tmp_path / "job.json").write_text(json.dumps({"metadata": metadata}))
    assert br.read_browser_allowed_sites(tmp_path) == expected


def test_browser_sites_that_are_wrong_fail_the_spawn(tmp_path) -> None:
    (tmp_path / "job.json").write_text(
        json.dumps({"metadata": {"browser_allowed_sites": ["http://x.com/"]}})
    )
    with pytest.raises(pr.ProcessRuntimeError) as raised:
        br.read_browser_allowed_sites(tmp_path)
    assert raised.value.code == "spawn_failed"


def test_the_contract_says_whether_the_agent_needs_a_browser(tmp_path) -> None:
    assert br.contract_needs_browser(tmp_path) is False
    contract = {
        "agent": "shopper",
        "one_liner": "Buys things on websites.",
        "modality": "chat",
    }
    (tmp_path / "contract.json").write_text(json.dumps(contract))
    assert br.contract_needs_browser(tmp_path) is False
    (tmp_path / "contract.json").write_text(
        json.dumps({**contract, "environment_needs": ["browser"]})
    )
    assert br.contract_needs_browser(tmp_path) is True
    (tmp_path / "contract.json").write_text("{not json")
    assert br.contract_needs_browser(tmp_path) is False


def test_closing_the_world_s_browsers_closes_each(tmp_path) -> None:
    manifest, freeze, context, made, _ = _browser_world(tmp_path)
    _spawn(manifest, freeze, context)
    br.close_browsers(context)
    assert made[0].closed
    assert context.browsers == {}


def test_removing_one_world_closes_that_world_s_browser_alone(tmp_path) -> None:
    manifest, freeze, context, made, _ = _browser_world(tmp_path)
    _spawn(manifest, freeze, context)
    other = _FakeBrowser(1, None)
    context.browsers[1] = other
    br.close_browsers(context, 0)
    assert made[0].closed and not other.closed
    assert context.browsers == {1: other}


# The browser the kit makes for a world


def _factory_context(tmp_path, *, resolver, require=False):
    manifest, freeze, context, _, _ = _browser_world(tmp_path)
    launched: list[dict] = []
    chowned: list[tuple[str, int, int]] = []

    def runner(argv, *, cwd, env, log_path, user=None, group=None, **_):
        launched.append({"argv": list(argv), "env": dict(env), "user": user, "group": group})
        # Stands in for Chromium: it writes the port it chose into its profile.
        (Path(cwd) / "DevToolsActivePort").write_text("9333\n/devtools/browser/x\n")
        return FakeHandle()

    return (
        dataclasses.replace(
            context,
            runner=runner,
            browser_factory=None,
            user_resolver=resolver,
            require_declared_user=require,
            chown=lambda path, uid, gid, dir_fd=None: chowned.append((str(path), uid, gid)),
        ),
        launched,
        chowned,
    )


def test_the_kit_runs_the_browser_as_the_agent_s_user_with_nothing_of_its_own(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(kit_browser, "chromium_binary", lambda: "/opt/alk-chromium")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    context, launched, chowned = _factory_context(
        tmp_path, resolver=_fake_user_resolver({"svc-agent": (1234, 5678)})
    )
    browser = br.kit_browser_factory(context)(0, None)
    try:
        browser.start()
    finally:
        browser.close()
    [launch] = launched
    assert launch["argv"][0] == "/opt/alk-chromium"
    assert (launch["user"], launch["group"]) == (1234, 5678)
    assert set(launch["env"]) <= {"PATH", "HOME", "TMPDIR", "LANG", "FONTCONFIG_PATH"}
    profile = next(one.split("=", 1)[1] for one in launch["argv"] if one.startswith("--user-data-dir="))
    assert chowned == [(profile, 1234, 5678)]
    # Its state is where the kit writes and the agent's user does not.
    assert browser.metadata()["state_path"] == str(tmp_path / "browser-state" / "w0.json")


def test_a_hosted_world_with_no_agent_user_starts_no_browser(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(kit_browser, "chromium_binary", lambda: "/opt/alk-chromium")
    context, launched, _ = _factory_context(tmp_path, resolver=lambda name: None, require=True)
    with pytest.raises(pr.ProcessRuntimeError) as raised:
        br.kit_browser_factory(context)
    assert raised.value.code == "spawn_failed"
    assert launched == []


def test_a_world_process_named_browser_keeps_its_folder_when_the_kit_s_browser_starts(
    tmp_path, monkeypatch
) -> None:
    import re

    monkeypatch.setattr(kit_browser, "chromium_binary", lambda: "/opt/alk-chromium")
    context, _, _ = _factory_context(tmp_path, resolver=_fake_user_resolver({"svc-agent": (1234, 5678)}))
    # A bundle may name a world process `browser`; its folder holds its own files and must outlive the kit's browser.
    theirs = pr.world_scratch_dir(context.work_directory, 0, "browser")
    theirs.mkdir(parents=True)
    (theirs / "uploads.db").write_text("the process's own data")
    browser = br.kit_browser_factory(context)(0, None)
    try:
        browser.start()
        browser.stop()
        browser.start()
    finally:
        browser.close()
    assert (theirs / "uploads.db").read_text() == "the process's own data"
    # The kit's folder has a name no world process can have.
    assert not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", Path(browser.directory).name)


def test_an_image_with_no_browser_fails_the_spawn(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(kit_browser, "chromium_binary", lambda: None)
    context, _, _ = _factory_context(tmp_path, resolver=lambda name: None)
    with pytest.raises(pr.ProcessRuntimeError) as raised:
        br.kit_browser_factory(context)
    assert raised.value.code == "spawn_failed"


# The provider: the job's sites and the contract in, the runtime's metadata out, the browser closed with its world


def _browser_provider(tmp_path, monkeypatch):
    made: list[_FakeBrowser] = []
    environments: dict[str, dict[str, str]] = {}

    def factory(world_index, log):
        made.append(_FakeBrowser(world_index, log))
        return made[-1]

    def runner(argv, *, cwd, env, log_path, user=None, group=None, **_):
        environments[argv[0]] = dict(env)
        return FakeHandle()

    monkeypatch.setattr(br, "kit_browser_factory", lambda context: factory)
    manifest = _manifest()
    source, bundle_dir = _provision_dirs(tmp_path)
    (bundle_dir / "contract.json").write_text(
        json.dumps(
            {
                "agent": "shopper",
                "one_liner": "Buys things on websites.",
                "modality": "chat",
                "environment_needs": ["browser"],
            }
        )
    )
    (tmp_path / "job.json").write_text(
        json.dumps({"metadata": {"browser_allowed_sites": ["shop.example.com"]}})
    )
    provider = _sql_spy_provider(runner=runner)
    [runtime] = _provision_one(provider, manifest, source, bundle_dir, tmp_path)
    return provider, runtime, made, environments


def test_a_provisioned_browser_world_gives_its_agent_the_browser_and_says_so(
    tmp_path, monkeypatch
) -> None:
    provider, runtime, made, environments = _browser_provider(tmp_path, monkeypatch)
    [browser] = made
    assert runtime.metadata["browser"] == browser.metadata()
    assert environments["python3"][BROWSER_CDP_URL_ENV] == "http://127.0.0.1:9022"
    provider._teardown_world(0)
    assert browser.closed


def test_a_job_with_no_browser_sites_never_reads_the_contract(tmp_path, monkeypatch) -> None:
    read: list[object] = []
    monkeypatch.setattr(pr, "contract_needs_browser", lambda bundle_dir: read.append(bundle_dir) or True)
    manifest = _manifest()
    source, bundle_dir = _provision_dirs(tmp_path)
    (tmp_path / "job.json").write_text(json.dumps({"metadata": {}}))
    provider = _sql_spy_provider(runner=lambda argv, **_: FakeHandle())
    [runtime] = _provision_one(provider, manifest, source, bundle_dir, tmp_path)
    assert read == []
    assert "browser" not in runtime.metadata


def test_closing_the_provider_closes_every_world_s_browser(tmp_path, monkeypatch) -> None:
    provider, _, made, _ = _browser_provider(tmp_path, monkeypatch)
    asyncio.run(provider.close(work_directory=tmp_path))
    assert made and all(browser.closed for browser in made)
