import hashlib
import json
import os

import pytest


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


@pytest.fixture(autouse=True)
def _no_controller_mark_left_behind():
    # Hosted authoring's main() marks its own process as a sandbox controller and loads a .env into
    # the runtime namespace. A test that runs it in-process must not leave either behind for later
    # tests in the session, which would then take the controller's .env path, not the local one.
    marked = "ALK_RUNTIME_CONFIGURATION_NAMES" in os.environ
    runtime = {name for name in os.environ if name.startswith("ALK_RUNTIME_VALUE_")}
    yield
    if not marked:
        os.environ.pop("ALK_RUNTIME_CONFIGURATION_NAMES", None)
    for name in [n for n in os.environ if n.startswith("ALK_RUNTIME_VALUE_") and n not in runtime]:
        del os.environ[name]
