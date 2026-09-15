"""Durable, replayable harness usage reporting for one hosted attempt."""

from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .outbound import HostedCapabilities, Transport, TransportError

USAGE_SCHEMA_VERSION = "futureagi.harness-usage.v1"
SIMULATOR_FUNDING_ALIAS = "ALK_SIMULATOR_FUNDING"
UsageAction = Literal["text_call", "voice_call", "managed_evaluation"]
Funding = Literal["platform", "customer"]


class UsageUnavailable(RuntimeError):
    """A paid action could not be authorized because the usage service was unavailable."""


class UsageDenied(RuntimeError):
    """The platform rejected a paid action for insufficient usage allowance."""

    def __init__(self, detail: dict[str, Any] | None = None) -> None:
        self.detail = detail or {}
        nested = self.detail.get("result")
        effective = nested if isinstance(nested, dict) else self.detail
        super().__init__(
            str(
                effective.get("reason")
                or effective.get("message")
                or "usage limit reached"
            )
        )


class UsageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: uuid.UUID
    action: UsageAction
    scenario_key: str = Field(min_length=1)
    amount: float = Field(ge=0, allow_inf_nan=False)
    occurred_at: datetime
    funding: Funding
    infra_failed: bool = False
    model: str | None = None

    @model_validator(mode="after")
    def _managed_model(self) -> "UsageRecord":
        if self.action == "managed_evaluation" and not self.model:
            raise ValueError("managed evaluation usage requires a model")
        return self


class UsageJournal:
    """An atomic cumulative snapshot whose immutable records are safe to replay."""

    def __init__(self, path: Path, *, attempt_id: str) -> None:
        self.path = path
        self.attempt_id = attempt_id
        self._lock = threading.RLock()
        self._started = datetime.now(timezone.utc)
        self._elapsed_before = 0.0
        self._records: list[UsageRecord] = []
        self._load()
        self._flush()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            raise UsageUnavailable(f"usage journal unreadable: {exc}") from exc
        if (
            not isinstance(raw, dict)
            or raw.get("schema_version") != USAGE_SCHEMA_VERSION
        ):
            raise UsageUnavailable("usage journal schema is unsupported")
        loaded: list[UsageRecord] = []
        for item in raw.get("records", []):
            try:
                loaded.append(UsageRecord.model_validate(item))
            except (TypeError, ValueError) as exc:
                raise UsageUnavailable(f"usage journal record invalid: {exc}") from exc
        self._records = loaded
        try:
            sandbox_seconds = float(raw.get("sandbox_seconds", 0))
        except (TypeError, ValueError):
            sandbox_seconds = 0
        if math.isfinite(sandbox_seconds) and sandbox_seconds >= 0:
            self._elapsed_before = sandbox_seconds

    @property
    def records(self) -> tuple[UsageRecord, ...]:
        with self._lock:
            return tuple(self._records)

    def append(
        self,
        *,
        action: UsageAction,
        scenario_key: str,
        amount: float,
        funding: Funding,
        infra_failed: bool = False,
        occurred_at: datetime | None = None,
        record_key: str | None = None,
        model: str | None = None,
    ) -> UsageRecord:
        if not math.isfinite(amount) or amount < 0:
            raise ValueError("usage amount must be nonnegative and finite")
        if action == "managed_evaluation" and not model:
            raise ValueError("managed evaluation usage requires a model")
        with self._lock:
            if record_key is None:
                ordinal = 1 + sum(
                    record.action == action and record.scenario_key == scenario_key
                    for record in self._records
                )
                record_key = str(ordinal)
            record_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"futureagi:harness-usage:{self.attempt_id}:{action}:{scenario_key}:{record_key}",
            )
            for existing in self._records:
                if existing.id != record_id:
                    continue
                if (
                    existing.action != action
                    or existing.scenario_key != scenario_key
                    or existing.amount != amount
                    or existing.funding != funding
                    or existing.infra_failed != infra_failed
                    or existing.model != model
                ):
                    raise UsageUnavailable(
                        f"usage record {record_id} was replayed with different facts"
                    )
                return existing
            record = UsageRecord(
                id=record_id,
                action=action,
                scenario_key=scenario_key,
                amount=amount,
                occurred_at=occurred_at or datetime.now(timezone.utc),
                funding=funding,
                infra_failed=infra_failed,
                model=model,
            )
            self._records.append(record)
            self._flush()
            return record

    def payload(self) -> dict[str, Any]:
        with self._lock:
            records = [record.model_dump(mode="json") for record in self._records]
            text_tokens = sum(
                record.amount
                for record in self._records
                if record.action == "text_call"
                and record.funding == "platform"
                and not record.infra_failed
            )
            voice_minutes = sum(
                record.amount
                for record in self._records
                if record.action == "voice_call" and not record.infra_failed
            )
        elapsed = self._elapsed_before + max(
            0.0, (datetime.now(timezone.utc) - self._started).total_seconds()
        )
        return {
            "schema_version": USAGE_SCHEMA_VERSION,
            "operation": "report",
            "records": records,
            "totals": {
                "text_sim_tokens": text_tokens,
                "voice_sim_minutes": voice_minutes,
            },
            "sandbox_seconds": elapsed,
        }

    def checkpoint(self) -> None:
        with self._lock:
            self._flush()

    def _flush(self) -> None:
        payload = self.payload()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w",
            dir=self.path.parent,
            prefix=".usage-",
            suffix=".json",
            delete=False,
            encoding="utf-8",
        )
        temporary = Path(handle.name)
        try:
            with handle:
                json.dump(
                    payload,
                    handle,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)


