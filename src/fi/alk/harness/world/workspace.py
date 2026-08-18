"""Standing the environment up in containers, with the harness deciding what that means.

The harness has read the agent's repository, so it knows what running that agent's code takes:
which base image, which install command, which store, which services. Encoding any of that here
would be guessing on behalf of an agent nobody has seen yet, and would be wrong for the next one.

So this provides two things and no opinions:

- a place to write files, under the session's own ``env`` directory
- a way to run container commands from there, and read back what happened

Everything else, the Dockerfile, the compose file, the schema, the entrypoint, is written by
whoever read the repository. What is enforced is only what keeps this safe to run on somebody's
machine: files stay inside the environment directory, and the only commands that run are container
commands.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ENV = "env"

# Only these. Not a general shell: a tool that can run anything is a tool with no guardrail, and
# the whole point of routing through here is that what happens is inspectable and bounded.
ALLOWED = ("docker", "docker-compose")

# Long enough for an image build that downloads a base layer, short enough that a hung build is
# reported rather than waited on forever.
PATIENCE = 900


def env_root(destination: Path) -> Path:
    """Where this agent's environment definition lives, beside its world."""
    root = Path(destination) / ENV
    root.mkdir(parents=True, exist_ok=True)
    return root


def inside(destination: Path, path: str) -> Path:
    """The full path for a file the harness wants to write, refused if it escapes.

    A path arrives as text from a model, so it is resolved and then checked rather than trusted.
    Writing outside the environment directory would mean the harness could touch anything on the
    machine it happens to be running on, which is not a thing to leave to a prompt.
    """
    root = env_root(destination).resolve()
    asked = (root / str(path).lstrip("/")).resolve()
    if not asked.is_relative_to(root):
        raise ValueError(
            f"{path!r} is outside the environment directory. Everything the environment needs "
            "lives under env/, so that building it cannot reach the rest of the machine."
        )
    return asked


