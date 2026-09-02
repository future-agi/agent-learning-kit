"""Standing an engine up in a container, which is the part no engine does differently.

Pulling an image, giving it a free port, waiting for it to actually answer, tearing it down
and not leaking it when a run is killed: none of that is about Postgres. It is the same work
for MySQL, ClickHouse, Mongo or anything else the harness is ever asked to run, so it is
written once here.

What an engine contributes is only what genuinely differs -- how to reach it, how to read what
it holds, and how to put that back. That is a small surface deliberately, because the cost of
teaching the harness a new engine is the thing that decides whether "whatever the agent uses"
is real or just an aspiration.
"""

from __future__ import annotations

import atexit
import os
import signal
import secrets
import subprocess
import time
from dataclasses import dataclass

from . import Held, StoreError

# How long to wait for a fresh container to start answering. The first run on a machine pulls
# the image, which dominates; afterwards this is a second or two.
READY_TIMEOUT_SECONDS = 180.0

# Marks every container this module starts, so strays from a killed run can be found and
# removed without guessing at names.
LABEL = "alk.harness.store"

# The network to join, when the harness is itself in a container. Publishing a port to the
# host's loopback is enough when the harness runs on the host, but from inside a container
# 127.0.0.1 is its own loopback and the engine is not there. Sharing a network instead lets
# the engine be reached by container name, on the port it actually listens on.
NETWORK = "ALK_DOCKER_NETWORK"

# Set this to run one engine per store again, which is only worth it to isolate a run completely.
PER_STORE = "ALK_STORE_PER_WORLD"

# One engine per image for the life of the process, and a database inside it per world.
#
# A container per world is what this used to do, and it does not survive being asked for several
# worlds at once: each engine takes seconds to become ready and megabytes to hold, so a fan-out
# or a test file stands up four or five at a time, the machine slows, and stores start failing
# their readiness deadline rather than the run failing for any reason to do with the harness.
# Sharing one engine makes standing up a world a `CREATE DATABASE`, which is immediate.
_ENGINES: dict[str, "_Engine"] = {}


def _release_on_signal(number: int, _frame: object) -> None:
    """Release the engines, then die the way we were asked to.

    ``atexit`` is not enough on its own: it does not run when a process is terminated, and a
    terminated process is the normal way a long run ends here. Every run stopped that way left its
    engine behind, which is how a machine ends up with one container per abandoned run.
    """
    _release_engines()
    signal.signal(number, signal.SIG_DFL)
    os.kill(os.getpid(), number)


def _catch_signals() -> None:
    """Ask to be told before we are killed, without stamping on a host that already cares.

    Only from the main thread, and never over a handler somebody else installed: this module is
    imported into other people's processes and must not quietly change how they shut down.
    """
    for number in (signal.SIGTERM, signal.SIGINT):
        try:
            if signal.getsignal(number) in (signal.SIG_DFL, None):
                signal.signal(number, _release_on_signal)
        except (ValueError, OSError):  # pragma: no cover - not the main thread
            return


def _release_engines() -> None:
    """Remove the engines this process started, when it ends.

    Nothing else will: a shared engine deliberately outlives the world that paid for it, so
    without this a machine accumulates one container per run until it is the reason the next run
    is slow. Registered once, on the first engine, so importing this module costs nothing.
    """
    for engine in list(_ENGINES.values()):
        docker("rm", "--force", "--volumes", engine.container, check=False)
    _ENGINES.clear()


@dataclass
class _Engine:
    """A running container several stores are pointed at."""

    container: str
    user: str
    password: str
    database: str
    host: str
    port: int | None
    worlds: int = 0


def docker(*args: str, check: bool = True) -> str:
    """Run a docker command, and turn its failure into something worth reading."""
    try:
        done = subprocess.run(  # nosec B603: list args, never shell=True
            ("docker", *args), capture_output=True, text=True, check=False
        )
    except FileNotFoundError as exc:  # pragma: no cover - depends on the machine
        raise StoreError(
            "docker is not on PATH, so no store can be stood up. Install Docker, or start "
            "Colima, and try again."
        ) from exc
    if check and done.returncode != 0:
        raise StoreError(
            f"docker {' '.join(args)} failed ({done.returncode}): "
            f"{(done.stderr or done.stdout).strip()}"
        )
    return done.stdout.strip()


