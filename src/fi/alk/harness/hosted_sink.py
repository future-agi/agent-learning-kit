"""Guest-side forwarder for the v1.6 hosted-harness outbound contract.

The gateway drops two files into the sandbox before launching the entrypoint:

- ``/work/.harness/secrets.json``      ``{alias: value}`` resolved target-provider secrets.
- ``/work/.harness/capabilities.json`` attempt token/fence + ingestion endpoints.

After ``HarnessExecutor`` writes its durable run artifacts under ``--output``, this
module maps them onto the platform ingestion API (``outbound-channels.md``):

    POST <events>     harness journal -> typed lifecycle events (log/*) + a terminal event
    POST <scenarios>  generated personas -> provision + begin (scenario ids)
    POST <results>    SimulationReport cases -> result receipts
    POST <artifacts>  manifest + PUT <artifacts>/<digest>

The platform only accepts a fixed set of event types and validates a per-type
payload plus a payload digest, so the harness's rich CanonicalEvent stream is
forwarded as ``log`` events and the run outcome is delivered as the required
``terminal`` event. Digests use the platform's exact canonicalization
(sort_keys, compact separators, ensure_ascii=False) over the payload (events) or
the body-minus-digest (results/manifest).
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

_SECRETS_PATH = Path("/work/.harness/secrets.json")
_CAPABILITIES_PATH = Path("/work/.harness/capabilities.json")
_EVENT_SCHEMA = "futureagi.harness-event.v1"
_RESULT_SCHEMA = "futureagi.harness-result.v1"
_MANIFEST_SCHEMA = "futureagi.harness-manifest.v1"
_HTTP_TIMEOUT = 30.0
_EVENT_BATCH = 100
_TERMINAL_STAGES = {"completed", "failed", "canceled"}
_GOOGLE_SA_ALIAS = "GOOGLE_APPLICATION_CREDENTIALS_JSON"
_GOOGLE_SA_PATH = Path("/work/.harness/vertex-sa.json")

_STATUS_MAP = {
    "passed": "passed",
    "finished": "passed",
    "completed": "passed",
    "failed": "failed",
    "gave-up": "failed",
    "gave_up": "failed",
    "ran-out-of-turns": "failed",
    "errored": "errored",
    "error": "errored",
    "timed_out": "errored",
    "skipped": "skipped",
}


# --------------------------------------------------------------------------- #
# secrets + capabilities
# --------------------------------------------------------------------------- #
def load_secrets(path: Path = _SECRETS_PATH) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def inject_secrets(secrets: dict[str, str]) -> list[str]:
    """Export resolved secrets as environment variables for the harness stack.

    Aliases are provider env-var names (e.g. ``LIVEKIT_API_KEY``). A full
    service-account JSON is written to a file and referenced via
    GOOGLE_APPLICATION_CREDENTIALS so Vertex/Google SDKs (incl. Claude on Vertex)
    can authenticate.
    """
    injected: list[str] = []
    for alias, value in secrets.items():
        if alias == _GOOGLE_SA_ALIAS:
            _GOOGLE_SA_PATH.parent.mkdir(parents=True, exist_ok=True)
            _GOOGLE_SA_PATH.write_text(value, encoding="utf-8")
            try:
                _GOOGLE_SA_PATH.chmod(0o600)
            except OSError:
                pass
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(_GOOGLE_SA_PATH)
            injected.append("GOOGLE_APPLICATION_CREDENTIALS(file)")
            continue
        os.environ[alias] = value
        injected.append(alias)
    return injected


def load_capabilities(path: Path = _CAPABILITIES_PATH) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


# --------------------------------------------------------------------------- #
# canonicalization (must match simulate.services.hosted_harness.canonical_*)
# --------------------------------------------------------------------------- #
def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def canonical_digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    if isinstance(value, str) and value:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return value
        except ValueError:
            pass
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def _event_id(raw: str) -> str:
    if raw and len(raw) <= 64 and all(c.isalnum() or c in "_-" for c in raw):
        return raw
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]


# --------------------------------------------------------------------------- #
# sink
# --------------------------------------------------------------------------- #
class HostedHarnessSink:
    def __init__(self, capabilities: dict[str, Any]) -> None:
        self.capabilities = capabilities
        self.job_id = str(capabilities["job_id"])
        self.attempt_id = str(capabilities["attempt_id"])
        self.attempt_number = int(capabilities["attempt_number"])
        endpoints = capabilities["endpoints"]
        self.events_url = endpoints["events"]
        self.results_url = endpoints["results"]
        self.scenarios_url = endpoints["scenarios"]
        self.artifacts_url = endpoints["artifacts"].rstrip("/")
        self._client = httpx.Client(
            timeout=_HTTP_TIMEOUT,
            headers={
                "Authorization": f"Bearer {capabilities['token']}",
                "X-Harness-Fence": str(capabilities["fence"]),
            },
        )

    def close(self) -> None:
        self._client.close()

    def _envelope(self, seq: int, emitted_at: str, stage: str, etype: str,
                  payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "event_id": _event_id(f"{self.attempt_id}:{seq}"),
            "job_id": self.job_id,
            "attempt_id": self.attempt_id,
            "attempt_number": self.attempt_number,
            "sequence": seq,
            "emitted_at": emitted_at,
            "stage": stage[:64],
            "type": etype,
            "payload": payload,
            "digest": canonical_digest(payload),
        }

    def forward_events(self, run_dir: Path, terminal_stage: str) -> dict[str, Any]:
        """Forward the harness journal as ``log`` events + a ``terminal`` event.

        The platform only accepts a fixed event vocabulary, so each harness
        CanonicalEvent becomes a ``log`` event and the run outcome is delivered
        as the required ``terminal`` event (last sequence).
        """
        journal = run_dir / "harness-events.jsonl"
        mapped: list[dict[str, Any]] = []
        seq = 0
        if journal.is_file():
            for line in journal.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except ValueError:
                    continue
                seq += 1
                message = str(raw.get("type") or "harness.event")
                detail = raw.get("payload")
                if detail is not None:
                    message = f"{message} {json.dumps(detail, default=str)[:800]}"
                payload = {"level": "info", "message": message[:4000]}
                mapped.append(
                    self._envelope(seq, _iso(raw.get("wall_time")), "running", "log", payload)
                )
        # Required terminal event.
        seq += 1
        term_stage = terminal_stage if terminal_stage in _TERMINAL_STAGES else "failed"
        term_payload = {"stage": term_stage, "reason": None}
        mapped.append(
            self._envelope(
                seq, datetime.now(timezone.utc).isoformat(), term_stage, "terminal", term_payload
            )
        )

        acked = 0
        for start in range(0, len(mapped), _EVENT_BATCH):
            chunk = mapped[start : start + _EVENT_BATCH]
            resp = self._client.post(
                self.events_url,
                json={"schema_version": _EVENT_SCHEMA, "events": chunk},
            )
            resp.raise_for_status()
            body = resp.json()
            acked = body.get("acked_through_sequence", acked)
            if body.get("rejected"):
                return {"channel": "events", "status": "partial", "count": len(mapped),
                        "acked_through_sequence": acked, "rejected": body["rejected"][:5]}
        return {"channel": "events", "status": "ok", "count": len(mapped),
                "acked_through_sequence": acked}

    def provision_scenarios(self, run_dir: Path) -> dict[str, Any]:
        index = run_dir / "scenarios.json"
        if not index.is_file():
            return {"channel": "scenarios", "status": "absent", "ids": {}}
        try:
            listing = json.loads(index.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"channel": "scenarios", "status": "unreadable", "ids": {}}
        entries = listing if isinstance(listing, list) else listing.get("scenarios", [])
        personas = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            key = str(entry.get("name") or entry.get("scenario_key") or "")
            if not key:
                continue
            personas.append(
                {
                    "scenario_key": key,
                    "name": str(entry.get("name") or key)[:255],
                    "situation": str(entry.get("instruction") or entry.get("situation") or ""),
                    "outcome": str(entry.get("outcome") or ""),
                    "persona": entry,
                }
            )
        if not personas:
            return {"channel": "scenarios", "status": "empty", "ids": {}}
        name = listing.get("name", "hosted-harness") if isinstance(listing, dict) else "hosted-harness"
        provision = self._client.post(
            self.scenarios_url,
            json={"operation": "provision", "name": name, "modality": "voice",
                  "personas": personas},
        )
        provision.raise_for_status()
        result = provision.json().get("result", {})
        ids = {row["scenario_key"]: row["scenario_id"] for row in result.get("scenarios", [])}
        run_test_id = result.get("run_test_id")
        if run_test_id and ids:
            begin = self._client.post(
                self.scenarios_url,
                json={"operation": "begin", "run_test_id": run_test_id,
                      "scenario_keys": list(ids)},
            )
            begin.raise_for_status()
        return {"channel": "scenarios", "status": "ok", "ids": ids, "run_test_id": run_test_id}

    def forward_results(self, run_dir: Path, scenario_ids: dict[str, str]) -> dict[str, Any]:
        report_path = run_dir / "report.json"
        if not report_path.is_file():
            return {"channel": "results", "status": "absent"}
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"channel": "results", "status": "unreadable"}
        cases = report.get("test_cases") or report.get("cases") or []
        posted = 0
        errors: list[str] = []
        for index, case in enumerate(cases):
            if not isinstance(case, dict):
                continue
            key = str(case.get("scenario_key") or case.get("scenario") or case.get("name") or "")
            scenario_id = scenario_ids.get(key)
            if not scenario_id:
                errors.append(f"no scenario_id for {key or index}")
                continue
            try:
                receipt = self._result_receipt(case, key, scenario_id)
                resp = self._client.post(self.results_url, json=receipt)
                resp.raise_for_status()
                posted += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{key or index}: {type(exc).__name__}: {exc}")
        return {"channel": "results", "status": "ok", "posted": posted, "errors": errors[:5]}

    def _result_receipt(self, case: dict[str, Any], key: str, scenario_id: str) -> dict[str, Any]:
        status = _STATUS_MAP.get(str(case.get("status") or "failed").lower(), "failed")
        sub_goals = [
            {
                "name": str(goal.get("name") or "goal")[:255],
                "held": goal.get("held"),
                "reason": goal.get("reason"),
                "judged": bool(goal.get("judged", True)),
            }
            for goal in (case.get("sub_goals") or [])
            if isinstance(goal, dict)
        ]
        evaluations = []
        for ev in case.get("evaluations") or case.get("checks") or []:
            if not isinstance(ev, dict):
                continue
            if ev.get("kind") == "metric" or "score" in ev:
                evaluations.append({"name": str(ev.get("name") or "metric")[:255],
                                    "kind": "metric", "score": float(ev.get("score", 0.0)),
                                    "reason": ev.get("reason")})
            else:
                evaluations.append({"name": str(ev.get("name") or "checkpoint")[:255],
                                    "kind": "checkpoint", "passed": bool(ev.get("passed", False)),
                                    "reason": ev.get("reason")})
        failure = None
        if status == "errored":
            raw = case.get("failure") or {}
            failure = {
                "domain": str(raw.get("domain") or "infrastructure"),
                "stage": str(raw.get("stage") or "running")[:64],
                "code": str(raw.get("code") or "errored")[:128],
                "message": str(raw.get("message") or "scenario errored")[:2000],
            }
        skipped = status == "skipped"
        receipt = {
            "schema_version": _RESULT_SCHEMA,
            "job_id": self.job_id,
            "attempt_id": self.attempt_id,
            "attempt_number": self.attempt_number,
            "scenario_key": key,
            "scenario_id": scenario_id,
            "scenario_attempt": 1,
            "world_index": None if skipped else int(case.get("world_index") or 0),
            "status": status,
            "sub_goals": [] if skipped else sub_goals,
            "evaluations": [] if skipped else evaluations,
            "call": None if skipped else self._call_block(case),
            "failure": failure,
        }
        receipt["digest"] = canonical_digest({k: v for k, v in receipt.items() if k != "digest"})
        return receipt

    @staticmethod
    def _call_block(case: dict[str, Any]) -> dict[str, Any] | None:
        started, ended = case.get("started_at"), case.get("ended_at")
        if not started or not ended:
            return None
        try:
            start_dt = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(str(ended).replace("Z", "+00:00"))
            duration_ms = max(int((end_dt - start_dt).total_seconds() * 1000), 0)
        except ValueError:
            return None
        result = case.get("result") or {}
        messages = result.get("messages") if isinstance(result, dict) else None
        turns = len(messages) if isinstance(messages, list) else int(case.get("turns") or 0)
        return {"started_at": _iso(start_dt), "ended_at": _iso(end_dt),
                "duration_ms": duration_ms, "turns": turns, "recording_artifacts": []}

    def upload_artifacts(self, run_dir: Path) -> dict[str, Any]:
        manifest_path = run_dir / "artifact-manifest.json"
        if not manifest_path.is_file():
            return {"channel": "artifacts", "status": "absent"}
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"channel": "artifacts", "status": "unreadable"}
        uploaded = 0
        entries: list[dict[str, Any]] = []
        errors: list[str] = []
        for record in manifest.get("files") or []:
            if not isinstance(record, dict):
                continue
            digest = str(record.get("sha256") or "")
            rel = str(record.get("path") or "")
            if not digest or not rel:
                continue
            local = run_dir / rel
            if not local.is_file():
                continue
            kind = str(record.get("kind") or "other")
            size = int(record.get("size") or local.stat().st_size)
            try:
                headers = {
                    "X-Artifact-Size": str(size),
                    "X-Artifact-Kind": kind,
                    "Content-Type": str(record.get("media_type") or "application/octet-stream"),
                }
                if record.get("scenario"):
                    headers["X-Scenario-Key"] = str(record["scenario"])
                resp = self._client.put(
                    f"{self.artifacts_url}/{digest}", content=local.read_bytes(), headers=headers
                )
                resp.raise_for_status()
                uploaded += 1
                entry = {"artifact_id": f"sha256:{digest}", "kind": kind, "size": size}
                if record.get("scenario"):
                    entry["scenario_key"] = str(record["scenario"])
                entries.append(entry)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{rel}: {type(exc).__name__}: {exc}")
        body = {
            "schema_version": _MANIFEST_SCHEMA,
            "job_id": self.job_id,
            "attempt_id": self.attempt_id,
            "attempt_number": self.attempt_number,
            "entries": entries,
            "complete": not errors,
        }
        body["digest"] = canonical_digest({k: v for k, v in body.items() if k != "digest"})
        try:
            resp = self._client.post(f"{self.artifacts_url}/manifest", json=body)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"manifest: {type(exc).__name__}: {exc}")
        return {"channel": "artifacts", "status": "ok", "uploaded": uploaded, "errors": errors[:5]}


def locate_run_dir(output: Path) -> Path | None:
    output = output.expanduser().resolve()
    for marker in ("report.json", "harness-events.jsonl", "scenarios.json"):
        matches = sorted(output.rglob(marker), key=lambda p: p.stat().st_mtime, reverse=True)
        if matches:
            return matches[0].parent
    return output if output.is_dir() else None


def forward_all(output: Path, capabilities: dict[str, Any],
                terminal_stage: str = "failed") -> dict[str, Any]:
    """Forward every durable channel; isolate per-channel failures."""
    run_dir = locate_run_dir(output)
    if run_dir is None:
        return {"status": "no_run_dir", "output": str(output)}
    sink = HostedHarnessSink(capabilities)
    report: dict[str, Any] = {"run_dir": str(run_dir), "channels": []}
    scenario_ids: dict[str, str] = {}
    try:
        # Scenarios first so result receipts can reference their ids; events
        # (incl. the required terminal) then results then artifacts.
        try:
            prov = sink.provision_scenarios(run_dir)
            report["channels"].append(prov)
            scenario_ids = prov.get("ids") or {}
        except Exception as exc:  # noqa: BLE001
            report["channels"].append({"channel": "scenarios", "status": "error",
                                       "error": f"{type(exc).__name__}: {exc}"})
        for label, fn in (
            ("events", lambda: sink.forward_events(run_dir, terminal_stage)),
            ("results", lambda: sink.forward_results(run_dir, scenario_ids)),
            ("artifacts", lambda: sink.upload_artifacts(run_dir)),
        ):
            try:
                report["channels"].append(fn())
            except Exception as exc:  # noqa: BLE001
                report["channels"].append({"channel": label, "status": "error",
                                           "error": f"{type(exc).__name__}: {exc}"})
    finally:
        sink.close()
    return report