def write(destination: Path, path: str, contents: str) -> Path:
    """Put one file into the environment definition."""
    target = inside(destination, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(contents, encoding="utf-8")
    return target


def listing(destination: Path) -> list[str]:
    root = env_root(destination)
    return sorted(
        str(found.relative_to(root)) for found in root.rglob("*") if found.is_file()
    )


def available() -> str:
    """Why containers cannot be used here, or an empty string when they can."""
    if not shutil.which("docker"):
        return "docker is not installed, or not on the path"
    done = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if done.returncode != 0:
        return f"docker is installed but not running: {(done.stderr or '').strip()[:200]}"
    return ""


def run(
    destination: Path,
    command: str,
    *,
    patience: int = PATIENCE,
    extra: dict[str, str] | None = None,
) -> tuple[int, str]:
    """Run one container command from the environment directory.

    Returns the exit code and the output, both streams together, because a build failure explains
    itself across the two and reading only one is how the actual cause gets lost.

    ``extra`` carries where the store already is, so the agent's own migration command can be
    pointed at it. Without that the only way to run a migration is to bring up a second database
    to run it against, which is a copy of the thing the harness is already holding.
    """
    words = _named_project(command.split(), destination)
    if not words:
        return 1, "no command given"
    if words[0] not in ALLOWED:
        return 1, (
            f"{words[0]!r} is not something this can run. Only {' and '.join(ALLOWED)} commands, "
            "because a general shell here would be a guardrail with nothing behind it. Everything "
            "the environment needs should be in a file it builds from, not in a command."
        )
    blocked = available()
    if blocked:
        return 1, blocked
    try:
        done = subprocess.run(
            words,
            cwd=str(env_root(destination)),
            capture_output=True,
            text=True,
            timeout=patience,
            env={**os.environ, **(extra or {})},
        )
    except subprocess.TimeoutExpired:
        return 1, (
            f"gave up after {patience}s. An install that takes this long usually means a "
            "dependency is being fetched that is not going to arrive; check what the last step "
            "was trying to reach."
        )
    output = ((done.stdout or "") + (done.stderr or "")).strip()
    return done.returncode, output


# Directories whose contents change as a side effect of running anything, and say nothing about
# whether the agent's own source was touched.
NOISE = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", ".mypy_cache"}


def _fingerprint(root: Path) -> dict[str, tuple[int, int]]:
    """What the agent's repository looks like right now, cheaply.

    Size and modification time rather than content: this runs before and after every setup
    command, and hashing a repository twice per migration would cost more than the migration.
    Anything that writes to a file moves both.
    """
    out: dict[str, tuple[int, int]] = {}
    for found in root.rglob("*"):
        if not found.is_file() or NOISE & set(found.parts):
            continue
        try:
            stat = found.stat()
        except OSError:
            continue
        out[str(found.relative_to(root))] = (stat.st_size, stat.st_mtime_ns)
    return out


def run_setup(
    source_root: Path | str,
    command: str,
    *,
    patience: int = PATIENCE,
    extra: dict[str, str] | None = None,
) -> tuple[int, str]:
    """Run the agent's own setup command, in the agent's own directory.

    This exists because only the agent's own tooling can produce the agent's own schema. Alembic,
    Django's migrate, Prisma -- none of them are a SQL file that could be applied through the
    store, and a schema transcribed by hand instead is a guess that every check written against
    it inherits. Watching that happen is what this is for: pointed at an agent with alembic
    migrations, the build stage tried sixteen times to run them through a container, gave up, and
    wrote the tables itself. They were nearly right, which is the worst kind of wrong.

    Wider than ``run``, deliberately, and the guarantee that the agent is never modified is kept
    by checking rather than by there being no way: the repository is fingerprinted before and
    after, and a command that changed anything is reported as having done so. Running a migration
    does not write to the source; something that does is worth stopping for.
    """
    root = Path(source_root)
    if not root.is_dir():
        return 1, f"no agent source at {root}"
    words = command.split()
    if not words:
        return 1, "no command given"

    before = _fingerprint(root)
    try:
        done = subprocess.run(  # nosec B603 — list args, never shell=True
            words,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=patience,
            env=_with_tools_on_path(extra, root),
        )
    except FileNotFoundError:
        return 1, (
            f"{words[0]!r} is not installed where the harness runs. The agent's own tooling has "
            "to be reachable to run its migrations; install it, or run it inside a container "
            "with run_env_command."
        )
    except subprocess.TimeoutExpired:
        return 1, f"gave up after {patience}s"

    output = ((done.stdout or "") + (done.stderr or "")).strip()
    changed = sorted(
        name for name, mark in _fingerprint(root).items() if before.get(name) != mark
    )
    gone = sorted(name for name in before if name not in _fingerprint(root))
    if changed or gone:
        listed = ", ".join((changed + gone)[:6])
        return 1, (
            f"REFUSED: that command modified the agent's own repository ({listed}). The agent "
            "under test is never edited -- what it ships is what is measured. Run something that "
            "only touches the store.\n" + output
        )
    return done.returncode, output


def _with_tools_on_path(extra: dict[str, str] | None, root: Path) -> dict[str, str]:
    """The environment for a setup command, with the tooling beside this interpreter reachable.

    A migration tool is installed into an environment, not onto the system, so ``alembic`` is a
    file next to the running ``python`` rather than something on PATH. Without this the command
    fails as "not installed" when it is installed, just not where a bare PATH looks.
    """
    import sys

    environment = {**os.environ, **(extra or {})}
    beside = str(Path(sys.executable).parent)
    environment["PATH"] = beside + os.pathsep + environment.get("PATH", "")
    # And the agent's own code importable by its own tooling: an alembic env.py imports the
    # models it migrates, so a migration run from anywhere but an installed checkout fails on
    # its first import rather than on anything to do with the database.
    environment["PYTHONPATH"] = str(root) + os.pathsep + environment.get("PYTHONPATH", "")
    return environment


# Compose names its containers after the directory it ran in, and puts no label of ours on them.
# So a run that is killed between "up" and "down" leaves a database running that nothing can
# find again: not by name, because the name is generic, and not by label, because there is none.
PROJECT = "alk-env"


def _named_project(words: list[str], destination: Path) -> list[str]:
    """A compose command, made findable afterwards.

    Given a project name derived from the session, every container it starts is called
    ``alk-env-<session>-<service>-1``, which is enough for ``strays`` to list them and for a
    person to remove them. Left alone if the command already names one.
    """
    if len(words) < 2 or words[0] != "docker" or words[1] != "compose":
        return words
    if any(one in ("-p", "--project-name") for one in words):
        return words
    session = Path(destination).name or "world"
    return [*words[:2], "-p", f"{PROJECT}-{session}", *words[2:]]


def strays() -> list[str]:
    """Containers a killed run left behind, both kinds.

    The store's own are labelled; compose's are found by the project name given above.
    """
    from .stores.container import docker

    listed = docker(
        "ps", "--format",
        '{{.Names}}\t{{.Label "com.docker.compose.project"}}\t{{.Label "alk.harness.agent"}}',
        check=False,
    )
    out = []
    for line in listed.splitlines():
        parts = line.split("\t")
        name = parts[0].strip()
        project = parts[1] if len(parts) > 1 else ""
        agent = parts[2] if len(parts) > 2 else ""
        # Three kinds leak: compose's, which carry a project name we set; the agent's own
        # container, which carries our label; and the store's, which container.strays finds.
        if project.startswith(PROJECT) or agent:
            out.append(name)
    return out