def simulator_funding(environ: Mapping[str, str] | None = None) -> Funding:
    value = (environ or os.environ).get(SIMULATOR_FUNDING_ALIAS, "").strip().lower()
    return "platform" if value == "platform" else "customer"


def _usage_endpoint(scenarios_endpoint: str) -> str:
    parsed = urlsplit(scenarios_endpoint)
    parts = parsed.path.rstrip("/").split("/")
    if not parts or parts[-1] != "scenarios":
        raise UsageUnavailable("cannot derive usage endpoint from scenarios capability")
    parts[-1] = "usage"
    return urlunsplit(parsed._replace(path="/".join(parts) + "/"))


class UsageReporter:
    def __init__(
        self,
        capabilities: HostedCapabilities,
        transport: Transport,
        journal: UsageJournal,
    ) -> None:
        self._capabilities = capabilities
        self._transport = transport
        self.journal = journal
        self._endpoint = _usage_endpoint(capabilities.endpoints.scenarios)
        self._network_lock = threading.RLock()

    def check(
        self,
        action: UsageAction,
        *,
        amount: float | None = None,
        model: str | None = None,
    ) -> None:
        body: dict[str, Any] = {"operation": "check", "action": action}
        if amount is not None:
            body["amount"] = amount
        if model is not None:
            body["model"] = model
        try:
            with self._network_lock:
                response = self._transport.request(
                    "POST",
                    self._endpoint,
                    headers=self._capabilities.auth_headers(),
                    json_body=body,
                )
        except TransportError as exc:
            raise UsageUnavailable(f"usage check unavailable: {exc}") from exc
        body = response.body
        result = (
            body.get("result")
            if isinstance(body, dict) and isinstance(body.get("result"), dict)
            else body
        )
        if response.status_code == 402:
            raise UsageDenied(body)
        if response.status_code < 200 or response.status_code >= 300:
            raise UsageUnavailable(
                f"usage check failed with HTTP {response.status_code}"
            )
        if not isinstance(result, dict) or result.get("allowed") is not True:
            raise UsageUnavailable("usage check response did not allow the action")

    def record(
        self,
        *,
        action: UsageAction,
        scenario_key: str,
        amount: float,
        funding: Funding,
        infra_failed: bool = False,
        occurred_at: datetime | None = None,
        record_key: str | None = None,
        model: str | None = None,
    ) -> UsageRecord:
        record = self.journal.append(
            record_key=record_key,
            action=action,
            scenario_key=scenario_key,
            amount=amount,
            funding=funding,
            infra_failed=infra_failed,
            occurred_at=occurred_at,
            model=model,
        )
        self.report()
        return record

    def report(self) -> bool:
        self.journal.checkpoint()
        try:
            with self._network_lock:
                response = self._transport.request(
                    "POST",
                    self._endpoint,
                    headers=self._capabilities.auth_headers(),
                    json_body=self.journal.payload(),
                )
        except TransportError:
            return False
        return 200 <= response.status_code < 300


_active_reporter: UsageReporter | None = None


def configure_reporter(reporter: UsageReporter | None) -> None:
    global _active_reporter
    _active_reporter = reporter


def active_reporter() -> UsageReporter | None:
    return _active_reporter
