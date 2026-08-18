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
SERVER_AT = "/alk/_serve.py"
LOG_AT = "/alk/server.log"
PORT = 8765

# Runs inside the agent's container for the life of the session, holding the world's state.
#
# Resident rather than one call per process because the agent's tools take that state as their
# first argument and mutate it in place. Shipping it across the boundary per call would mean
# two megabytes each way, fifty times a scenario, to hand a tool back what it already had.
# Stdlib only: this has to run in whatever image the agent brought.
SERVER = '''
import importlib, json
from http.server import BaseHTTPRequestHandler, HTTPServer

HELD = {"state": None}

def resolve(ask):
    found = importlib.import_module(ask["module"])
    target = found
    for part in ask["callable"].split("."):
        target = getattr(target, part)
    if ask.get("factory"):
        target = getattr(getattr(found, ask["factory"])(), ask["callable"].split(".")[-1])
    return target

def note(said):
    """One line to stdout, so `docker logs` shows what this container was asked to do.

    It showed nothing at all before: the access log was suppressed and nothing replaced it, so
    a container that had run fifty calls and one that had run none looked identical from
    outside, and a failure inside it left no trace anywhere.
    """
    print(said, flush=True)


def brief(value, limit=160):
    said = value if isinstance(value, str) else repr(value)
    return said if len(said) <= limit else said[:limit] + "..."


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        # Replaced, not silenced: one line per call below says more than a request log would.
        pass

    def _reply(self, body, code=200):
        raw = json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/health":
            return self._reply({"ok": True})
        if self.path == "/state":
            return self._reply({"ok": True, "state": HELD["state"]})
        self._reply({"ok": False, "error": "no such path"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        ask = json.loads(self.rfile.read(length) or "{}")
        if self.path == "/state":
            HELD["state"] = ask.get("state")
            held = HELD["state"]
            note("state set: " + brief(
                {k: len(v) for k, v in held.items()} if isinstance(held, dict) else type(held).__name__
            ))
            return self._reply({"ok": True})
        if self.path != "/call":
            return self._reply({"ok": False, "error": "no such path"}, 404)
        called = str(ask.get("module")) + "." + str(ask.get("callable"))
        try:
            target = resolve(ask)
            args = dict(ask.get("args") or {})
            note("call  " + called + " " + brief(args))
            if ask.get("first_arg"):
                value = target(HELD["state"], **args)
            else:
                value = target(**args)
            note("  ok  " + brief(value))
            self._reply({"ok": True, "result": value})
        except Exception as raised:
            import traceback

            note("  raised " + type(raised).__name__ + ": " + str(raised))
            # The whole traceback, because the one line above is rarely enough to see why a
            # tool inside somebody else's package fell over.
            note(traceback.format_exc())
            # The tool refusing is an answer; the sandbox breaking is not. Both come back, and
            # the caller decides which it is from `raised`.
            self._reply({"ok": False, "raised": True,
                         "error": type(raised).__name__ + ": " + str(raised)})

HTTPServer(("0.0.0.0", PORT_HERE), Handler).serve_forever()
'''

# A slim base carries no compiler, and a requirements file pinned to versions with no wheel for
# this platform then fails on whatever has to be built from source -- pyzmq, tiktoken, yarl and
# pillow all did, on an agent whose own dependencies were otherwise fine.
DOCKERFILE = """FROM python:{version}-slim
WORKDIR /agent
RUN apt-get update \\
 && apt-get install -y --no-install-recommends build-essential git \\
 && rm -rf /var/lib/apt/lists/*
COPY . /agent
RUN pip install --no-cache-dir uv 2>/dev/null || true
{install}
"""


class ToolRefused(Exception):
    """The agent's own tool raised. Its behaviour, not ours, and recorded as a refusal."""


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


