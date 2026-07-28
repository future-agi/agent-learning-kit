from __future__ import annotations

from fi.simulate.runtime.capabilities import CapabilitySet
from fi.simulate.runtime.ids import new_plan_id
from fi.simulate.runtime.plan import (
    AdapterRef,
    ArtifactPlan,
    EvidencePlan,
    SimulationPlan,
)
from fi.simulate.runtime.spec import SimulationSpec

_ENDPOINT_CAPABILITIES = {
    "callable": {"text", "transcript_events", "tool_events"},
    "http": {"text", "transcript_events", "tool_events"},
    "websocket": {"text", "streaming", "transcript_events", "tool_events"},
    "livekit": {
        "audio",
        "streaming",
        "interruption",
        "recording",
        "transcript_events",
        "web_rtc",
    },
}


def build_plan(spec: SimulationSpec) -> SimulationPlan:
    supported = sorted(_ENDPOINT_CAPABILITIES.get(spec.target.adapter, set()))
    root_directory = spec.artifacts.root_directory or f".fagi/runs/{spec.run_id}"
    return SimulationPlan(
        plan_id=new_plan_id(),
        run_id=spec.run_id,
        spec_hash=spec.spec_hash or spec.content_hash(),
        environment_adapter=AdapterRef(
            name=spec.environment.adapter,
            version=spec.environment.adapter_version,
            config=spec.environment.config,
        ),
        target_adapter=AdapterRef(
            name=spec.target.adapter,
            version=spec.target.adapter_version,
            config=spec.target.config,
        ),
        simulator_adapter=AdapterRef(
            name=spec.simulator.adapter,
            version=spec.simulator.adapter_version,
            config=spec.simulator.config,
        ),
        negotiated_capabilities=CapabilitySet(
            required=spec.target.required_capabilities,
            supported=supported,
        ),
        runtime_requirements=spec.execution.runtime,
        timeout_policy=spec.execution.timeout,
        retry_policy=spec.execution.retry,
        cleanup_policy=spec.execution.cleanup,
        evidence_plan=EvidencePlan(
            source_ids=[source.source_id for source in spec.evidence.sources],
            required_capabilities=spec.evidence.required_capabilities,
        ),
        artifact_plan=ArtifactPlan(
            enabled=spec.artifacts.enabled,
            root_directory=root_directory,
            record_audio=spec.artifacts.record_audio,
            required_types=spec.artifacts.required_types,
        ),
    )
