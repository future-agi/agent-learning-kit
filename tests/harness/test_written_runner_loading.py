"""Loading a runner a model wrote.

Both cases here are invisible to a single-bundle, single-world test and fire exactly where the
feature is meant to earn its keep: generated code, and more than one world in a job.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fi.alk.harness import transports


def _bundle(tmp_path: Path, name: str, body: str, module: str = "runner") -> Path:
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{module}.py").write_text(body, encoding="utf-8")
    return root


GOOD = """
class R:
    def __init__(self, adapter, context):
        self.adapter = adapter
    async def run(self, scenario, runtime, *, world=None):
        return "{tag}"
"""


# --- defect 1: a module-level error must be a declaration problem, not a crash ------------------


def test_a_syntax_error_in_a_written_runner_is_reported_as_a_declaration_problem(
    tmp_path,
):
    root = _bundle(tmp_path, "b", "class R:\n  def __init__(self, a, c)\n    pass\n")
    with pytest.raises(transports.TransportUnresolved) as raised:
        transports._load_written_runner("runner:R", root)
    message = str(raised.value)
    assert "failed while loading" in message
    assert "SyntaxError" in message  # names the error type
    assert "runner.py" in message  # and the file to fix


def test_an_exception_in_module_level_code_is_reported_not_raised_raw(tmp_path):
    root = _bundle(
        tmp_path, "b", "raise RuntimeError('no credentials at import time')\n"
    )
    with pytest.raises(transports.TransportUnresolved) as raised:
        transports._load_written_runner("runner:R", root)
    assert "RuntimeError" in str(raised.value)
    assert "no credentials at import time" in str(raised.value)


def test_a_missing_third_party_import_is_reported(tmp_path):
    root = _bundle(tmp_path, "b", "import a_package_that_is_not_installed_anywhere\n")
    with pytest.raises(transports.TransportUnresolved):
        transports._load_written_runner("runner:R", root)


def test_a_broken_module_is_not_left_in_the_cache(tmp_path):
    root = _bundle(tmp_path, "b", "raise ValueError('boom')\n")
    with pytest.raises(transports.TransportUnresolved):
        transports._load_written_runner("runner:R", root)
    leaked = [
        n for n in sys.modules if n.startswith("_alk_runner_") and n.endswith(".runner")
    ]
    assert leaked == []


def test_a_missing_attribute_still_names_what_was_wanted(tmp_path):
    root = _bundle(tmp_path, "b", GOOD.format(tag="x"))
    with pytest.raises(transports.TransportUnresolved) as raised:
        transports._load_written_runner("runner:Missing", root)
    assert "Missing" in str(raised.value)


# --- defect 2: one bundle must not be served another bundle's runner ---------------------------


def test_two_bundles_using_the_same_module_name_load_different_runners(tmp_path):
    # The case that cannot occur in a one-bundle test and is certain once a job has two worlds:
    # a skill teaches one conventional module name, so every bundle uses it.
    first = _bundle(tmp_path, "world0", GOOD.format(tag="from-world-0"))
    second = _bundle(tmp_path, "world1", GOOD.format(tag="from-world-1"))

    import asyncio

    a = transports._load_written_runner("runner:R", first)
    b = transports._load_written_runner("runner:R", second)

    assert asyncio.run(a("ad", "ctx").run(None, None)) == "from-world-0"
    assert asyncio.run(b("ad", "ctx").run(None, None)) == "from-world-1"
    assert a is not b


def test_the_same_bundle_twice_is_consistent(tmp_path):
    root = _bundle(tmp_path, "w", GOOD.format(tag="stable"))
    first = transports._load_written_runner("runner:R", root)
    second = transports._load_written_runner("runner:R", root)
    import asyncio

    assert asyncio.run(second("a", "c").run(None, None)) == "stable"
    assert first.__name__ == second.__name__


def test_the_bundle_does_not_stay_on_sys_path(tmp_path):
    # Left behind, one bundle shadows the next bundle's imports for the rest of the process.
    root = _bundle(tmp_path, "w", GOOD.format(tag="x"))
    before = list(sys.path)
    transports._load_written_runner("runner:R", root)
    assert str(root) not in sys.path
    assert sys.path == before


def test_sys_path_is_restored_even_when_the_module_fails(tmp_path):
    root = _bundle(tmp_path, "w", "raise RuntimeError('boom')\n")
    before = list(sys.path)
    with pytest.raises(transports.TransportUnresolved):
        transports._load_written_runner("runner:R", root)
    assert sys.path == before


def test_a_runner_may_import_its_own_siblings(tmp_path):
    root = tmp_path / "w"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "helper.py").write_text("TAG = 'via-sibling'\n", encoding="utf-8")
    (root / "entry.py").write_text(
        "from pkg.helper import TAG\n"
        "class R:\n"
        "    def __init__(self, adapter, context): pass\n"
        "    async def run(self, scenario, runtime, *, world=None): return TAG\n",
        encoding="utf-8",
    )
    import asyncio

    made = transports._load_written_runner("entry:R", root)
    assert asyncio.run(made("a", "c").run(None, None)) == "via-sibling"


def test_a_dotted_module_name_resolves_inside_the_bundle(tmp_path):
    root = tmp_path / "w"
    (root / "runners").mkdir(parents=True)
    (root / "runners" / "__init__.py").write_text("", encoding="utf-8")
    (root / "runners" / "voice.py").write_text(
        GOOD.format(tag="dotted"), encoding="utf-8"
    )
    import asyncio

    made = transports._load_written_runner("runners.voice:R", root)
    assert asyncio.run(made("a", "c").run(None, None)) == "dotted"


def test_a_malformed_spec_says_what_shape_is_wanted(tmp_path):
    for bad in ("runner", "runner:", ":R"):
        with pytest.raises(transports.TransportUnresolved) as raised:
            transports._load_written_runner(bad, tmp_path)
        assert "module:Attribute" in str(raised.value)
