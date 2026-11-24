"""The build id a reading records comes from the image the kit runs in, never from the checkout
that publishes a template over some other image (TH-10400)."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fi.alk.harness import seed_contract

ROOT = Path(__file__).resolve().parents[1]
CLEAN = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture
def script():
    path = ROOT / "scripts" / "e2b-template.py"
    spec = importlib.util.spec_from_file_location("e2b_template_script", path)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    try:
        spec.loader.exec_module(loaded)
        yield loaded
    finally:
        sys.modules.pop(spec.name, None)


def _recorded(monkeypatch, value: str) -> str:
    monkeypatch.setenv("ALK_BUILD_ID", value)
    return seed_contract._build_id()


def test_the_image_carries_its_own_build_id(monkeypatch):
    lines = (ROOT / "Dockerfile.hosted").read_text().splitlines()
    arg = next(
        i
        for i, line in enumerate(lines)
        if line.startswith("ARG ALK_HOSTED_SOURCE_REVISION=")
    )
    env = lines.index("ENV ALK_BUILD_ID=${ALK_HOSTED_SOURCE_REVISION}")
    assert env > arg
    # An image built without the argument (the Daytona snapshot, a local build) records no build.
    default = lines[arg].split("=", 1)[1]
    assert _recorded(monkeypatch, default) == ""


def test_an_image_built_from_a_dirty_checkout_records_no_build(script, monkeypatch):
    assert script._image_revision(CLEAN, dirty=False) == CLEAN
    dirty = script._image_revision(CLEAN, dirty=True)
    assert dirty.startswith(CLEAN)
    assert _recorded(monkeypatch, dirty) == ""


def test_the_template_stamps_a_build_id_only_for_an_image_this_run_built(script):
    assert "ALK_BUILD_ID" not in script._template_envs(None)
    assert script._template_envs(CLEAN) == {
        **script.HOSTED_RUNTIME_ENV,
        "ALK_BUILD_ID": CLEAN,
    }
    assert "ALK_BUILD_ID" not in script.HOSTED_RUNTIME_ENV


class _Published(Exception):
    pass


@pytest.fixture
def template_envs(monkeypatch):
    """The environment main() gives the E2B template, through a stand-in for the e2b SDK whose
    build stops the run."""
    seen: list[dict[str, str]] = []

    class Template:
        def from_image(self, image, **kwargs):
            return self

        def set_envs(self, envs):
            seen.append(dict(envs))
            return self

        def set_user(self, user):
            return self

        def set_workdir(self, workdir):
            return self

        @staticmethod
        def build(template, **kwargs):
            raise _Published

    monkeypatch.setitem(sys.modules, "e2b", SimpleNamespace(Template=Template))
    monkeypatch.setenv("E2B_API_KEY", "test-key")
    return seen


def _run(script, monkeypatch, *argv: str, status: str = "") -> None:
    monkeypatch.setattr(
        script, "_git", lambda repo, *args: CLEAN if args[0] == "rev-parse" else status
    )
    monkeypatch.setattr(sys, "argv", ["e2b-template.py", *argv])
    with pytest.raises(_Published):
        script.main()


def test_a_given_image_is_published_without_the_checkouts_commit(
    script, monkeypatch, template_envs
):
    # An image built at commit A, published from a checkout at commit B.
    monkeypatch.setattr(
        script,
        "build_and_push_image",
        lambda **kwargs: pytest.fail("no image is built"),
    )

    _run(script, monkeypatch, "--image-reference", "example/alk@sha256:" + "a" * 64)

    assert template_envs == [dict(script.HOSTED_RUNTIME_ENV)]


@pytest.mark.parametrize("dirty", [False, True], ids=["clean", "dirty"])
def test_an_image_built_here_is_published_with_the_revision_it_was_built_with(
    script, monkeypatch, template_envs, dirty
):
    built: dict = {}

    def build(**kwargs):
        built.update(kwargs)
        return script.PublishedImage(tag="example/alk:t", digest="sha256:" + "b" * 64)

    monkeypatch.setattr(script, "build_and_push_image", build)

    _run(
        script,
        monkeypatch,
        *(["--allow-dirty"] if dirty else []),
        status=" M src/fi/alk/harness/seed_contract.py" if dirty else "",
    )

    (envs,) = template_envs
    assert envs["ALK_BUILD_ID"] == built["source_revision"]
    # A dirty build records no build.
    assert _recorded(monkeypatch, envs["ALK_BUILD_ID"]) == ("" if dirty else CLEAN)


def _browser_check(script) -> str:
    catalog = json.loads((ROOT / "hosted-snapshot" / "catalog.json").read_text())
    return dict(script._base_checks(catalog))["browser-chromium"]


def test_the_image_puts_the_kits_browser_where_its_certification_looks():
    text = (ROOT / "Dockerfile.hosted").read_text()
    assert "playwright install --with-deps --no-shell chromium" in text
    assert 'ln -s "$chrome" /usr/local/bin/alk-chromium' in text
    assert "chmod -R a+rX,a-w /opt/ms-playwright" in text
    # The folder is not the default for an agent's own Playwright.
    assert "ENV PLAYWRIGHT_BROWSERS_PATH" not in text


def test_the_playwright_pin_is_set_in_one_place():
    text = (ROOT / "Dockerfile.hosted").read_text()
    assert text.count("ARG PLAYWRIGHT_VERSION=") == 1
    assert '"playwright==${PLAYWRIGHT_VERSION}"' in text
    assert "playwright==1" not in text


@pytest.mark.parametrize("compose_profile", [False, True])
def test_every_template_profile_certifies_the_browser(script, compose_profile):
    catalog = json.loads((ROOT / "hosted-snapshot" / "catalog.json").read_text())
    labels = [
        label
        for label, _ in script._base_checks(catalog, compose_profile=compose_profile)
    ]
    assert labels.count("browser-chromium") == 1


# A PNG's eight-byte signature, as a printf format.
_PNG = "\\211PNG\\r\\n\\032\\n"

_FAKE_CHROMIUM = """#!/bin/sh
# Stands in for Chromium. For --screenshot it writes PNG_BYTES. For --dump-dom it prints the DOM
# a page's script left (RENDERS=1), or only its own arguments back, as a binary that loads no page.
for arg in "$@"; do
  case "$arg" in
    --screenshot=*) printf "${PNG_BYTES}" > "${arg#--screenshot=}" ;;
  esac
