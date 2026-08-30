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


# --- defect 3: a written runner inherited no evidence contract at all --------------------------


def _declared(tmp_path: Path, declaration: dict) -> Path:
    import json

    root = _bundle(tmp_path, "declared", GOOD.replace("{tag}", "x"))
    (root / "transport.json").write_text(json.dumps(declaration), encoding="utf-8")
    return root


def _resolve(root: Path):
    from fi.alk.harness.hosted_entrypoint import _register_builtin_transports

    _register_builtin_transports()
    return transports.resolve(
        transports.Evidence(connector="livekit", modality="voice", bundle_dir=root)
    )


def test_a_written_runner_for_a_known_transport_owes_what_that_transport_owes(tmp_path):
    """The evidence gate exists because the build stage can write its own runner, so a written
    runner is the only thing it was built to police. Declaring a transport ALK implements and
    omitting `requires` is what the skill tells authors to do, and it left the gate holding an
    empty tuple: a six-turn voice call with no transcript and no audio passed clean."""
    from fi.alk.harness.hosted_scheduler import CallOutcome, call_evidence_faults

    inherited = _resolve(_declared(tmp_path, {"transport": "livekit", "runner": "runner:R"})).requires
    assert inherited == transports._REGISTRY["livekit"].requires
    assert "transcript" in inherited and "recordings" in inherited

    silent_voice_call = CallOutcome(
        calls=[],
        turns=6,
        started_at=None,
        ended_at=None,
        duration_ms=0,
        transcript_artifact=None,
        recording_artifacts=[],
    )
    faults = call_evidence_faults(silent_voice_call, inherited)
    assert faults, "a voice call with no transcript and no audio must not pass"
    assert any("transcript" in fault for fault in faults)
    assert any("recording" in fault for fault in faults)


def test_writing_your_own_runner_does_not_change_what_the_transport_owes(tmp_path):
    """Same transport, same platform, same rendering. Who wrote the runner is not a reason for a
    voice call to owe less evidence than one the built-in runner produced."""
    written = _resolve(_declared(tmp_path, {"transport": "livekit", "runner": "runner:R"}))
    builtin = _resolve(_declared(tmp_path, {"transport": "livekit"}))
    assert written.requires == builtin.requires
    assert written.key == builtin.key == "livekit"


def test_a_declared_requires_still_wins_over_the_inherited_default(tmp_path):
    """"Declare it and you are held to exactly that" has to keep meaning that, or inheritance
    would quietly become a floor nobody can go under."""
    from fi.alk.harness.hosted_entrypoint import _transport_requires

    root = _declared(
        tmp_path, {"transport": "livekit", "runner": "runner:R", "requires": ["turns"]}
    )
    assert _transport_requires(context=_context(root)) == ("turns",)


def test_an_explicitly_empty_requires_is_a_promise_not_an_absence(tmp_path):
    """`"requires": []` is an author saying this runner owes nothing. Treating it as "unset" and
    inheriting the default would override a stated intention with a guess, which is the same
    conflation as the defect itself, pointing the other way."""
    from fi.alk.harness.hosted_entrypoint import _transport_requires

    root = _declared(
        tmp_path, {"transport": "livekit", "runner": "runner:R", "requires": []}
    )
    assert _transport_requires(context=_context(root)) == ()


def test_a_novel_transport_still_resolves_but_says_nothing_will_be_checked(tmp_path, caplog):
    """A transport ALK has never heard of must keep working: that genericity is the point. But
    there is nothing to inherit, so the run says so rather than reporting a clean gate."""
    import logging

    with caplog.at_level(logging.WARNING, logger=transports.__name__):
        resolved = _resolve(
            _declared(tmp_path, {"transport": "whatsapp_business", "runner": "runner:R"})
        )
    assert resolved.key == "whatsapp_business"
    assert resolved.requires == ()
    assert "nothing its runner returns will be checked" in caplog.text
    assert "whatsapp_business" in caplog.text


def test_a_novel_transport_that_declares_requires_is_not_warned_about(tmp_path, caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger=transports.__name__):
        _resolve(
            _declared(
                tmp_path,
                {
                    "transport": "whatsapp_business",
                    "runner": "runner:R",
                    "requires": ["turns", "transcript"],
                },
            )
        )
    assert caplog.text == ""


def _context(bundle_dir: Path):
    """The narrow slice of CallRunnerContext that _transport_requires actually reads."""
    from types import SimpleNamespace

    return SimpleNamespace(
        job=SimpleNamespace(agent=SimpleNamespace(connector="livekit")),
        bundle_dir=bundle_dir,
    )


def test_an_unresolvable_transport_says_nothing_will_be_checked(tmp_path, caplog):
    """Reached whenever the caller injects its own call runner, which is the normal path for an
    embedder and for most of the entrypoint's own tests: building a runner is what would
    otherwise have resolved this first, and nothing enforces that ordering. An empty tuple is
    then the only truthful answer, since there is no declaration to hold anyone to, but it is
    indistinguishable from a runner that genuinely owes nothing. So it is announced."""
    import logging

    from fi.alk.harness import hosted_entrypoint as he

    root = _declared(tmp_path, {"transport": "nothing_implements_this"})
    with caplog.at_level(logging.WARNING, logger=he.__name__):
        assert he._transport_requires(context=_context(root)) == ()
    assert "nothing a call returns will be checked" in caplog.text


def test_an_unreadable_declaration_is_not_the_same_as_no_declaration(tmp_path, caplog):
    """`is_file` already separates "nothing declared" from this, so reaching the parse failure
    means a declaration exists and could not be read. Silently returning {} makes it read as the
    first: the runner the build stage wrote is ignored, resolution falls through to recognition or
    fails naming no declaration, and the evidence contract goes with it."""
    import logging

    root = _bundle(tmp_path, "broken", GOOD.replace("{tag}", "x"))
    (root / "transport.json").write_text('{"transport": "livekit",', encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=transports.__name__):
        assert transports.declared(root) == {}
    assert "could not be read" in caplog.text
    assert "declared nothing" in caplog.text


def test_a_declaration_that_is_not_an_object_says_so(tmp_path, caplog):
    import logging

    root = _bundle(tmp_path, "listy", GOOD.replace("{tag}", "x"))
    (root / "transport.json").write_text('["livekit"]', encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=transports.__name__):
        assert transports.declared(root) == {}
    assert "not an object" in caplog.text


def test_a_missing_declaration_is_silent(tmp_path, caplog):
    """The ordinary case. A warning that fires when nothing is wrong is one people learn to
    ignore, which would cost the two above their meaning."""
    import logging

    with caplog.at_level(logging.WARNING, logger=transports.__name__):
        assert transports.declared(_bundle(tmp_path, "bare", GOOD)) == {}
    assert caplog.text == ""
