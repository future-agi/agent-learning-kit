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

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
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
            return self._reply({"ok": True})
        if self.path != "/call":
            return self._reply({"ok": False, "error": "no such path"}, 404)
        try:
            target = resolve(ask)
            args = dict(ask.get("args") or {})
            if ask.get("first_arg"):
                value = target(HELD["state"], **args)
            else:
                value = target(**args)
            self._reply({"ok": True, "result": value})
        except Exception as raised:
            # The tool refusing is an answer; the sandbox breaking is not. Both come back, and
            # the caller decides which it is from `raised`.
            self._reply({"ok": False, "raised": True,
                         "error": type(raised).__name__ + ": " + str(raised)})

HTTPServer(("0.0.0.0", PORT_HERE), Handler).serve_forever()
'''

DOCKERFILE = """FROM python:{version}-slim
WORKDIR /agent
COPY . /agent
RUN pip install --no-cache-dir uv 2>/dev/null || true
RUN {install}
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

    Everything runs this way, not only agents that fail to import here. When the agent's code
    runs in the harness's own interpreter it gets the harness's installed versions: an agent
    shipping one release of a library and tested against another is being measured on a
    combination that exists nowhere, and nothing errors to say so. One path also means one
    behaviour, rather than a fast path that quietly differs from the slow one.
    """
    root = Path(source_root)
    recipe, _its_own = dockerfile_for(root, runtime)
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
        f'exec "$P" {SERVER_AT}',
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
        raise ToolRefused(str(answer.get("error")))
    raise SandboxError(str(answer.get("error") or "the call failed with no reason given"))


def tear_down(session: str) -> None:
    """Remove what this session started. Safe when it started nothing."""
    _docker("rm", "--force", f"alk-agent-{session}".lower(), check=False)
    _docker("network", "rm", f"alk-net-{session}", check=False)
