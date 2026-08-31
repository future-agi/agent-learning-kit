"""Pins the runner return conventions in world-handle-interface.md v3.2's "Return conventions"
block: a check returning ``""``/whitespace counts as held, and a bare ``False`` from ``ready()``
is broken rather than a plain not-ready. The unchanged neighbors are pinned alongside them so a
future edit to either runner cannot drift the other conventions without a test noticing.
"""

from __future__ import annotations

import pytest

from fi.alk.harness.checks import run_check
from fi.alk.harness.folder import _run, check_ready
from fi.alk.harness.scenario import Scenario

# --- checks.py: run_check's return convention -------------------------------------------------


@pytest.mark.parametrize("returning", ["None", "True", "''", "'   '"])
def test_check_returning_nothing_or_whitespace_holds(returning: str) -> None:
    outcome = run_check(f"def check(world, calls):\n    return {returning}\n", None, [])
    assert outcome.held and not outcome.broken and outcome.said == ""


def test_check_returning_a_non_empty_string_does_not_hold() -> None:
    outcome = run_check("def check(world, calls):\n    return 'no rows'\n", None, [])
    assert not outcome.held and not outcome.broken and outcome.said == "no rows"


def test_check_returning_bare_false_does_not_hold_with_reason_false() -> None:
    """An agent result, matching checks.py's existing convention — not a broken check."""
    outcome = run_check("def check(world, calls):\n    return False\n", None, [])
    assert not outcome.held and not outcome.broken and outcome.said == "False"


@pytest.mark.parametrize("returning", ["0", "1", "42", "[]", "{}", "0.0", "object()"])
def test_check_returning_any_other_value_is_broken(returning: str) -> None:
    """Anything that is not the held set, a non-empty string, or bare False is our own mistake,
    not a finding about the agent — a check that returns 0 by accident must not be scored as a
    failing sub-goal with an uninterpretable reason."""
    outcome = run_check(f"def check(world, calls):\n    return {returning}\n", None, [])
    assert not outcome.held and outcome.broken


# --- folder.py: ready()'s return convention ---------------------------------------------------


@pytest.mark.parametrize("returning", ["None", "True", "''", "'   '"])
def test_ready_returning_nothing_or_whitespace_is_ready(returning: str) -> None:
    outcome = _run(f"def ready(world):\n    return {returning}\n", "s/ready.py", "ready", None)
    assert outcome.ok and not outcome.broken and outcome.said == ""


def test_ready_returning_a_non_empty_string_is_not_ready() -> None:
    outcome = _run(
        "def ready(world):\n    return 'orders pending'\n", "s/ready.py", "ready", None
    )
    assert not outcome.ok and not outcome.broken and outcome.said == "orders pending"


def test_ready_returning_bare_false_is_broken_not_advisory() -> None:
    """The behavioral change: ready() cannot name what it wants, so a bare False is our own
    mistake, not a precondition failing on the shared sealed baseline."""
    outcome = _run("def ready(world):\n    return False\n", "s/ready.py", "ready", None)
    assert not outcome.ok and outcome.broken


def test_setup_returning_bare_false_stays_advisory_not_broken() -> None:
    """The change is scoped to ready() only; setup()'s own convention is untouched."""
    outcome = _run("def setup(world):\n    return False\n", "s/setup.py", "setup", None)
    assert not outcome.ok and not outcome.broken


@pytest.mark.parametrize("returning", ["0", "1", "[]", "{}", "0.0"])
def test_ready_returning_any_other_value_is_broken(returning: str) -> None:
    """ready() cannot turn a non-string, non-False value into a precondition sentence either, so
    it gets the same broken verdict bare False does."""
    outcome = _run(f"def ready(world):\n    return {returning}\n", "s/ready.py", "ready", None)
    assert not outcome.ok and outcome.broken


@pytest.mark.parametrize("returning", ["0", "1", "[]", "{}", "0.0"])
def test_setup_returning_any_other_value_stays_advisory(returning: str) -> None:
    """The widened rule is keyed on entry == "ready", not on the value alone — setup() keeps its
    own untouched convention for every one of these values too."""
    outcome = _run(f"def setup(world):\n    return {returning}\n", "s/setup.py", "setup", None)
    assert not outcome.broken and not outcome.ok


def test_check_ready_reports_bare_false_as_broken() -> None:
    """Pins the production entry point, not just `_run`: `check_ready` is the only caller that
    can ever produce entry == "ready", so a test that only calls `_run` directly would not catch
    a rename or rewiring that silently reverted this."""
    outcome = check_ready(
        Scenario(name="s", ready_code="def ready(world):\n    return False\n"), None
    )
    assert not outcome.ok and outcome.broken


# --- a displayed label has to keep the part that identifies what it points at ------------------


