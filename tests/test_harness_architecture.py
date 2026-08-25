from __future__ import annotations

from pathlib import Path

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
from fi.alk.harness.executor import _failure_from_events
from fi.alk.harness.job import HarnessJob
from fi.alk.harness.provision import source_fingerprint
from fi.simulate.runtime.spec import RuntimeIsolation
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

    with pytest.raises(
        ValueError, match="local_job_cannot_use_private_platform_github"
    ):
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
            "commit_sha": "a" * 40,
        },
        runtime={"isolation": "dedicated_vm"},
        agent={
            "connector": "livekit",
            "secret_refs": {
                "api_key": {
                    "manager": "platform-vault",
                    "key": "secret_livekit_key",
                    "purpose": "target_provider",
                }
            },
        },
    )
    assert job.agent.secret_refs["api_key"].key == "secret_livekit_key"
    assert job.runtime.isolation is RuntimeIsolation.DEDICATED_VM

    with pytest.raises(ValueError, match="resolved_secret_forbidden"):
        HarnessJob(
            job_id="job",
            run_id="run",
            execution="hosted",
            source={
                "kind": "github",
                "installation_id": "installation",
                "repository": "customer/agent",
                "commit_sha": "a" * 40,
            },
            agent={"connector": "livekit", "config": {"api_key": "raw-key"}},
            runtime={"isolation": "dedicated_vm"},
        )


def test_public_github_job_needs_no_installation_but_is_commit_pinnable() -> None:
    job = HarnessJob(
        job_id="job",
        run_id="run",
        execution="hosted",
        source={
            "kind": "github",
            "visibility": "public",
            "repository": "customer/public-agent",
            "ref": "main",
            "commit_sha": "a" * 40,
        },
        agent={"connector": "auto"},
        runtime={"isolation": "dedicated_vm"},
    )

    assert job.source.installation_id is None
    assert job.source.commit_sha == "a" * 40


@pytest.mark.parametrize(
    "security",
    [
        {"allow_privileged": True},
        {"allow_host_runtime_control": True},
        {"read_only_source": False},
    ],
)
def test_hosted_job_rejects_unsafe_security_policy(security: dict) -> None:
    with pytest.raises(ValueError, match="hosted_.*forbidden|hosted_source"):
        HarnessJob(
            job_id="job",
            run_id="run",
            execution="hosted",
            source={
                "kind": "github",
                "visibility": "public",
                "repository": "customer/public-agent",
                "commit_sha": "a" * 40,
            },
            runtime={"isolation": "dedicated_vm"},
            agent={"connector": "auto"},
            security=security,
        )


def test_job_retry_policy_cannot_retry_agent_or_grading_failures() -> None:
    with pytest.raises(ValueError, match="retry_domain_unsafe"):
        HarnessJob(
            job_id="job",
            run_id="run",
            execution="hosted",
            source={
                "kind": "github",
                "visibility": "public",
                "repository": "customer/public-agent",
                "commit_sha": "a" * 40,
            },
            runtime={"isolation": "dedicated_vm"},
            agent={"connector": "auto"},
            retry={"retryable_domains": ["agent", "grading"]},
        )


def test_failed_stage_is_reported_as_structured_non_retryable_failure(tmp_path: Path):
    (tmp_path / "harness-events.jsonl").write_text(
        '{"type":"harness.stage.failed","payload":'
        '{"stage":"scenarios","status":1,"detail":"invalid generated fixture"}}\n',
        encoding="utf-8",
    )

    failure = _failure_from_events(tmp_path)

    assert failure.domain.value == "simulator"
    assert failure.stage.value == "validating_scenarios"
    assert failure.retryable is False


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


def test_source_fingerprint_hashes_symlink_metadata_without_reading_target(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    secret = tmp_path / "outside-secret"
    secret.write_text("first secret", encoding="utf-8")
    (source / "link").symlink_to(secret)

    first = source_fingerprint(source)
    secret.write_text("different secret", encoding="utf-8")
    second = source_fingerprint(source)

    assert first == second
