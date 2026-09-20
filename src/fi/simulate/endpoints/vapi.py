"""Vapi target-agent adapter — capability declaration + Stage 6/8 seam.

The phone leg to a Vapi assistant runs through ``LiveKitAgentEndpoint``
with ``TelephonyTransport(kind="sip_outbound")`` today; this class
exists so a future direct-Vapi execution path (or hosted-runner
selection matrix) can register an adapter with the same shape as the
LiveKit and Retell ones. Post-call evidence still flows through
``fi.simulate.evidence.providers.vapi.VapiEvidenceSource``.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

from fi.simulate.realtime.events import RealtimeEvent
from fi.simulate.realtime.media import AudioFrame
from fi.simulate.runtime.capabilities import EndpointCapabilities

from .base import (
    AgentEndpointManifest,
    DiscoveryRequest,
    DiscoverySnapshot,
    EndpointHandle,
    ReadinessResult,
    ReconciliationResult,
)


class VapiOriginatorConfigError(ValueError):
    pass


@dataclass(frozen=True)
class VapiCall:
    call_id: str
    status: str | None


class VapiCallOriginator:
    """Create an opt-in Vapi call to the LiveKit inbound DID."""

    _base_url = "https://api.vapi.ai"
    _CLEANUP_TIMEOUT_SECONDS = 20.0
    _POLL_INTERVAL_SECONDS = 0.25
    _LIST_LIMIT = 1000

    def __init__(
        self,
        *,
        api_key: str,
        assistant_id: str,
        phone_number_id: str,
        destination: str,
        public_api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._assistant_id = assistant_id
        self._phone_number_id = phone_number_id
        self._destination = destination
        self._public_api_key = (public_api_key or "").strip()
        self._call_marker = "alk-" + uuid.uuid4().hex
        self._start_attempted = False
        self._creation_attempted = False
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._client = client or httpx.AsyncClient(
            base_url=os.environ.get("VAPI_API_BASE_URL", self._base_url),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
        self._owns_client = client is None

    @classmethod
    def from_env(cls) -> "VapiCallOriginator":
        names = (
            "VAPI_API_KEY",
            "VAPI_ASSISTANT_ID",
            "VAPI_PHONE_NUMBER_ID",
            "LIVEKIT_INBOUND_DID",
        )
        values = {name: os.environ.get(name, "").strip() for name in names}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ValueError(
                "vapi_originator_config_missing: " + ", ".join(sorted(missing))
            )
        return cls(
            api_key=values["VAPI_API_KEY"],
            assistant_id=values["VAPI_ASSISTANT_ID"],
            phone_number_id=values["VAPI_PHONE_NUMBER_ID"],
            destination=values["LIVEKIT_INBOUND_DID"],
            public_api_key=os.environ.get("VAPI_PUBLIC_API_KEY"),
        )

    async def start(self) -> VapiCall:
        if self._start_attempted:
            raise ValueError("vapi_originator_already_started")
        self._start_attempted = True
        assistant_response = await self._client.get(
            f"/assistant/{self._assistant_id}",
            headers=self._headers,
            follow_redirects=False,
        )
        assistant_response.raise_for_status()
        assistant = assistant_response.json()
        if not isinstance(assistant, dict):
            raise VapiOriginatorConfigError("vapi_assistant_response_invalid")
        monitor_plan = assistant.get("monitorPlan")
        if monitor_plan is None:
            monitor_plan = {}
        if not isinstance(monitor_plan, dict):
            raise VapiOriginatorConfigError("vapi_monitor_plan_invalid")
        authenticated = monitor_plan.get("controlAuthenticationEnabled", False)
        if type(authenticated) is not bool:
            raise VapiOriginatorConfigError("vapi_control_authentication_invalid")
        if authenticated and not self._public_api_key:
            raise VapiOriginatorConfigError("vapi_public_api_key_required_for_control")
        phone_response = await self._client.get(
            f"/phone-number/{self._phone_number_id}",
            headers=self._headers,
            follow_redirects=False,
        )
        phone_response.raise_for_status()
        self._creation_attempted = True
        response = await self._client.post(
            "/call",
            headers=self._headers,
            json={
                "assistantId": self._assistant_id,
                "phoneNumberId": self._phone_number_id,
                "customer": {"number": self._destination},
                "name": self._call_marker,
                "assistantOverrides": {
                    "monitorPlan": {
                        "controlEnabled": True,
                        "controlAuthenticationEnabled": bool(self._public_api_key),
                    }
                },
            },
            follow_redirects=False,
        )
        response.raise_for_status()
        payload = response.json()
        call_id = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(call_id, str) or not call_id.strip():
            raise ValueError("vapi_call_response_missing_id")
        status = payload.get("status") if isinstance(payload, dict) else None
        return VapiCall(
            call_id=call_id,
            status=str(status) if status is not None else None,
        )

    async def stop(self, call_id: str) -> None:
        if not isinstance(call_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", call_id):
            raise ValueError("vapi_call_id_invalid")
        await asyncio.wait_for(
            self._stop_and_confirm(call_id), timeout=self._CLEANUP_TIMEOUT_SECONDS
        )

    def _owns_call(self, payload: Any) -> bool:
        return (
            isinstance(payload, dict)
            and payload.get("name") == self._call_marker
            and payload.get("assistantId") == self._assistant_id
            and payload.get("phoneNumberId") == self._phone_number_id
            and isinstance(payload.get("customer"), dict)
            and payload["customer"].get("number") == self._destination
        )

    async def _stop_and_confirm(self, call_id: str) -> None:
        sent = False
        while True:
            response = await self._client.get(
                f"/call/{call_id}", headers=self._headers, follow_redirects=False
            )
            response.raise_for_status()
            payload = response.json()
            if not self._owns_call(payload) or payload.get("id") != call_id:
                raise ValueError("vapi_call_ownership_mismatch")
            if payload.get("status") == "ended":
                return
            monitor = payload.get("monitor")
            control_url = (
                monitor.get("controlUrl") if isinstance(monitor, dict) else None
            )
            if control_url and not sent:
                self._validate_control_url(control_url, call_id)
                # Control authenticates with the public key, never the private API bearer.
                headers = (
                    {"Authorization": f"Bearer {self._public_api_key}"}
                    if self._public_api_key
                    else {}
                )
                request = httpx.Request(
                    "POST", control_url, headers=headers, json={"type": "end-call"}
                )
                answer = await self._client.send(
                    request, auth=None, follow_redirects=False
                )
                if answer.status_code not in {404, 409}:
                    answer.raise_for_status()
                    sent = True
            await asyncio.sleep(self._POLL_INTERVAL_SECONDS)

    @staticmethod
    def _validate_control_url(value: str, call_id: str) -> None:
        url = urlsplit(value)
        host = url.hostname or ""
        if (
            url.scheme != "https"
            or not host.endswith(".vapi.ai")
            or url.port not in {None, 443}
            or url.username is not None
            or url.password is not None
            or url.fragment
            or url.path != f"/{call_id}/control"
        ):
            raise ValueError("vapi_call_control_url_invalid")

    async def reconcile_and_stop(
        self, *, started_after_ms: int, ended_before_ms: int
    ) -> list[str]:
        if (
            type(started_after_ms) is not int
            or type(ended_before_ms) is not int
            or not 0 <= started_after_ms <= ended_before_ms
        ):
            raise TypeError("vapi_reconcile_requires_ordered_epoch_milliseconds")
        if not self._creation_attempted:
            return []
        response = await self._client.get(
            "/call",
            headers=self._headers,
            params={
                "assistantId": self._assistant_id,
                "phoneNumberId": self._phone_number_id,
                "createdAtGe": datetime.fromtimestamp(
                    started_after_ms / 1000, timezone.utc
                ).isoformat(),
                "createdAtLe": datetime.fromtimestamp(
                    ended_before_ms / 1000, timezone.utc
                ).isoformat(),
                "limit": self._LIST_LIMIT,
            },
            follow_redirects=False,
        )
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list) or len(rows) > self._LIST_LIMIT:
            raise ValueError("vapi_call_list_invalid")
        owned = []
        for row in rows:
            if not self._owns_call(row):
                continue
            try:
                created = datetime.fromisoformat(
                    row["createdAt"].replace("Z", "+00:00")
                )
                if created.tzinfo is None:
                    continue
                created_ms = created.timestamp() * 1000
            except (KeyError, AttributeError, TypeError, ValueError, OverflowError):
                continue
            if started_after_ms <= created_ms <= ended_before_ms:
                owned.append(row)
        if len(owned) != 1:
            raise RuntimeError("vapi_call_reconciliation_unresolved")
        call_id = owned[0].get("id")
        await self.stop(call_id)
        return [call_id]

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class VapiAgentEndpoint:
    def __init__(self, *, name: str, assistant_id: str | None = None) -> None:
        self.manifest = AgentEndpointManifest(
            name=name,
            provider="vapi",
            world_kinds=["voice"],
            capabilities=EndpointCapabilities(
                audio=True,
                text=True,
                streaming=True,
                interruption=True,
                dtmf=True,
                transfer=True,
                transcript_events=True,
                tool_events=True,
                usage_events=True,
                internal_metrics=True,
                recording=True,
                web_rtc=False,
                sip=True,
            ),
            metadata={"assistant_id": assistant_id} if assistant_id else {},
        )
        self.capabilities = self.manifest.capabilities

    async def discover(self, request: DiscoveryRequest) -> DiscoverySnapshot:
        del request
        return DiscoverySnapshot(capabilities=self.capabilities)

    async def prepare(self, plan) -> EndpointHandle:  # noqa: ANN001
        return EndpointHandle(
            handle_id=f"vapi-{uuid.uuid4().hex[:12]}",
            endpoint_name=self.manifest.name,
            created_at=datetime.now(timezone.utc),
            metadata={"plan_id": getattr(plan, "plan_id", None)},
        )

    async def wait_ready(self, handle: EndpointHandle) -> ReadinessResult:
        del handle
        raise NotImplementedError(
            "Vapi direct execution seam; live path uses LiveKit SIP outbound today"
        )

    async def send(
        self, handle: EndpointHandle, event: RealtimeEvent | AudioFrame
    ) -> None:
        raise NotImplementedError("VapiAgentEndpoint.send is a Stage-8 seam")

    async def receive(
        self, handle: EndpointHandle
    ) -> AsyncIterator[RealtimeEvent | AudioFrame]:
        raise NotImplementedError("VapiAgentEndpoint.receive is a Stage-8 seam")
        yield  # type: ignore[unreachable]

    async def stop(self, handle: EndpointHandle) -> None:
        del handle

    async def cleanup(self, handle: EndpointHandle) -> None:
        del handle

    async def reconcile(self, handle: EndpointHandle) -> ReconciliationResult:
        del handle
        return ReconciliationResult(reconciled=True)


__all__ = [
    "VapiAgentEndpoint",
    "VapiCall",
    "VapiCallOriginator",
    "VapiOriginatorConfigError",
]
