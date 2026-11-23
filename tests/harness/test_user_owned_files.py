"""A process's user owns the folder that holds its log and its receipt, and every world shares that
user: a process of it can leave a link there. The harness can write and read almost anywhere, so
it never goes through one. Each test goes through an entry point the harness already had, and each
fails on the code that wrote or read through a link.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from fi.alk.harness import process_runtime as pr
from fi.alk.harness.provider_lifecycle import (
    LifecycleInvocation,
    ProviderContext,
    ProviderLifecycleError,
    ProviderType,
    run_lifecycle_invocation,
    validate_provision_receipt,
)

_CASE = "held-back case: the caller says they lost their card\n"

_LINKS = pytest.mark.parametrize("link", [os.symlink, os.link], ids=["symlink", "hard link"])


@pytest.fixture
def held_back(tmp_path: Path) -> Path:
    case = tmp_path / "bundle" / "scenarios" / "case.yaml"
    case.parent.mkdir(parents=True)
    case.write_text(_CASE, encoding="utf-8")
    return case


@_LINKS
def test_a_process_log_never_writes_through_a_link(tmp_path, held_back, link):
    log = tmp_path / "world" / "process.log"
    log.parent.mkdir()
    link(held_back, log)

    process = pr.default_process_runner(
        ["sh", "-c", "echo from the agent"], cwd=tmp_path, env=dict(os.environ), log_path=log
    )

    assert process.wait(5)
    assert held_back.read_text(encoding="utf-8") == _CASE
    assert not log.is_symlink() and log.stat().st_nlink == 1
    assert log.read_text(encoding="utf-8") == "from the agent\n"


def test_a_process_log_swapped_for_a_link_is_never_read_back(tmp_path, held_back):
    log = tmp_path / "world" / "process.log"
    process = pr.default_process_runner(
        ["true"], cwd=tmp_path, env=dict(os.environ), log_path=log
    )
    assert process.wait(5)
    # What another process of the same user can do once the run has ended.
    log.unlink()
    log.symlink_to(held_back)

    assert _CASE not in process.captured_output()


@_LINKS
def test_a_lifecycle_log_never_writes_through_a_link(tmp_path, held_back, link):
    lifecycle = tmp_path / "lifecycle"
    lifecycle.mkdir()
    log = lifecycle / "provision.log"
    link(held_back, log)
    invocation = LifecycleInvocation(
        argv=("provision",),
        cwd=tmp_path,
        environment={},
        output_path=lifecycle / "provider-target.json",
        timeout_seconds=5,
    )

    def run(*_args, **_kwargs):
        return SimpleNamespace(returncode=1, stdout="from the provider", stderr="")

    assert run_lifecycle_invocation(invocation, log_path=log, run=run) == 1
    assert held_back.read_text(encoding="utf-8") == _CASE
    assert log.read_text(encoding="utf-8") == "from the provider"


def test_a_receipt_left_as_a_link_is_refused(tmp_path):
    context = ProviderContext(
        attempt_id="attempt-1",
        world_id="world-2",
        provider=ProviderType.VAPI,
        public_base_url="https://opaque.example/a/w",
        event_url="https://opaque.example/a/w/events",
        tool_base_url="https://opaque.example/a/w/tools",
        provider_resource_prefix="alk-attempt-1-world-2",
        idempotency_key="attempt-1:world-2:vapi",
        expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
    )
    # A valid receipt, so only the link can be the reason it is refused.
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "provider": "vapi",
                "attempt_id": context.attempt_id,
                "world_id": context.world_id,
                "target": {"kind": "assistant", "id": "assistant-1", "version": None},
                "resources": [{"kind": "assistant", "id": "assistant-1", "owned": True}],
                "cleanup": {"receipt_version": "1", "idempotency_key": context.idempotency_key},
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )
    receipt = tmp_path / "lifecycle" / "provider-target.json"
    receipt.parent.mkdir()
    receipt.symlink_to(elsewhere)

    with pytest.raises(ProviderLifecycleError, match="provider_provision_receipt_invalid"):
        validate_provision_receipt(receipt, context=context)