done
case " $* " in
  *" --dump-dom "*)
    if [ "$RENDERS" = 1 ]; then printf '<html><head></head><body data-ready="42"></body></html>\\n'
    else printf '[ERROR] could not load page: %s\\n' "$*"; fi ;;
esac
"""


# Stands in for GNU stat with the real mode and the owner a test names, since a test can't make a
# file root's. A folder reports DIR_OWNER, anything else FILE_OWNER.
_FAKE_STAT = """#!/usr/bin/env python3
import os, stat, sys
args = sys.argv[1:]
follow = "-L" in args
args = [a for a in args if a != "-L"]
fmt, path = args[args.index("-c") + 1], args[-1]
info = os.stat(path) if follow else os.lstat(path)
owner = os.environ["DIR_OWNER" if stat.S_ISDIR(info.st_mode) else "FILE_OWNER"]
print(fmt.replace("%a", format(info.st_mode & 0o7777, "o")).replace("%U", owner))
"""


def _run_browser_check(
    script,
    tmp_path,
    *,
    mode: int,
    png_bytes: str,
    renders: bool = True,
    folder_mode: int = 0o555,
    file_owner: str = "root",
    folder_owner: str = "root",
):
    # As in the image: alk-chromium on the path is a link, in a writable folder, to the browser in
    # its own folder, so the check must follow the link for both the binary and its folder.
    tools = tmp_path / "bin"
    tools.mkdir()
    (tools / "stat").write_text(_FAKE_STAT)
    (tools / "stat").chmod(0o755)
    folder = tmp_path / "browser"
    folder.mkdir()
    fake = folder / "chrome"
    fake.write_text(_FAKE_CHROMIUM)
    fake.chmod(mode)
    (tools / "alk-chromium").symlink_to(fake)
    folder.chmod(folder_mode)
    env = {
        **os.environ,
        "PATH": f"{tools}:{os.environ['PATH']}",
        "PNG_BYTES": png_bytes,
        "RENDERS": "1" if renders else "0",
        "FILE_OWNER": file_owner,
        "DIR_OWNER": folder_owner,
    }
    try:
        return subprocess.run(
            ["sh", "-c", _browser_check(script)], env=env, capture_output=True
        )
    finally:
        folder.chmod(0o755)


def test_the_browser_check_passes_a_browser_that_runs_the_page_and_screenshots(
    script, tmp_path
):
    result = _run_browser_check(script, tmp_path, mode=0o555, png_bytes=_PNG)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "case",
    [
        # Writable: an agent's user could replace the kit's browser.
        dict(mode=0o755, png_bytes=_PNG),
        # Not runnable by the agent's user.
        dict(mode=0o550, png_bytes=_PNG),
        # Wrote something that is not a PNG.
        dict(mode=0o555, png_bytes="GIF89a"),
        # Printed its own arguments back, and no page.
        dict(mode=0o555, png_bytes=_PNG, renders=False),
        # Owned by a user other than root, who can make it writable again.
        dict(mode=0o555, png_bytes=_PNG, file_owner="svc-agent"),
        # In a folder where a new file can be moved over it: owned by another user, or writable.
        dict(mode=0o555, png_bytes=_PNG, folder_owner="svc-agent"),
        dict(mode=0o555, png_bytes=_PNG, folder_mode=0o755),
    ],
    ids=[
        "writable",
        "not-runnable",
        "not-a-png",
        "no-page",
        "file-owner",
        "folder-owner",
        "folder-writable",
    ],
)
def test_the_browser_check_fails_a_browser_it_should_not_certify(
    script, tmp_path, case
):
    result = _run_browser_check(script, tmp_path, **case)
    assert result.returncode != 0
