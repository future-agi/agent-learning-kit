"""What a stage may do is what it was given, and that has to be enforced where the SDK asks.

None of this was covered when the deny-by-default regime was deleted, which is why deleting it
left the suite green. The rule it enforces is not "these tools are dangerous" but "a session is
offered whatever its host happens to expose, and anything the harness did not grant is not part
of how this stage works".
"""

import asyncio

import pytest

from fi.alk.harness.backends.base import SessionSpec
from fi.alk.harness.backends.claude import ClaudeBackend
from fi.alk.harness.config import UNWANTED, gate_hooks, permission_gate


def _decide(granted, tool, ask=None, payload=None):
    return asyncio.run(permission_gate(ask, granted)(tool, payload or {}, None))


def _hook(granted, tool):
    hooks = gate_hooks(granted)["PreToolUse"][0].hooks[0]
    return asyncio.run(hooks({"tool_name": tool}, None, None))


def test_a_stage_may_not_use_a_tool_it_was_not_granted():
    """The regression this pins: an allow-everything callback is not a neutral default. The SDK
    consults the callback precisely for the tools that were NOT allowlisted, so allowing by
    default there approves the host extras the harness never offered, which is the one case the
    gate exists for."""
    from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

    assert isinstance(_decide(["Read"], "Read"), PermissionResultAllow)
    refused = _decide(["Read"], "Bash")
    assert isinstance(refused, PermissionResultDeny)
    assert "not part of this stage" in refused.message
    # It says what the stage does have, so the model redirects instead of hunting for a way in.
    assert "Read" in refused.message


def test_the_callback_alone_cannot_enforce_this_so_a_hook_does():
    """Asserted against the SDK's own shadowing rule rather than our reading of it: every tool we
    grant auto-approves before the callback runs. A gate built only on the callback is therefore
    never consulted for the tools it was meant to govern."""
    from claude_agent_sdk.types import _get_can_use_tool_shadowed_warning

    warning = _get_can_use_tool_shadowed_warning("default", ["Read", "Glob"])
    assert warning and "will not be invoked for: Read, Glob" in warning

    assert _hook(["Read"], "Read") == {}
    denied = _hook(["Read"], "Bash")["hookSpecificOutput"]
    assert denied["permissionDecision"] == "deny"
    assert "not part of this stage" in denied["permissionDecisionReason"]
    # The question route is never denied: it is how a stage reaches a human.
    assert _hook(["Read"], "AskUserQuestion") == {}


def test_the_operator_question_still_reaches_whoever_is_running_it():
    async def ask(tool_name, payload, context):
        return f"asked about {tool_name}"

    assert _decide([], "AskUserQuestion", ask=ask) == "asked about AskUserQuestion"


@pytest.mark.parametrize(
    "granted,expected",
    [
        (("AskUserQuestion",), True),
        (("AskUserQuestion", "Read", "Glob", "Grep", "Write", "Edit", "Bash"), False),
    ],
)
def test_a_stage_that_asked_for_a_shell_keeps_it(granted, expected):
    """The list of host tools kept out of the model's view is filtered against the grant, so a
    stage that was given Bash still sees Bash. A blanket denial would silently outrank the
    stage's own tool list, which is how withholding turns into a stage that cannot finish."""
    spec = SessionSpec(system_prompt="x", builtins=granted)
    options = ClaudeBackend().create(spec)._options
    assert ("Bash" in (options.disallowed_tools or [])) is expected
    assert set(options.disallowed_tools or []).isdisjoint(granted)


def test_the_backend_wires_the_hook_the_callback_and_the_hidden_list():
    """All three, because each covers what the others cannot: the hook runs on every call, the
    callback carries the operator question, and the hidden list saves the turn a denial costs."""
    spec = SessionSpec(system_prompt="x", builtins=("AskUserQuestion", "Read"))
    options = ClaudeBackend().create(spec)._options
    assert options.permission_mode == "default"
    assert options.hooks and "PreToolUse" in options.hooks
    assert options.can_use_tool is not None
    assert "WebSearch" in options.disallowed_tools
    assert set(UNWANTED) - set(options.disallowed_tools) == set()


def test_an_ungated_stage_is_left_alone():
    """The simulated customer has no tools and runs bare; gating it would change its behaviour
    for no gain."""
    options = ClaudeBackend().create(SessionSpec(system_prompt="x", gated=False))._options
    assert options.can_use_tool is None
    assert not options.hooks
