"""Retell target-agent adapter — capability declaration + Stage 6/8 seam.

Retell has no PSTN outbound API; ``VapiAgentEndpoint`` supports both
directions but this adapter refuses ``sip_outbound`` explicitly to
match the guard in ``AgentDefinition``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Literal

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


class RetellAgentEndpoint:
    def __init__(
        self,
        *,
        name: str,
        channel: Literal["sip_inbound", "web_call"] = "sip_inbound",
        agent_id: str | None = None,
    ) -> None:
        if channel == "sip_outbound":
            raise ValueError(
                "retell_pstn_outbound_unsupported: Retell has no outbound API"
            )
        self.manifest = AgentEndpointManifest(
            name=name,
            provider="retell",
            world_kinds=["voice"],
            capabilities=EndpointCapabilities(
                audio=True,
                text=True,
                streaming=True,
                interruption=True,
                dtmf=False,
                transfer=False,
                transcript_events=True,
                tool_events=True,
                usage_events=True,
                internal_metrics=False,
                recording=True,
                web_rtc=channel == "web_call",
                sip=channel == "sip_inbound",
            ),
            metadata={"channel": channel, "agent_id": agent_id}
            if agent_id
            else {"channel": channel},
        )
        self._channel = channel
        self.capabilities = self.manifest.capabilities

    async def discover(self, request: DiscoveryRequest) -> DiscoverySnapshot:
        del request
        return DiscoverySnapshot(capabilities=self.capabilities)

    async def prepare(self, plan) -> EndpointHandle:  # noqa: ANN001
        return EndpointHandle(
            handle_id=f"retell-{uuid.uuid4().hex[:12]}",
            endpoint_name=self.manifest.name,
            created_at=datetime.now(timezone.utc),
            metadata={"plan_id": getattr(plan, "plan_id", None), "channel": self._channel},
        )

    async def wait_ready(self, handle: EndpointHandle) -> ReadinessResult:
        del handle
        raise NotImplementedError(
            "Retell direct execution seam; live path uses LiveKit SIP inbound today"
        )

    async def send(
        self, handle: EndpointHandle, event: RealtimeEvent | AudioFrame
    ) -> None:
        raise NotImplementedError("RetellAgentEndpoint.send is a Stage-8 seam")

    async def receive(
        self, handle: EndpointHandle
    ) -> AsyncIterator[RealtimeEvent | AudioFrame]:
        raise NotImplementedError("RetellAgentEndpoint.receive is a Stage-8 seam")
        yield  # type: ignore[unreachable]

    async def stop(self, handle: EndpointHandle) -> None:
        del handle

    async def cleanup(self, handle: EndpointHandle) -> None:
        del handle

    async def reconcile(self, handle: EndpointHandle) -> ReconciliationResult:
        del handle
        return ReconciliationResult(reconciled=True)


__all__ = ["RetellAgentEndpoint"]
