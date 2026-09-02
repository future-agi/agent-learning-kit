"""A shell for backends that have no host CLI behind them.

Claude Code brings its own Bash. Any other loop granted that name gets this one: same name, same
single ``command`` argument, so a stage's skill text means the same thing whichever backend is
running it. Without it the default backend reads an agent's source and the other one reads it
*and* runs its tests, and the suites they write differ for a reason nobody can see.

Reading a repository tells you what an agent is supposed to do. Running it tells you what it
does. The scenarios worth writing come from the gap, so the stage that writes them is trusted
with both, and the boundary is the sandbox it runs in rather than the tool list.

Writing is still not offered here, and not by omission. The harness's own artifacts go through
tools that validate them, and a stage able to edit the agent under test could make its scenarios
pass by changing the agent rather than by writing a better scenario.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from .base import ToolSpec

# Long enough for an agent's own test suite, short enough that a command waiting on input fails
# rather than holding the stage until its turn budget runs out.
TIMEOUT_SECONDS = 120
MAX_OUTPUT_CHARS = 30_000


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _error(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def _clipped(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    half = MAX_OUTPUT_CHARS // 2
    dropped = len(text) - MAX_OUTPUT_CHARS
    return f"{text[:half]}\n\n... {dropped} characters omitted ...\n\n{text[-half:]}"


def _environment() -> dict[str, str]:
    """The command's environment, with this process's own interpreter first on the path.

    A stage reaching for the shell in this harness almost always wants to look at the world, and
    the world's libraries are installed where the harness runs, not wherever a bare ``python``
    resolves to. Observed on a live run: the stage wrote ``python -c "import psycopg"`` to inspect
    the seeded database and lost the turn to a missing module that was installed all along.
    """
    env = dict(os.environ)
    here = str(Path(sys.executable).parent)
    existing = env.get("PATH", "")
    if here not in existing.split(os.pathsep):
        env["PATH"] = f"{here}{os.pathsep}{existing}" if existing else here
    return env


def shell_tools(cwd: str | None) -> list[ToolSpec]:
    """A single ``Bash`` tool, rooted at the session's working directory."""
    base = Path(cwd) if cwd else Path.cwd()
    env = _environment()

    async def run(args: dict) -> dict:
        command = str(args.get("command") or "").strip()
        if not command:
            return _error("Say what to run.")
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=str(base),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                # No stdin. A command that asks a question should fail saying so, rather than
                # wait for an answer that is never coming and take the stage's turn with it.
                stdin=asyncio.subprocess.DEVNULL,
            )
        except OSError as broke:
            return _error(f"could not start: {broke}")
        try:
            out, _ = await asyncio.wait_for(process.communicate(), timeout=TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return _error(
                f"timed out after {TIMEOUT_SECONDS}s and was killed. Anything that waits for "
                "input or runs a server has to be run some other way."
            )
        said = _clipped(out.decode("utf-8", "replace"))
        if process.returncode:
            return _ok(f"exit {process.returncode}\n{said}")
        return _ok(said or "(no output)")

    return [
        ToolSpec(
            name="Bash",
            description=(
                "Run a shell command in the working directory. Use it to see what the agent "
                "actually does: run its tests, check what a script prints, look at what its "
                "own tooling reports. Nothing is read from standard input and a command is "
                f"killed after {TIMEOUT_SECONDS} seconds."
            ),
            input_schema={
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
            handler=run,
        )
    ]