class ContainerStore(Held):
    """An engine the harness runs in a container for the agent to be pointed at.

    Started once for a suite and reset between scenarios: standing an engine up costs seconds
    and putting its data back costs milliseconds, so the container stays and only its contents
    move.

    Subclasses supply ``image``, ``container_port``, the environment the image needs, and how
    to read and restore what it holds. Everything else is here.
    """

    engine: str = ""
    image: str = ""
    container_port: int = 0
    # Environment the image needs to come up with a known user, password and database. Values
    # are formatted with ``user``, ``password`` and ``database``.
    boot_env: dict[str, str] = {}

    def __init__(
        self,
        version: str | None = None,
        image: str | None = None,
        database: str = "alk",
        user: str = "alk",
        password: str | None = None,
    ) -> None:
        default = type(self).image
        if image:
            self.image = image
        elif version:
            self.image = f"{default.split(':')[0]}:{version}"
        else:
            self.image = default
        self.database = database
        self.user = user
        self.password = password or secrets.token_hex(16)
        self.container = f"alk-store-{secrets.token_hex(6)}"
        self.network = os.environ.get(NETWORK, "").strip()
        self.host = "127.0.0.1"
        self.port: int | None = None
        self._started = False
        self._shared: _Engine | None = None
        # Every script `apply` has run, in order. Saved beside the rows so a restore into a
        # fresh container can stand the schema up before putting the rows back.
        self.applied: list[str] = []

    # -- lifecycle -------------------------------------------------------------------

    def start(self) -> None:
        """Point this store at an engine, standing one up if the process has none. Idempotent."""
        if self._started:
            return
        if os.environ.get(PER_STORE, "").strip():
            self._start_alone()
            return

        engine = _ENGINES.get(self.image)
        if engine is None:
            # First world in this process pays for the engine; every one after it pays nothing.
            engine = self._start_engine()
            if not _ENGINES:
                atexit.register(_release_engines)
                _catch_signals()
            _ENGINES[self.image] = engine
        self.container = engine.container
        self.user, self.password = engine.user, engine.password
        self.host, self.port = engine.host, engine.port
        self._shared = engine
        engine.worlds += 1
        self._started = True
        self._make_space(engine)

    def _start_engine(self) -> _Engine:
        """Run the container, wait for it to answer, and describe it for everyone after."""
        engine = _Engine(
            container=f"alk-store-{secrets.token_hex(6)}",
            user=self.user,
            password=self.password,
            database=self.database,
            host="127.0.0.1",
            port=None,
        )
        self.container = engine.container
        self._run_container()
        self._started = True
        if self.network:
            engine.host, engine.port = engine.container, self.container_port
        else:
            engine.port = self._published_port()
        self.host, self.port = engine.host, engine.port
        self._await_ready()
        return engine

    def _start_alone(self) -> None:
        """One engine for this store only, as it used to be."""
        self.container = f"alk-store-{secrets.token_hex(6)}"
        self._run_container()
        self._started = True
        if self.network:
            self.host, self.port = self.container, self.container_port
        else:
            self.port = self._published_port()
        self._await_ready()

    def _run_container(self) -> None:
        environment: list[str] = []
        for name, template in self.boot_env.items():
            environment += [
                "--env",
                f"{name}={template.format(user=self.user, password=self.password, database=self.database)}",
            ]
        docker(
            "run",
            "--detach",
            "--name",
            self.container,
            "--label",
            f"{LABEL}=1",
            *environment,
            *(("--network", self.network) if self.network else ()),
            # Bound to loopback and given whatever port is free, so parallel runs on one
            # machine never collide. Kept even on a shared network, where it is what lets
            # someone on the host open a client against a running scenario.
            "--publish",
            f"127.0.0.1::{self.container_port}",
            self.image,
        )

    def _make_space(self, engine: _Engine) -> None:
        """Give this store its own space inside the shared engine.

        The base class has nowhere to put one, so it shares the engine's own. An engine whose
        stores would tread on each other overrides this.
        """
        self.database = engine.database

    def _drop_space(self) -> None:
        """Remove this store's space. The engine stays up for the next world."""

    def stop(self) -> None:
        """Give up this store's space. Safe when it never started, so teardown needs no guard.

        The engine is deliberately left running. It is shared, and the next world in this process
        wants it; a killed run leaves it labelled, so strays are still findable by label.
        """
        if not self._started:
            return
        if self._shared is None:
            docker("rm", "--force", "--volumes", self.container, check=False)
        else:
            self._drop_space()
            self._shared.worlds -= 1
            self._shared = None
        self._started = False
        self.port = None

    def _published_port(self) -> int:
        mapping = docker("port", self.container, f"{self.container_port}/tcp")
        if not mapping:
            raise StoreError(
                f"{self.container} published no port for {self.container_port}/tcp"
            )
        # "127.0.0.1:32768", or several lines when both stacks are bound.
        return int(mapping.splitlines()[0].rsplit(":", 1)[1])

    def _await_ready(self) -> None:
        """Poll until the engine answers, and say what went wrong if it never does.

        A container that is running is not an engine that is ready: most database images start,
        run their own initialisation, restart once, and only then listen. Connecting is the
        only honest test, which is why this asks the subclass to really connect rather than
        checking that the process exists.
        """
        deadline = time.monotonic() + READY_TIMEOUT_SECONDS
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                self.probe()
                return
            except Exception as exc:  # noqa: BLE001 - any failure means not ready yet
                last = exc
                time.sleep(0.25)
        logs = docker("logs", "--tail", "20", self.container, check=False)
        container = self.container
        # A container that never answers is not a container to leave running -- `start()` already
        # set `_started`, so without this the caller's own teardown never runs (nothing ever calls
        # `stop()` on a store whose `start()` raised) and the container leaks for good.
        self.stop()
        raise StoreError(
            f"{container} did not answer within {READY_TIMEOUT_SECONDS:.0f}s: {last}\n"
            f"last lines of its log:\n{logs}"
        )

    def probe(self) -> None:
        """Really talk to the engine. Anything raised means "not ready yet"."""
        raise NotImplementedError

    # -- what the agent is pointed at ------------------------------------------------

    def dsn(self) -> str:
        """The connection string to hand the agent, in place of its own."""
        raise NotImplementedError

    def env(self, variable: str) -> dict[str, str]:
        """The DSN under the name this agent reads it from.

        Redirecting an agent is usually one environment variable, and which one is a fact about
        the agent rather than about us -- so it is named by the caller, never assumed here.
        """
        return {variable: self.dsn()}

    def address(self) -> tuple[str, int]:
        if not self._started or self.port is None:
            raise StoreError("the store has not been started, so it has no address yet")
        return self.host, self.port


def strays() -> list[str]:
    """Containers the harness started that are still running.

    A killed run leaves its container behind, and the next one has no way to know it is not the
    owner. Naming them is enough; removing them is the caller's decision.
    """
    listed = docker(
        "ps", "--filter", f"label={LABEL}=1", "--format", "{{.Names}}", check=False
    )
    return [name for name in listed.splitlines() if name.strip()]
