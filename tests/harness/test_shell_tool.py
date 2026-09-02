"""The shell a backend without a host CLI offers under the name Claude Code uses.

It exists so a skill instruction means the same thing on every backend. The cases worth pinning
are the ones where a difference would be invisible: a command that waits for input, one that runs
forever, one that fails, and one that floods the stage with output.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fi.alk.harness.backends.base import FILE_TOOLS, HOST_TOOLS
from fi.alk.harness.backends.shell import MAX_OUTPUT_CHARS, shell_tools


def run(cwd, command: str) -> dict:
    tool = shell_tools(str(cwd))[0]
    return asyncio.run(tool.handler({"command": command}))


def test_it_is_named_and_shaped_like_the_one_it_stands_in_for():
    tool = shell_tools(None)[0]
    assert tool.name == "Bash"
    assert tool.input_schema["required"] == ["command"]
    assert "Bash" in HOST_TOOLS and "Bash" not in FILE_TOOLS


def test_it_runs_in_the_session_directory(tmp_path: Path):
    (tmp_path / "marker.txt").write_text("here")
    assert "marker.txt" in run(tmp_path, "ls")["content"][0]["text"]


def test_a_failure_reports_its_exit_code_rather_than_looking_like_success(tmp_path: Path):
    said = run(tmp_path, "ls /definitely-not-here")["content"][0]["text"]
    assert said.startswith("exit ")


def test_an_empty_command_is_refused(tmp_path: Path):
    assert run(tmp_path, "   ").get("is_error")


def test_nothing_is_read_from_standard_input(tmp_path: Path):
    """A command that asks a question has to fail saying so, not hold the stage's turn."""
    said = run(tmp_path, "cat")["content"][0]["text"]
    assert "no output" in said or said == ""


def test_output_is_clipped_rather_than_flooding_the_stage(tmp_path: Path):
    said = run(tmp_path, "for i in $(seq 1 40000); do echo aaaaaaaaaaaaaaaaaaaaaaaaa; done")
    text = said["content"][0]["text"]
    assert len(text) <= MAX_OUTPUT_CHARS + 200
    assert "characters omitted" in text