def test_a_shortened_path_keeps_the_end_that_names_the_file():
    """Cutting the end off a path keeps what every path shares and discards what distinguishes it.
    Every skill file under one stage shares a prefix well past the limit, so a run recorded three
    reads of `.../skills/write-scenar...` and could not say whether the model opened the skill body
    or one of its references -- which is the question of whether the reference catalogue is used at
    all, left unanswerable by a display format."""
    from fi.alk.harness.session import _elided

    prefix = "/opt/alk-venv/lib/python3.12/site-packages/fi/alk/harness/"
    shown = {
        _elided(prefix + name)
        for name in (
            "skills/write-scenarios/SKILL.md",
            "skills/write-scenarios/references/_authoring-code.md",
            "skills/write-scenarios/references/chat.md",
        )
    }
    assert len(shown) == 3, f"two paths render identically: {shown}"
    assert all(len(one) <= 80 for one in shown)
    assert any(one.endswith("_authoring-code.md") for one in shown)


def test_a_short_path_is_left_alone():
    from fi.alk.harness.session import _elided

    assert _elided("world/tools.py") == "world/tools.py"


def test_a_long_value_that_is_not_a_path_still_truncates_at_the_end():
    """Only paths carry their identity at the tail. A prose label reads from the front."""
    from fi.alk.harness.session import _elided

    shortened = _elided("word " * 40)
    assert len(shortened) == 80
    assert shortened.startswith("word word")
    assert shortened.endswith("...")


# --- the runner reference is untested guidance, so its claims are checked here -----------------
#
# No runner has ever been written against `_writing-a-runner.md`. Its sufficiency IS the
# acceptance test for "adding an agent type is a skill file, not a code change", and every symbol
# it names lives in a different module that can be renamed without anyone opening it.


def _runner_reference() -> str:
    from fi.alk.harness.config import SKILLS_ROOT

    return (
        SKILLS_ROOT / "build-environment" / "references" / "_writing-a-runner.md"
    ).read_text(encoding="utf-8")


def test_the_runner_reference_invents_no_outcome_field():
    """Every keyword the example passes to CallOutcome must be a real field. Not checked against a
    list written here, which would only ever confirm the names it already knew: the keywords are
    read out of the example itself, so a field renamed in the document is caught rather than
    silently skipped."""
    import dataclasses
    import re

    from fi.alk.harness.hosted_scheduler import CallOutcome

    body = _runner_reference()
    literal = body[body.index("return CallOutcome(") : body.index("        )", body.index("return CallOutcome("))]
    passed = set(re.findall(r"^\s*(\w+)=", literal, re.MULTILINE))
    assert passed, "the example stopped constructing a CallOutcome"
    real = {f.name for f in dataclasses.fields(CallOutcome)}
    assert not passed - real, f"the example passes fields that do not exist: {sorted(passed - real)}"


def test_the_runner_reference_omits_no_context_field():
    """The inverse direction, which is the one that actually bit: the document listed five of
    CallRunnerContext's seven fields, and the two it left out were `evidence_seam` -- which
    decides whether tool calls can be observed at all -- and `source_directory`. A guard that only
    checks that named things exist cannot see an omission; this is what does."""
    import dataclasses

    from fi.alk.harness.call_runner import CallRunnerContext

    body = _runner_reference()
    missing = [
        field.name
        for field in dataclasses.fields(CallRunnerContext)
        if field.name not in body
    ]
    assert not missing, f"the reference never mentions: {missing}"


def test_the_runner_reference_describes_the_upload_it_depends_on():
    """The example's digests can only come from `upload_artifact`, and the document used to end
    at `transcript_artifact="sha256:..."` without saying so. An author following it had two ways
    out: fabricate a digest, or return None and hit the evidence gate after a run was paid for."""
    import inspect

    from fi.alk.harness.hosted_entrypoint import OutboundAdapter

    body = _runner_reference()
    assert "upload_artifact" in body
    signature = inspect.signature(OutboundAdapter.upload_artifact)
    for parameter in ("kind", "scenario_key"):
        assert parameter in signature.parameters
        assert parameter in body, f"the example no longer passes {parameter}"
    # It returns None on refusal rather than raising, which is what shapes the calling code.
    assert "returns None" in body or "returns `None`" in body
    assert str(signature.return_annotation).endswith("str | None")


def test_the_runner_reference_points_at_something_that_exists():
    """It defers to call_runner rather than restating the seam rules. A pointer at a renamed
    symbol is worse than no pointer: it reads as authoritative and leads nowhere."""
    import inspect

    from fi.alk.harness import call_runner

    body = _runner_reference()
    assert "evidence_seam" in body and "http_tool" in body
    for symbol in ("_collect_http_tool_calls",):
        assert symbol in body
        assert hasattr(call_runner, symbol), f"the reference points at a missing {symbol}"
    assert "evidence_seam" in (inspect.getdoc(call_runner) or "")
