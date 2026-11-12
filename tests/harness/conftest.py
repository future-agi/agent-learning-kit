import hashlib
import json
import multiprocessing
import os
import time

import pytest

# A killed child is reaped by the fork server, a moment after its parent's join returns.
_REAP_SECONDS = 10.0


@pytest.fixture(scope="session", autouse=True)
def _save_checker_fork_server():
    """Starts the fork server once per test process, so no test pays its start in a deadline."""
    try:
        from fi.alk.harness.forkserver_preload import PRELOAD
        from fi.alk.harness.save_checker import default_mode
    except ImportError:
        yield
        return
    if default_mode() == "child":
        try:
            context = multiprocessing.get_context("forkserver")
            context.set_forkserver_preload(PRELOAD)
            warm = context.Process(target=time.sleep, args=(0,), daemon=True)
            warm.start()
            warm.join(120)
        except Exception:
            # Only a warm-up: a test that starts a child starts the fork server itself.
            pass
    yield


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.fixture(autouse=True)
def _save_checkers_closed():
    """Closes every save checker a test left open, and fails it if one of their children lives.

    Only the pids those checkers started are checked, not the fork server's or the folder scan's.
    """
    yield
    try:
        from fi.alk.harness import save_checker
    except ImportError:
        return
    checkers = list(save_checker._OPEN)
    pids = [pid for checker in checkers for pid in checker.pids]
    for checker in checkers:
        checker.close()
        save_checker._OPEN.discard(checker)
    until = time.monotonic() + _REAP_SECONDS
    alive = [pid for pid in pids if _alive(pid)]
    while alive and time.monotonic() < until:
        time.sleep(0.05)
        alive = [pid for pid in alive if _alive(pid)]
    if alive:
        pytest.fail(f"save checker children still running after the test: {alive}")


@pytest.fixture
def resolved_compose_images(monkeypatch):
    from fi.alk.harness.certified_compose_runtime import CertifiedComposeRuntimeProvider
    from fi.alk.harness.compose_broker import ComposeBrokerClient
    from fi.alk.harness.compose_image_lock import ResolvedComposeImage, image_repository

    calls = []

    def resolve(self, *, manifest, document, runtime, source):
        calls.append(document)
        return [
            ResolvedComposeImage(
                service=name,
                declared_reference=service["image"],
                immutable_reference=image_repository(service["image"])
                + "@sha256:"
                + hashlib.sha256(service["image"].encode()).hexdigest(),
                platform="linux/amd64",
                correspondence="dependency",
            )
            for name, service in json.loads(document)["services"].items()
            if not service.get("build")
        ]

    monkeypatch.setattr(CertifiedComposeRuntimeProvider, "resolve_images", resolve)
    monkeypatch.setattr(ComposeBrokerClient, "resolve_images", resolve)
    return calls
