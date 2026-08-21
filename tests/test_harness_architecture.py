from __future__ import annotations

from pathlib import Path
import asyncio
from types import SimpleNamespace

import pytest

from fi.alk.harness.bundle import (
    BundleError,
    BundleProvenance,
    BundleRuntime,
    RuntimeKind,
    export_session_bundle,
    load_bundle,
    seal_bundle,
)
from fi.alk.harness.events import BufferedEventSink, EventOutbox
from fi.alk.harness.executor import GitHubSourceAcquirer
from fi.alk.harness.job import HarnessJob
from fi.simulate.runtime.events import CanonicalEvent


def _manifest() -> dict:
    return {
        "name": "ride-environment",
        "digest": "sha256:" + "0" * 64,
        "runtime": BundleRuntime(kind=RuntimeKind.COMPOSE, document="compose.yaml"),
        "services": ["tools-api", "postgres"],
        "capabilities": {
            "ride_tools": {
                "protocol": "http",
                "service": "tools-api",
                "container_port": 8080,
                "configuration_name": "TOOLS_API_URL",
            }
        },
        "readiness": [
            {"capability": "ride_tools", "path": "/health", "timeout_seconds": 60}
        ],
        "provenance": BundleProvenance(
            source_kind="repository", source_digest="a" * 64
        ),
    }


def test_bundle_is_content_addressed_and_reproducible(tmp_path: Path) -> None:
    (tmp_path / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    first = seal_bundle(tmp_path, _manifest())
    second = seal_bundle(tmp_path, first)

    assert first.digest == second.digest
    assert load_bundle(tmp_path).digest == first.digest
    assert first.files[0].path == "compose.yaml"


def test_bundle_detects_file_tampering(tmp_path: Path) -> None:
    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    seal_bundle(tmp_path, _manifest())
    compose.write_text("services: {unexpected: {}}\n", encoding="utf-8")

    with pytest.raises(BundleError, match="bundle_files_changed"):
        load_bundle(tmp_path)


def test_bundle_refuses_customer_secrets(tmp_path: Path) -> None:
    (tmp_path / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / ".env").write_text("API_KEY=customer-secret\n", encoding="utf-8")

    with pytest.raises(BundleError, match="bundle_secret_file_forbidden"):
        seal_bundle(tmp_path, _manifest())


def test_exported_bundle_survives_source_as_an_internal_artifact(
    tmp_path: Path,
) -> None:
    source = tmp_path / "agent"
    session = tmp_path / "session"
    source.mkdir()
    session.mkdir()
    (source / "docker-compose.yml").write_text(
        "services: {tools-api: {image: busybox}}\n"
    )
    (source / "handler.py").write_text("def handle(): return 'ok'\n")
    (source / ".env.local").write_text("API_KEY=not-copied\n")
    (session / "contract.json").write_text('{"agent":"ride"}\n')

    root, bundle = export_session_bundle(source, session, name="ride-environment")

    assert bundle.runtime.document == "services/source/docker-compose.yml"
    assert (root / "services/source/handler.py").exists()
    assert not (root / "services/source/.env.local").exists()
    assert load_bundle(root).digest == bundle.digest


def test_local_and_hosted_jobs_reject_the_other_sides_source() -> None:
    with pytest.raises(ValueError, match="hosted_job_cannot_use_local_path"):
        HarnessJob(
            job_id="job",
            run_id="run",
            execution="hosted",
            source={"kind": "local_repository", "local_path": "/private/agent"},
            agent={"connector": "http"},
        )

    with pytest.raises(ValueError, match="local_job_cannot_use_platform_github"):
        HarnessJob(
            job_id="job",
            run_id="run",
            execution="local",
            source={
                "kind": "github",
                "installation_id": "installation",
                "repository": "customer/agent",
            },
            agent={"connector": "http"},
        )


def test_job_carries_references_but_rejects_resolved_secrets() -> None:
    job = HarnessJob(
        job_id="job",
        run_id="run",
        execution="hosted",
        source={
            "kind": "github",
            "installation_id": "installation",
            "repository": "customer/agent",
            "ref": "main",
        },
        agent={
            "connector": "livekit",
            "secret_refs": {
                "api_key": {
                    "manager": "futureagi",
                    "key": "secret_livekit_key",
                    "purpose": "connect to agent",
                }
            },
        },
    )
    assert job.agent.secret_refs["api_key"].key == "secret_livekit_key"

    with pytest.raises(ValueError, match="resolved_secret_forbidden"):
        HarnessJob(
            job_id="job",
            run_id="run",
            execution="hosted",
            source={
                "kind": "github",
                "installation_id": "installation",
                "repository": "customer/agent",
            },
            agent={"connector": "livekit", "config": {"api_key": "raw-key"}},
        )


class _Transport:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def send(self, _run_id: str, events: list[CanonicalEvent]) -> set[str]:
        self.seen.extend(event.event_id for event in events)
        return {event.event_id for event in events}


def test_event_delivery_is_durable_and_retryable(tmp_path: Path) -> None:
    outbox = EventOutbox(tmp_path, "run")
    offline = BufferedEventSink(outbox)
    events = [
        CanonicalEvent.create(
            run_id="run",
            test_case_id="harness",
            event_type="harness.stage.completed",
            source="harness",
            sequence=index,
            payload={"stage": stage},
        )
        for index, stage in enumerate(("environment", "scenarios"))
    ]
    for event in events:
        offline.write(event)
    assert [event.event_id for event in outbox.pending()] == [
        event.event_id for event in events
    ]

    transport = _Transport()
    online = BufferedEventSink(outbox, transport)
    assert online.flush() == 2
    assert outbox.pending() == []
    assert online.flush() == 0
    assert transport.seen == [event.event_id for event in events]


def test_hosted_github_checkout_keeps_token_out_of_process_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "installation-secret-token"
    observed = {}

    def run(command, **kwargs):
        observed["command"] = command
        observed["environment"] = kwargs["env"]
        Path(command[-1]).mkdir()
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("fi.alk.harness.executor.subprocess.run", run)
    job = HarnessJob(
        job_id="job",
        run_id="run",
        execution="hosted",
        source={
            "kind": "github",
            "installation_id": "installation",
            "repository": "customer/private-agent",
            "ref": "main",
        },
        agent={"connector": "http"},
    )

    checkout = asyncio.run(
        GitHubSourceAcquirer(lambda _installation: token).acquire(job, tmp_path)
    )

    assert checkout == tmp_path / "repository"
    assert token not in " ".join(observed["command"])
    assert observed["environment"]["GIT_CONFIG_VALUE_0"].endswith(token)
