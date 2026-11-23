import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

# The helper module is not a test file, so its asserts are rewritten only if registered first.
pytest.register_assert_rewrite("reference_agents")


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


@pytest.fixture
def chown_path():
    """Where a fake ``chown`` call lands; ``user_files.own_tree`` passes a name in a descriptor."""

    def resolve(path, dir_fd=None) -> Path:
        if dir_fd is None:
            return Path(path)
        if sys.platform == "darwin":
            import fcntl

            base = fcntl.fcntl(dir_fd, fcntl.F_GETPATH, bytes(1024)).split(b"\0", 1)[0].decode()
        else:
            base = os.readlink(f"/proc/self/fd/{dir_fd}")
        return Path(os.path.normpath(os.path.join(base, path)))

    return resolve
