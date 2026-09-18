from typing import Any

from fi.alk.harness.backends.vertex_gemini import (
    _flattened,
    _forget_old_reads,
    _successful_terminal_save,
)


def test_successful_authoring_save_is_terminal() -> None:
    assert _successful_terminal_save(
        "mcp__world__save_world", {"content": "Saved to /work/authoring."}
    )
    assert _successful_terminal_save(
        "mcp__provision__save_environment", {"content": "Saved."}
    )
    assert _successful_terminal_save(
        "mcp__scenarios__save_scenarios", {"content": "Saved 10 scenarios."}
    )
    assert _successful_terminal_save(
        "mcp__source_data__finish_review", {"content": "Saved 12 invariants."}
    )


def test_rejected_save_and_non_save_tools_are_not_terminal() -> None:
    assert not _successful_terminal_save(
        "mcp__world__save_world", {"is_error": True, "content": "Not saved."}
    )
    assert not _successful_terminal_save(
        "mcp__world__check_world", {"content": "All checks pass."}
    )
    assert not _successful_terminal_save("mcp__world__save_world", "Saved")


def _read(call_id: str, name: str, text: str) -> Any:
    from google.genai import types

    return types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=call_id, name=name, response={"content": text}
                )
            )
        ],
    )


def _said(part: Any) -> str:
    return _flattened(part.parts[0].function_response.response)


def test_small_sessions_keep_every_read() -> None:
    contents = [
        _read(f"c{n}", "mcp__scenarios__inspect_world", "x" * 100) for n in range(10)
    ]
    _forget_old_reads(contents)
    assert all(_said(one) == "x" * 100 for one in contents)


def test_a_long_session_forgets_all_but_the_newest_reads() -> None:
    contents = [
        _read(f"c{n}", "mcp__scenarios__inspect_world", "x" * 10_000) for n in range(10)
    ]
    _forget_old_reads(contents)
    kept = [one for one in contents if _said(one) == "x" * 10_000]
    assert len(kept) == 2
    assert kept == contents[-2:]
    assert "inspect_world" in _said(contents[0])


def test_forgetting_is_per_tool_and_leaves_writes_alone() -> None:
    contents = [
        _read("c0", "mcp__scenarios__inspect_world", "w" * 30_000),
        _read("c1", "mcp__scenarios__inspect_world", "w" * 30_000),
        _read("c2", "mcp__scenarios__inspect_world", "w" * 30_000),
        _read("c3", "mcp__scenarios__inspect_scenario", "s" * 100),
        _read("c4", "mcp__scenarios__submit_scenario", "gate said no" * 500),
    ]
    _forget_old_reads(contents)
    assert _said(contents[0]).startswith("[dropped")
    assert _said(contents[1]) == "w" * 30_000
    assert _said(contents[3]) == "s" * 100
    assert _said(contents[4]) == "gate said no" * 500


def test_forgetting_twice_changes_nothing_more() -> None:
    contents = [
        _read(f"c{n}", "mcp__scenarios__inspect_world", "x" * 10_000) for n in range(10)
    ]
    _forget_old_reads(contents)
    once = [_said(one) for one in contents]
    _forget_old_reads(contents)
    assert [_said(one) for one in contents] == once
