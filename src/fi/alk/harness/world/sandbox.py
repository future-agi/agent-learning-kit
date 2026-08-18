"""Running the agent's own tools where the agent's own dependencies are.

The harness imports an agent's tools into its own interpreter, which works only for an agent
that imports nothing the harness does not already have. tau-bench's tools import the standard
library and bind straight in; the sandwich agent's import langchain, and binding failed with
``No module named 'langchain'``. Until now the stage answered that by writing the tool itself,
which is how a world ends up testing an agent nobody ships.

So an agent that cannot be imported here is given a container of its own -- its own Dockerfile
where it ships one, because that is its real environment as deployed, and one built from what
the contract already records about its runtime where it does not. Its tools are then called
inside it, over ``docker exec``, with the call and its answer as JSON.

The store joins the same network, so the agent reaches it by name the way it would anywhere
else. That is the point of doing it this way rather than with a virtualenv: a virtualenv
isolates Python packages and nothing else, and an agent that needs an audio library or a
database client needs more than packages.

Deliberately the fallback. An agent whose tools import cleanly is bound in-process, because a
container costs minutes to build and buys nothing there.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

# Long enough to install a real dependency tree.
PATIENCE = 1800

LABEL = "alk.harness.agent"
RUNNER_AT = "/alk/_call.py"

# Runs inside the agent's container. One call in on stdin, one answer out on stdout.
RUNNER = '''
import importlib, json, sys

def main():
    ask = json.loads(sys.stdin.read())
    found = importlib.import_module(ask["module"])
    target = found
    for part in ask["callable"].split("."):
        target = getattr(target, part)
    if ask.get("factory"):
        target = getattr(getattr(found, ask["factory"])(), ask["callable"].split(".")[-1])
    args = dict(ask.get("args") or {})
    first, state = ask.get("first_arg"), ask.get("state")
    value = target(state, **args) if first else target(**args)
    answer = {"ok": True, "result": value}
    # A tool handed the world's own structure mutates it in place, so what it looks like
    # afterwards is part of the answer.
    if first:
        answer["state"] = state
    json.dump(answer, sys.stdout, default=str)

try:
    main()
except Exception as raised:
    json.dump({"ok": False, "error": f"{type(raised).__name__}: {raised}"}, sys.stdout)
'''

DOCKERFILE = """FROM python:{version}-slim
WORKDIR /agent
COPY . /agent
RUN pip install --no-cache-dir uv 2>/dev/null || true
RUN {install}
"""


class SandboxError(RuntimeError):
    """The agent's own environment could not be built, or could not answer.

    Always ours or the agent's setup, never a verdict on the agent's behaviour: a tool that
    could not be run has not been tested, and saying so is the whole reason this exists.
    """


def _docker(*args: str, check: bool = True, stdin: str | None = None) -> tuple[int, str]:
    try:
        done = subprocess.run(  # nosec B603 — list args, never shell=True
            ("docker", *args), input=stdin, capture_output=True, text=True, timeout=PATIENCE
        )
    except FileNotFoundError as exc:
        raise SandboxError("docker is not on PATH, so no agent container can be built") from exc
    except subprocess.TimeoutExpired as exc:
        raise SandboxError(f"docker {args[0]} gave up after {PATIENCE}s") from exc
    output = ((done.stdout or "") + (done.stderr or "")).strip()
    if check and done.returncode != 0:
        raise SandboxError(f"docker {args[0]} failed: {output[-1200:]}")
    return done.returncode, output


def _version(said: str) -> str:
    """A Python version a base image actually has, from however the contract phrased it.

    ">=3.11", "3.10+", "Python 3.12" all appear. What matters is the two numbers.
    """
    found = re.search(r"(\d+)\.(\d+)", said or "")
    return f"{found.group(1)}.{found.group(2)}" if found else "3.11"


def dockerfile_for(source_root: Path | str, runtime: object) -> tuple[Path, bool]:
    """The Dockerfile to build this agent from, and whether it is the agent's own.

    Its own wins, always. A Dockerfile in the repository is the environment its author says the
    code runs in, and anything generated here is at best a good guess at the same thing.
    """
    root = Path(source_root)
    for named in ("Dockerfile", "dockerfile"):
        if (root / named).exists():
            return root / named, True

    declared = str(getattr(runtime, "dockerfile", "") or "")
    if declared and (root / declared).exists():
        return root / declared, True

    written = root / ".alk-generated.Dockerfile"
    install = str(getattr(runtime, "install", "") or "").strip()
    if not install:
        install = "pip install -e ." if (root / "pyproject.toml").exists() else (
            "pip install -r requirements.txt" if (root / "requirements.txt").exists() else ""
        )
    if not install:
        raise SandboxError(
            f"{root} says nothing about how it is installed -- no Dockerfile, no install "
            "command in the contract, no pyproject and no requirements. Its tools cannot be run "
            "here, and that is a finding to report rather than a gap to write around."
        )
    written.write_text(
        DOCKERFILE.format(version=_version(str(getattr(runtime, "version", ""))), install=install),
        encoding="utf-8",
    )
    return written, False


def network_for(session: str) -> str:
    """A network for this session, so the agent and the store can see each other by name."""
    name = f"alk-net-{session}"
    _docker("network", "create", name, check=False)
    return name


def stand_up(session: str, source_root: Path | str, runtime: object, store: object = None) -> str:
    """Build the agent's image, start it, and put it where the store is. Returns the container.

    Kept alive doing nothing, because a container that exits cannot be exec'd into and starting
    one per call would cost more than the call.
    """
    root = Path(source_root)
    recipe, its_own = dockerfile_for(root, runtime)
    tag = f"alk-agent-{session}".lower()
    _docker("build", "-t", tag, "-f", str(recipe), str(root))

    network = network_for(session)
    container = f"alk-agent-{session}".lower()
    _docker("rm", "--force", container, check=False)
    _docker(
        "run", "--detach", "--name", container, "--label", f"{LABEL}=1",
        "--network", network, "--entrypoint", "sleep", tag, "infinity",
    )

    # The store joins the same network, so the agent reaches it by container name rather than
    # through the host -- which is what it would do anywhere else it runs.
    named = getattr(store, "container", "")
    if named:
        _docker("network", "connect", network, named, check=False)

    _docker("exec", container, "mkdir", "-p", "/alk")
    _docker("exec", "-i", container, "sh", "-c", f"cat > {RUNNER_AT}", stdin=RUNNER)
    return container


def call(
    container: str,
    module: str,
    called: str,
    args: dict,
    *,
    factory: str = "",
    first_arg: str = "",
    state: object = None,
    workdir: str = "",
) -> tuple[object, object]:
    """Run one tool call inside the agent's container. Returns its answer and the state after.

    Raises only where the sandbox itself failed. A tool that refused is an answer and comes back
    as one, because a refusal is the agent behaving, not the harness breaking.
    """
    ask = {
        "module": module, "callable": called, "factory": factory,
        "args": args, "first_arg": first_arg, "state": state,
    }
    where = ["--workdir", f"/agent/{workdir}".rstrip("/")] if workdir and workdir != "." else []
    # Whichever interpreter has the agent's dependencies. `uv sync` and `poetry install` put
    # them in a virtualenv inside the checkout rather than on the system python, so running the
    # obvious `python` finds the agent's own code and none of what it imports.
    picked = (
        f'if [ -x /agent/.venv/bin/python ]; then P=/agent/.venv/bin/python; '
        f'else P=python; fi; exec "$P" {RUNNER_AT}'
    )
    code, output = _docker(
        "exec", "-i", *where, "--env", "PYTHONPATH=/agent:/agent/src",
        container, "sh", "-c", picked,
        check=False, stdin=json.dumps(ask, default=str),
    )
    if code != 0 or not output.strip():
        raise SandboxError(
            f"the agent's container could not run {module}.{called}: {output[-800:] or 'no output'}"
        )
    answer = json.loads(output[output.index("{"):])
    if not answer.get("ok"):
        raise SandboxError(answer.get("error") or "the call failed with no reason given")
    return answer.get("result"), answer.get("state")


def tear_down(session: str) -> None:
    """Remove what this session started. Safe when it started nothing."""
    _docker("rm", "--force", f"alk-agent-{session}".lower(), check=False)
    _docker("network", "rm", f"alk-net-{session}", check=False)
