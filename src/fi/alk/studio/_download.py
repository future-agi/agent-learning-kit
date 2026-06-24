"""Read-only account pull lane (Phase 7, unit 6; P7-D5 — no push-back).

Transport is stdlib ``urllib.request`` (ARCH Decision 7; the
``_post_redteam_corpus_hook`` precedent) — the vendored httpx client stays
untouched. Auth is pure reuse of the existing config conventions
(``AgentLearningConfig.from_env`` over the ``AGENT_LEARNING_/FUTURE_AGI_/FI_``
env triples; headers ``X-Api-Key``/``X-Secret-Key`` byte-matching
``fi/api/auth.py``). Release gates NEVER touch this module's network path —
the pure validation functions (``validate_download``, ``verify_pin``) are
what gates exercise on local fixtures.

Every pulled artifact is version-pinned, checksummed, and content-scanned
before it may enter the library (stored-injection channel, R§1 2606.04425);
flagged payloads are disposed ``quarantined``. Pull receipts are provenance
entries in the library index — never a standalone artifact kind.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from fi.simulate.simulation.models import (
    Persona,
    PersonaIdentity,
    PersonaProvenance,
    Scenario,
)

from ._library import (
    quarantine_payload,
    record_pull_receipt,
    save_persona,
    save_scenario,
)
from ._scan import DownloadRejected, scan_content

PERSONA_DOWNLOAD_PIN_FIELDS = (
    "source", "source_id", "source_updated_at", "downloaded_at",
    "checksum_sha256", "content_scan",
)

_PERSONA_PATHS = {
    "all": "/simulate/api/personas/",
    "system": "/simulate/api/personas/system/",
    "workspace": "/simulate/api/personas/workspace/",
}
_SCENARIO_PATH = "/simulate/scenarios/"

# Platform text-style/speech knobs carried verbatim (§6.3): NO dial mapping
# at pull time in v1 (a dial without a shipped realization metric does not
# ship — ARCH Decision 4).
_STYLE_LIST_FIELDS = ("personality", "communication_style")
_STYLE_TEXT_FIELDS = (
    "tone", "verbosity", "punctuation", "slang_usage", "filler_words",
)
_DEMOGRAPHIC_FIELDS = ("gender", "age_group", "occupation", "location")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _config(config: Optional[Any]) -> Any:
    if config is not None:
        return config
    from fi.alk.config import AgentLearningConfig, current_config, get_api_key

    cfg = AgentLearningConfig.from_env()
    if not cfg.api_key:
        configured = current_config()
        if configured.api_key:
            return configured
        # raises the canonical missing-key message (config.py) — the CLI
        # surfaces it verbatim, never a traceback.
        get_api_key(required=True)
    return cfg


def _headers(config: Any) -> Dict[str, str]:
    # byte-matching the vendored client precedent (fi/api/auth.py:133-134)
    return {
        "X-Api-Key": str(config.api_key),
        "X-Secret-Key": str(config.secret_key or config.api_key),
        "Accept": "application/json",
    }


def _get_json(url: str, headers: Mapping[str, str], *, timeout: float = 30.0) -> Any:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — https/account URL from config
        return json.loads(response.read().decode("utf-8"))


def _rows(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload]
    if isinstance(payload, Mapping):
        for key in ("results", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [dict(item) for item in value]
    return []


def _field(payload: Mapping[str, Any], snake: str) -> Any:
    """snake_case expected (P-G §2), camelCase tolerated (CloudEngine precedent)."""
    if snake in payload:
        return payload[snake]
    camel = snake.split("_")[0] + "".join(
        part.title() for part in snake.split("_")[1:]
    )
    return payload.get(camel)


def checksum_payload(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_download(
    payload: Mapping[str, Any],
    *,
    source: str = "api.futureagi.com",
) -> Dict[str, Any]:
    """PURE (no network): scan + pin a raw platform payload.

    Returns the pin block; raises ``DownloadRejected`` (disposition
    ``quarantined``) when the content scan flags the payload."""
    scan = scan_content(payload)
    pin = {
        "source": source,                              # never the full keyed URL
        "source_id": str(_field(payload, "id") or _field(payload, "platform_id") or ""),
        "source_updated_at": _field(payload, "updated_at"),
        "downloaded_at": _now(),
        "checksum_sha256": checksum_payload(payload),
        "content_scan": scan,
    }
    if scan["status"] == "flagged":
        raise DownloadRejected(
            "downloaded artifact is scan-flagged (stored-injection channel); "
            "envelope disposition quarantined",
            findings=scan["findings"],
            pin=pin,
        )
    return pin


def verify_pin(payload: Mapping[str, Any], pin: Mapping[str, Any]) -> Dict[str, Any]:
    """Re-validate a pinned payload: tampered = checksum mismatch; unpinned =
    missing pin fields — both non-admissible (PRD §4.4)."""
    missing = [field for field in PERSONA_DOWNLOAD_PIN_FIELDS if field not in pin]
    if missing:
        return {
            "status": "unpinned",
            "admissible": False,
            "errors": [f"missing pin field: {field}" for field in missing],
        }
    actual = checksum_payload(payload)
    if actual != pin["checksum_sha256"]:
        return {
            "status": "tampered",
            "admissible": False,
            "errors": [
                f"sha256 mismatch vs pin (expected {pin['checksum_sha256'][:8]}…, "
                f"found {actual[:8]}…)"
            ],
        }
    return {"status": "ok", "admissible": True, "errors": []}


def map_platform_persona(
    payload: Mapping[str, Any],
    *,
    pin: Mapping[str, Any],
) -> Persona:
    """Platform persona ontology -> kit Persona (§6.3): demographics-as-lists
    -> identity.demographics (lint-flagged); personality/communication_style
    + text-style knobs -> identity.style_notes verbatim; additional
    instruction -> identity.summary; full payload at provenance.raw."""
    demographics: Dict[str, Any] = {}
    for field in _DEMOGRAPHIC_FIELDS:
        value = _field(payload, field)
        if value:
            demographics[field] = value
    style_notes: List[str] = []
    for field in _STYLE_LIST_FIELDS:
        value = _field(payload, field)
        if isinstance(value, list):
            style_notes.extend(f"{field}: {item}" for item in value)
        elif value:
            style_notes.append(f"{field}: {value}")
    for field in _STYLE_TEXT_FIELDS:
        value = _field(payload, field)
        if value:
            style_notes.append(f"{field}: {value}")
    name = _field(payload, "name")
    identity = PersonaIdentity(
        name=str(name) if name else None,
        summary=(
            str(_field(payload, "additional_instruction"))
            if _field(payload, "additional_instruction") else None
        ),
        demographics=demographics,
        style_notes=style_notes,
    )
    provenance = PersonaProvenance(
        evidence_class="cloud_downloaded",
        source_format="futureagi",
        raw=json.dumps(payload, sort_keys=True, default=str),
        pin=dict(pin),
    )
    embedded = {
        key: value for key, value in payload.items()
        if isinstance(key, str)
    }
    return Persona(
        persona=embedded,                      # speech/voice fields verbatim (Phase 9)
        situation=str(
            _field(payload, "description") or "Pulled platform persona session."
        ),
        outcome="The conversation completes naturally.",
        identity=identity,
        provenance=provenance,
    )


def pull_personas(
    *,
    scope: str = "all",
    ids: Optional[Sequence[str]] = None,
    page_size: int = 50,
    library: Union[str, Any, None] = None,
    config: Optional[Any] = None,
    list_only: bool = False,
) -> Dict[str, Any]:
    """Read-only persona pull (keyed, explicit). org/workspace resolved
    server-side from the key pair; paginated list + per-id detail reads."""
    cfg = _config(config)
    headers = _headers(cfg)
    base = str(cfg.api_url).rstrip("/")
    host = urllib.parse.urlsplit(base).netloc or base

    if ids:
        payloads = [
            _get_json(f"{base}{_PERSONA_PATHS['all']}{identifier}/", headers)
            for identifier in ids
        ]
    else:
        path = _PERSONA_PATHS.get(scope, _PERSONA_PATHS["all"])
        listing = _get_json(
            f"{base}{path}?page_size={int(page_size)}", headers
        )
        payloads = _rows(listing)

    if list_only:
        return {
            "status": "listed",
            "exit_code": 0,
            "personas": [
                {
                    "platform_id": str(_field(item, "id") or ""),
                    "name": _field(item, "name"),
                    "updated_at": _field(item, "updated_at"),
                }
                for item in payloads
            ],
            "summary": {"visible": len(payloads)},
        }

    pulled: List[Dict[str, Any]] = []
    quarantined: List[Dict[str, Any]] = []
    for payload in payloads:
        try:
            pin = validate_download(payload, source=host)
        except DownloadRejected as rejection:
            entry = {
                "platform_id": str(_field(payload, "id") or ""),
                "content_scan": {"status": "flagged", "findings": rejection.findings},
            }
            if library is not None:
                path = quarantine_payload(
                    f"persona-{entry['platform_id'] or 'unknown'}",
                    dict(payload),
                    rejection.findings,
                    library=library,
                )
                entry["quarantine_file"] = str(path)
            quarantined.append(entry)
            continue
        persona = map_platform_persona(payload, pin=pin)
        entry = {
            "platform_id": pin["source_id"],
            "persona_version": persona.content_hash(),
            "pin": pin,
            "content_scan": pin["content_scan"],
        }
        if library is not None:
            saved = save_persona(persona, library=library)
            entry["local_file"] = saved["path"]
            receipt = record_pull_receipt(
                {
                    "artifact": "persona",
                    "platform_id": pin["source_id"],
                    "source": pin["source"],
                    "source_updated_at": pin["source_updated_at"],
                    "checksum_sha256": pin["checksum_sha256"],
                    "downloaded_at": pin["downloaded_at"],
                    "ref": saved["ref"],
                },
                library=library,
            )
            entry["receipt"] = receipt
        pulled.append(entry)

    status = "pulled" if pulled and not quarantined else (
        "quarantined" if quarantined else "empty"
    )
    return {
        "status": status,
        "exit_code": 1 if quarantined else 0,
        "pulled": pulled,
        "quarantined": quarantined,
        "summary": {"pulled": len(pulled), "quarantined": len(quarantined)},
    }


def _scenario_rows(
    base: str,
    headers: Mapping[str, str],
    identifier: str,
    detail: Mapping[str, Any],
) -> Dict[str, Any]:
    """Dataset-row composition (BUILD §6.2): prefer the ``/export/`` payload
    when the endpoint exists; else rows embedded on the detail read;
    ``rows_available: false`` is a legal recorded pull state."""
    try:
        export = _get_json(f"{base}{_SCENARIO_PATH}{identifier}/export/", headers)
        rows = _rows(export) or _rows(export.get("dataset", {})) if isinstance(export, Mapping) else _rows(export)
        if rows:
            return {"rows_available": True, "rows": rows, "rows_source": "export"}
    except (urllib.error.HTTPError, urllib.error.URLError):
        pass
    for key in ("dataset_rows", "rows"):
        value = detail.get(key)
        if isinstance(value, list) and value:
            return {"rows_available": True, "rows": [dict(r) for r in value], "rows_source": key}
    return {"rows_available": False, "rows": [], "rows_source": None}


def _compose_dataset_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    if {"persona", "situation", "outcome"} <= set(row):
        return dict(row)
    return {
        "persona": dict(row),
        "situation": str(row.get("situation") or "Pulled scenario row."),
        "outcome": str(row.get("outcome") or "The task completes successfully."),
    }


def pull_scenarios(
    *,
    ids: Optional[Sequence[str]] = None,
    library: Union[str, Any, None] = None,
    config: Optional[Any] = None,
) -> Dict[str, Any]:
    """SDK-only scenario pull (the CLI canon has no ``scenario pull`` in v1).

    Composes scenario + linked-Dataset rows + persona reads (soft link via
    ``metadata.persona_ids``) client-side. Pulled scenarios stay kind=None —
    legacy untyped is NEVER silently retyped (ARCH §2a)."""
    cfg = _config(config)
    headers = _headers(cfg)
    base = str(cfg.api_url).rstrip("/")
    host = urllib.parse.urlsplit(base).netloc or base

    if ids:
        details = [
            _get_json(f"{base}{_SCENARIO_PATH}{identifier}/", headers)
            for identifier in ids
        ]
    else:
        details = _rows(_get_json(f"{base}{_SCENARIO_PATH}", headers))

    pulled: List[Dict[str, Any]] = []
    quarantined: List[Dict[str, Any]] = []
    for detail in details:
        identifier = str(_field(detail, "id") or "")
        try:
            pin = validate_download(detail, source=host)
        except DownloadRejected as rejection:
            entry = {"platform_id": identifier, "content_scan": {
                "status": "flagged", "findings": rejection.findings,
            }}
            if library is not None:
                path = quarantine_payload(
                    f"scenario-{identifier or 'unknown'}",
                    dict(detail), rejection.findings, library=library,
                )
                entry["quarantine_file"] = str(path)
            quarantined.append(entry)
            continue
        rows_block = _scenario_rows(base, headers, identifier, detail)
        persona_ids = []
        metadata = detail.get("metadata")
        if isinstance(metadata, Mapping):
            persona_ids = list(metadata.get("persona_ids") or [])
        linked_personas = []
        for persona_id in persona_ids:
            try:
                linked_personas.append(
                    _get_json(f"{base}{_PERSONA_PATHS['all']}{persona_id}/", headers)
                )
            except (urllib.error.HTTPError, urllib.error.URLError):
                continue
        dataset = [
            _compose_dataset_row(row) for row in rows_block["rows"]
        ] or [{
            "persona": {"name": str(_field(detail, "name") or "Pulled Persona")},
            "situation": str(_field(detail, "description") or "Pulled scenario."),
            "outcome": "The task completes successfully.",
        }]
        scenario = Scenario(
            name=str(_field(detail, "name") or f"pulled-scenario-{identifier}"),
            description=(
                str(_field(detail, "description"))
                if _field(detail, "description") else None
            ),
            dataset=dataset,
        )
        entry: Dict[str, Any] = {
            "platform_id": identifier,
            "scenario_version": scenario.content_hash(),
            "rows_available": rows_block["rows_available"],
            "rows_source": rows_block["rows_source"],
            "linked_personas": len(linked_personas),
            "pin": pin,
        }
        if library is not None:
            saved = save_scenario(scenario, library=library)
            entry["local_file"] = saved["path"]
            entry["receipt"] = record_pull_receipt(
                {
                    "artifact": "scenario",
                    "platform_id": identifier,
                    "source": pin["source"],
                    "source_updated_at": pin["source_updated_at"],
                    "checksum_sha256": pin["checksum_sha256"],
                    "downloaded_at": pin["downloaded_at"],
                    "rows_available": rows_block["rows_available"],
                    "ref": saved["ref"],
                },
                library=library,
            )
        pulled.append(entry)

    status = "pulled" if pulled and not quarantined else (
        "quarantined" if quarantined else "empty"
    )
    return {
        "status": status,
        "exit_code": 1 if quarantined else 0,
        "pulled": pulled,
        "quarantined": quarantined,
        "summary": {"pulled": len(pulled), "quarantined": len(quarantined)},
    }


__all__ = [
    "PERSONA_DOWNLOAD_PIN_FIELDS",
    "checksum_payload",
    "map_platform_persona",
    "pull_personas",
    "pull_scenarios",
    "validate_download",
    "verify_pin",
]