def dockerfile_for(
    source_root: Path | str, runtime: object, written: Path | str | None = None
) -> tuple[Path, bool]:
    """The Dockerfile to build this agent from, and whether it is the agent's own.

    Its own wins, always. A Dockerfile in the repository is the environment its author says the
    code runs in, and anything generated here is at best a good guess at the same thing.
    """
    # One the stage wrote for this session wins over everything. It was written by whoever read
    # the repository and watched the generated one fail, and ignoring it is how a build ends up
    # repeating a recipe already known not to work -- one said so: "the harness always uses its
    # own generated Dockerfile and ignores my override".
    if written:
        found = Path(written)
        for candidate in (found, found / "Dockerfile", found / "env" / "Dockerfile"):
            if candidate.is_file():
                return candidate, True

    root = Path(source_root)
    for named in ("Dockerfile", "dockerfile"):
        if (root / named).exists():
            return root / named, True

    declared = str(getattr(runtime, "dockerfile", "") or "")
    if declared and (root / declared).exists():
        return root / declared, True

    written = root / ".alk-generated.Dockerfile"
    steps = _install_steps(root, runtime)
    if not steps:
        raise SandboxError(
            f"{root} says nothing about what it needs -- no Dockerfile, no install command in "
            "the contract, no pyproject and no requirements. Its tools cannot be run here, and "
            "that is a finding to report rather than a gap to write around."
        )
    written.write_text(
        DOCKERFILE.format(version=_version(str(getattr(runtime, "version", ""))), install=steps),
        encoding="utf-8",
    )
    return written, False


