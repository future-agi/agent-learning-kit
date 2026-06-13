"""Keyed account-sync client over fi-instrumentation-otel (Phase 8, ARCH §2d).

THE ONLY SANCTIONED NETWORK HOME in the telemetry package — the
``telemetry_boundary`` gate scans every other telemetry module for
network-capable imports. ``fi_instrumentation`` is imported LAZILY inside
``sync_run`` only, after the kill switch and the key check, so the no-key
import graph carries nothing network-capable.

Doctrine (never soften): keys are the consent boundary (P8-D2) — they gate
the DESTINATION, never the capability; metadata by default; content only with
the capture+redaction contract; ``AGENT_LEARNING_TELEMETRY=off`` overrides
keys and binds everything including vendored ``fi/*`` (P8-D6); any transport
failure degrades to local-only with the cursor unmoved (R§3.5).
"""

from __future__ import annotations

import json
import os
from typing import Any, Mapping

from ..config import DEFAULT_API_URL, AgentLearningConfig
from ._contract import (
    FI_KIT_PHASE_ATTR,
    FI_KIT_RUN_ID_ATTR,
    FI_KIT_WORLD_ATTR,
    kill_switch_on,
)
from ._ledger import RunLedger
from ._row import canonical_row_address

SYNC_PATH = "/tracer/v1/traces"
LEDGER_ROW_ATTR = "fi.kit.ledger_row"
SYNC_SPAN_NAME = "agent-learning.ledger-row"
SYNC_PROJECT_NAME = "agent-learning"


def sync_enabled() -> bool:
    """Keys present AND the kill switch not ``off`` (P8-D2 + P8-D6)."""

    if kill_switch_on():
        return False
    config = AgentLearningConfig.from_env()
    return bool(config.api_key and config.secret_key)


def sync_destination() -> dict[str, Any]:
    """The literal destination a real sync would use — names always, values
    never (the dry-run transparency surface prints this, UI-UX §4.1)."""

    config = AgentLearningConfig.from_env()
    base_url = (
        os.environ.get("FI_BASE_URL") or config.api_url or DEFAULT_API_URL
    ).rstrip("/")
    return {
        "endpoint": f"{base_url}{SYNC_PATH}",
        "base_url": base_url,
        "transport": "otlp-http",
        "headers": {
            "X-Api-Key": "present" if config.api_key else "missing",
            "X-Secret-Key": "present" if config.secret_key else "missing",
        },
    }


def encode_metadata_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """The metadata-only sync payload (PRD §4.2): the canonical row fields —
    metadata, hashes, and the contract fields themselves. Content
    (transcripts/prompts/tool I/O) is NEVER part of this projection."""

    return {key: value for key, value in row.items()}


def encoded_run_id(row: Mapping[str, Any]) -> str:
    """The content address the sync encoder transmits — must be IDENTICAL to
    the locally-computed ``run_id`` (gate #72 check 6, identity equivalence).
    ``canonical_row_address`` strips the envelope fields, so the encoder and
    the ledger share one canonicalization discipline."""

    return canonical_row_address(encode_metadata_row(row))


def content_sync_admissible(row: Mapping[str, Any]) -> bool:
    """Row-level content gate: ``content_bearing`` rows need the non-empty
    ``redaction`` mapping (names + strategy) recorded on the row."""

    redaction = row.get("redaction")
    return bool(
        row.get("content_bearing") is True
        and isinstance(redaction, Mapping)
        and redaction
    )


def _collector_reachable(base_url: str, timeout: float = 3.0) -> tuple[bool, str]:
    """Cheap TCP preflight so an unreachable collector degrades to local
    immediately (the OTLP exporter itself fire-and-forgets failures). Lazy
    stdlib imports: this module is the only sanctioned network home, and even
    here network-capable imports stay in-function (gate #72 check 1)."""

    import socket
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    host = parsed.hostname or base_url
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, "ok"
    except OSError as exc:
        return False, f"collector unreachable: {exc}"


def sync_run(
    row: Mapping[str, Any],
    *,
    content: bool = False,
    ledger: RunLedger | None = None,
    world: str | None = None,
) -> dict[str, Any]:
    """Sync one ledger row to the user's own Future AGI account.

    Idempotent by content address (P8-D3): a re-send of an already-confirmed
    ``run_id`` is a no-op. Every failure degrades to local-only — the row
    stays unsynced, the cursor unmoved, and nothing propagates (R§3.5).
    """

    run_id = str(row.get("run_id") or "")
    if kill_switch_on():
        return {"status": "disabled", "reason": "kill_switch", "sent": False}
    config = AgentLearningConfig.from_env()
    if not (config.api_key and config.secret_key):
        return {"status": "no_keys", "sent": False}
    channel = "metadata"
    if content:
        if not content_sync_admissible(row):
            return {
                "status": "refused",
                "reason": "capture_contract_missing",
                "sent": False,
            }
        channel = "metadata+content"
    store = ledger if ledger is not None else RunLedger()
    cursor = store.read_cursor()
    already = cursor["synced"].get(run_id)
    if already == channel or (already == "metadata+content" and channel == "metadata"):
        return {
            "status": "noop",
            "reason": "already_synced",
            "channel": already,
            "sent": False,
        }
    payload = encode_metadata_row(row)
    destination = sync_destination()
    reachable, reason = _collector_reachable(destination["base_url"])
    if not reachable:
        # Degrade-to-local (R§3.5): the row stays unsynced, the cursor
        # unmoved; a later `runs sync --queued` resumes idempotently.
        return {"status": "deferred", "reason": reason, "sent": False}
    try:
        # Lazy: the ONLY place the telemetry package touches a network-capable
        # import, and only after the kill switch + key + admission gates.
        from fi_instrumentation import register
        from fi_instrumentation.fi_types import ProjectType
        from fi_instrumentation.otel import Transport

        provider = register(
            project_name=SYNC_PROJECT_NAME,
            project_type=ProjectType.EXPERIMENT,
            transport=Transport.HTTP,
            headers={
                "X-Api-Key": config.api_key,
                "X-Secret-Key": config.secret_key,
            },
            batch=False,
            verbose=False,
            set_global_tracer_provider=False,
        )
        tracer = provider.get_tracer("agent_learning.telemetry")
        with tracer.start_as_current_span(SYNC_SPAN_NAME) as span:
            span.set_attribute(FI_KIT_RUN_ID_ATTR, run_id)
            span.set_attribute(FI_KIT_PHASE_ATTR, str(row.get("phase") or ""))
            if world:
                span.set_attribute(FI_KIT_WORLD_ATTR, str(world))
            span.set_attribute(
                LEDGER_ROW_ATTR,
                json.dumps(
                    payload, sort_keys=True, separators=(",", ":"), default=str
                ),
            )
        provider.shutdown()
    except BaseException as exc:  # noqa: BLE001 — degrade-to-local (R§3.5)
        return {
            "status": "deferred",
            "reason": f"{type(exc).__name__}: {exc}",
            "sent": False,
        }
    store.write_cursor(run_id, channel)
    return {
        "status": "synced",
        "channel": channel,
        "run_id": run_id,
        "endpoint": destination["endpoint"],
        "sent": True,
    }
