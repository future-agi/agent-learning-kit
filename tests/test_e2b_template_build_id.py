"""The build id a reading records comes from the image the kit runs in, never from the checkout
that publishes a template over some other image (TH-10400)."""

from __future__ import annotations

import importlib.util
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