def _declared(root: Path) -> list[str]:
    """What the agent says it needs, from whichever file it says it in.

    Read rather than installed-by-proxy. ``pip install -e .`` only works on a project that is
    packageable, and plenty of agents are not: a folder with main.py and a requirements file, an
    app under src/ with no packaging metadata, a subdirectory of a monorepo. One failed on
    exactly that -- no build-system table, so pip had nothing to build with.
    """
    manifest = root / "pyproject.toml"
    if manifest.exists():
        try:
            import tomllib

            found = tomllib.loads(manifest.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - an unreadable manifest is not a reason to stop
            found = {}
        listed = ((found.get("project") or {}).get("dependencies")) or []
        if listed:
            return [str(one) for one in listed]
    requirements = root / "requirements.txt"
    if requirements.exists():
        return [
            line.strip()
            for line in requirements.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith(("#", "-"))
        ]
    return []


def _install_steps(root: Path, runtime: object) -> str:
    """The RUN lines that put the agent's dependencies in the image.

    Its dependencies are the point; installing the project itself is not. The code is copied in
    and reached through PYTHONPATH, so it is already importable -- and the package install is
    the part that fails on an agent that was never meant to be packaged. So the declared
    requirements go in first and must succeed, and the project install is attempted afterwards
    and allowed not to.
    """
    import shlex as quoting

    said = _command(str(getattr(runtime, "install", "") or ""))
    declared = _declared(root)
    lines: list[str] = []
    if declared:
        listed = " ".join(quoting.quote(one) for one in declared)
        lines.append(f"RUN pip install --no-cache-dir {listed}")
    if said:
        # What the agent itself says, but never fatal: it is usually `pip install -e .`, which
        # is exactly the step that cannot work on a project with no build backend.
        lines.append(f"RUN {said} || true")
    elif (root / "pyproject.toml").exists():
        lines.append("RUN pip install --no-cache-dir -e . || true")
    return "\n".join(lines)


def _command(said: str) -> str:
    """The runnable part of however the contract phrased an install step.

    It is written by something reading a repository, and it explains itself: one agent recorded
    "pip install -e . (from repo root; pyproject.toml present)", which a shell reads as a syntax
    error at the bracket. The commentary is dropped and what is left has to look like a command.
    """
    # Only the commentary goes. Trailing punctuation is left alone: the "." in
    # "pip install -e ." is the argument, and stripping it produces a command that fails
    # for a reason nobody would guess from reading it.
    return said.split("(")[0].strip()


def context_for(source_root: Path | str, runtime: object) -> Path:
    """What to copy into the image: the repository, not the package inside it.

    Agents compute paths from ``__file__``. One refuses to import unless a web build sits at
    ``../../web/dist``, so an image built from its package alone moved that to ``/web`` and its
    tools could not load at all -- which then read as the tools being unreachable rather than
    the context being too narrow.

    ``runtime.workdir`` already says how deep the code sits: a workdir of
    ``components/python/src`` means the root is three levels above it. Climbing that far turns
    the package back into the repository it came from.
    """
    root = _with_a_manifest(Path(source_root))
    workdir = str(getattr(runtime, "workdir", "") or "").strip("./")
    if not workdir or workdir == ".":
        return root
    # Only where the checkout actually ends in that path. A workdir naming somewhere else
    # entirely is a disagreement to leave alone rather than to climb blindly out of.
    depth = len(Path(workdir).parts)
    climbed = root
    for _ in range(depth):
        if climbed.name and (climbed.parent / Path(workdir)).is_dir():
            return climbed.parent
        climbed = climbed.parent
    return root


MANIFESTS = ("pyproject.toml", "requirements.txt", "setup.py", "setup.cfg")


def _with_a_manifest(root: Path, climb: int = 4) -> Path:
    """The nearest directory at or above ``root`` that says what the agent needs.

    Pointing at a subdirectory is a normal thing to do -- "the agent is in this folder" -- and
    the thing that declares its dependencies is often a level or two up. One agent was given as
    <repo>/src, because that is the directory its packages import from, while the pyproject sat
    in <repo>; the image was built from src, `pip install` found nothing to install, and the
    failure said nothing about a path.

    Bounded, and it keeps what it was given when nothing turns up: climbing to the filesystem
    root looking for a manifest would eventually find somebody else's.
    """
    if any((root / named).exists() for named in MANIFESTS):
        return root
    found = root
    for _ in range(climb):
        found = found.parent
        if found == found.parent:
            break
        if any((found / named).exists() for named in MANIFESTS):
            return found
    return root


def network_for(session: str) -> str:
    """A network for this session, so the agent and the store can see each other by name."""
    name = f"alk-net-{session}"
    _docker("network", "create", name, check=False)
    return name


def stand_up(
    session: str,
    source_root: Path | str,
    runtime: object,
    store: object = None,
    written: Path | str | None = None,
) -> str:
    """Build the agent's image, start it, and put it where the store is. Returns the container.

    Everything runs this way, not only agents that fail to import here. When the agent's code
    runs in the harness's own interpreter it gets the harness's installed versions: an agent
    shipping one release of a library and tested against another is being measured on a
    combination that exists nowhere, and nothing errors to say so. One path also means one
    behaviour, rather than a fast path that quietly differs from the slow one.
    """
    root = context_for(source_root, runtime)
    recipe, _its_own = dockerfile_for(root, runtime, written)
    tag = f"alk-agent-{session}".lower()
    _docker("build", "-t", tag, "-f", str(recipe), str(root))

    network = network_for(session)
    container = f"alk-agent-{session}".lower()
    _docker("rm", "--force", container, check=False)
    _docker(
        "run", "--detach", "--name", container, "--label", f"{LABEL}=1",
        "--network", network, "--publish", f"127.0.0.1::{PORT}",
        "--entrypoint", "sleep", tag, "infinity",
    )

    # The store joins the same network, so the agent reaches it by container name rather than
    # through the host -- which is what it would do anywhere else it runs.
    named = getattr(store, "container", "")
    if named:
        _docker("network", "connect", network, named, check=False)

    _docker("exec", container, "mkdir", "-p", "/alk")
    _docker(
        "exec", "-i", container, "sh", "-c", f"cat > {SERVER_AT}",
        stdin=SERVER.replace("PORT_HERE", str(PORT)),
    )
    # Whichever interpreter has the agent's dependencies. `uv sync` and `poetry install` put
    # them in a virtualenv inside the checkout rather than on the system python, so the obvious
    # `python` finds the agent's own code and none of what it imports.
    _docker(
        "exec", "--detach", "--env", "PYTHONPATH=/agent:/agent/src", container, "sh", "-c",
        f'if [ -x /agent/.venv/bin/python ]; then P=/agent/.venv/bin/python; else P=python; fi; '
        f'exec "$P" {SERVER_AT} >> {LOG_AT} 2>&1',
    )
    _await(container)
    return container


def _address(container: str) -> str:
    _, mapping = _docker("port", container, f"{PORT}/tcp")
    if not mapping:
        raise SandboxError(f"{container} published no port for {PORT}")
    return "http://127.0.0.1:" + mapping.splitlines()[0].rsplit(":", 1)[1]


def _await(container: str, patience: float = 60.0) -> None:
    """Block until the agent's container answers, and say what its logs held if it never does."""
    import time
    import urllib.error
    import urllib.request

    where = _address(container) + "/health"
    deadline = time.monotonic() + patience
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(where, timeout=2) as answer:  # nosec B310 — loopback
                if answer.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001 - anything means not ready yet
            last = exc
            time.sleep(0.25)
    _, logs = _docker("logs", "--tail", "20", container, check=False)
    raise SandboxError(
        f"the agent's container never answered: {last}\nits last output:\n{logs}"
    )


def _ask(container: str, path: str, body: dict | None = None) -> dict:
    import urllib.request

    raw = json.dumps(body or {}, default=str).encode() if body is not None else None
    request = urllib.request.Request(
        _address(container) + path, data=raw,
        headers={"Content-Type": "application/json"},
        method="POST" if raw is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=PATIENCE) as answer:  # nosec B310
            return json.loads(answer.read())
    except Exception as exc:  # noqa: BLE001
        raise SandboxError(f"the agent's container could not be reached: {exc}") from exc


def set_state(container: str, state: object) -> None:
    """Hand the world's state to the container, which holds it for the scenario."""
    _ask(container, "/state", {"state": state})


def get_state(container: str) -> object:
    """What the state looks like now, after whatever the agent's tools did to it."""
    return _ask(container, "/state").get("state")


def call(
    container: str,
    module: str,
    called: str,
    args: dict,
    *,
    factory: str = "",
    first_arg: str = "",
) -> object:
    """Run one tool call inside the agent's container, against the state it is holding.

    A tool that refused raises ``ToolRefused``: that is the agent behaving, and the world
    records it as a refusal rather than as the sandbox falling over.
    """
    answer = _ask(container, "/call", {
        "module": module, "callable": called, "factory": factory,
        "args": args, "first_arg": first_arg,
    })
    if answer.get("ok"):
        return answer.get("result")
    if answer.get("raised"):
        # The agent's tool raised. Its behaviour, and the traceback is in the container's log
        # for whoever wants to see where inside its package it went.
        raise ToolRefused(str(answer.get("error")))
    raise SandboxError(
        str(answer.get("error") or "the call failed with no reason given") + recent(container)
    )


def tear_down(session: str) -> None:
    """Remove what this session started. Safe when it started nothing."""
    _docker("rm", "--force", f"alk-agent-{session}".lower(), check=False)
    _docker("network", "rm", f"alk-net-{session}", check=False)


def recent(container: str, lines: int = 12) -> str:
    """What the container last said, appended to a failure that came out of it.

    A sandbox error read as "could not run X" and nothing else, while the reason sat in a log
    nobody was looking at. Carrying it into the error is the difference between a failure that
    can be acted on and one that has to be reproduced first.
    """
    # Not `docker logs`: that shows PID 1, and the server is exec'd alongside it, so its
    # output never appears there. It writes to a file inside the container instead.
    _, said = _docker(
        "exec", container, "sh", "-c", f"tail -n {lines} {LOG_AT} 2>/dev/null", check=False
    )
    return f"\n\nwhat the container last said:\n{said}" if said.strip() else ""
