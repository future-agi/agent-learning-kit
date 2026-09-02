"""Regression: `ContainerStore._await_ready`'s timeout path must not leak the container it
started.

`start()` sets `_started = True` (world/stores/container.py) BEFORE `_await_ready()` runs, so a
container that never answers within `READY_TIMEOUT_SECONDS` used to raise `StoreError` straight
out of `start()` with the container still running -- nothing else ever calls `stop()` on a store
whose own `start()` raised, so the container was orphaned for good. Docker-gated, following the
same rule as `tests/test_harness_stores.py`'s own Postgres lane.
"""

from __future__ import annotations

import pytest

from fi.alk.bench._docker import docker_available
from fi.alk.harness.world.stores import container
from fi.alk.harness.world.stores.postgres import PostgresStore

pg = pytest.mark.skipif(not docker_available(), reason="docker daemon unavailable")


class NeverReadyStore(PostgresStore):
    """Boots exactly like `PostgresStore` (so the container genuinely starts and stays running --
    a real "running but not an engine that answers" case, not a `docker run` failure) but its
    `probe()` never succeeds, so `_await_ready` always times out."""

    def probe(self) -> None:
        raise ConnectionError("deliberately never ready, for the leak-on-timeout regression")


def _container_exists(name: str) -> bool:
    listed = container.docker(
        "ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}", check=False
    )
    return name in listed.splitlines()


@pg
def test_await_ready_timeout_removes_the_container_it_started(monkeypatch) -> None:
    # A short deadline keeps this fast -- the bug and the fix are both about WHAT HAPPENS on
    # timeout, not about how long a real engine takes to boot.
    monkeypatch.setattr(container, "READY_TIMEOUT_SECONDS", 0.5)
    # The engine this store starts for itself, rather than one it was handed. Stores normally
    # share an engine per image and a shared engine is only made ready once, so the timeout path
    # belongs to whoever starts one.
    monkeypatch.setenv(container.PER_STORE, "1")
    store = NeverReadyStore()
    try:
        with pytest.raises(container.StoreError, match="did not answer"):
            store.start()
        assert not _container_exists(store.container)
        assert store._started is False  # same post-teardown state `stop()` leaves on success
        assert store.port is None
    finally:
        # Backstop only -- a passing test already removed it via the fixed timeout path.
        store.stop()


class TestEnginesAreReleasedWhenARunIsKilled:
    """`atexit` alone leaks, because a long run is normally ended by killing it.

    Every run stopped with a signal left its Postgres engine behind: three abandoned runs, three
    containers still up hours later, which is what made a laptop unusable.
    """

    def test_a_signal_releases_the_engines_then_lets_the_process_die(self, monkeypatch):
        released: list[str] = []
        monkeypatch.setattr(
            container, "_release_engines", lambda: released.append("released")
        )
        killed: list[int] = []
        monkeypatch.setattr(container.os, "kill", lambda _pid, number: killed.append(number))
        monkeypatch.setattr(container.signal, "signal", lambda *args: None)

        container._release_on_signal(container.signal.SIGTERM, None)

        assert released == ["released"], "the engines were not released before dying"
        assert killed == [container.signal.SIGTERM], "the process did not die the way it was asked"

    def test_a_handler_somebody_else_installed_is_left_alone(self, monkeypatch):
        """This module is imported into other people's processes and must not change their exit."""
        theirs = lambda *_args: None  # noqa: E731
        monkeypatch.setattr(container.signal, "getsignal", lambda _n: theirs)
        installed: list[object] = []
        monkeypatch.setattr(
            container.signal, "signal", lambda _n, handler: installed.append(handler)
        )
        container._catch_signals()
        assert installed == []
