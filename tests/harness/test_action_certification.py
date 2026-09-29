from __future__ import annotations

from dataclasses import dataclass, field

from fi.alk.harness.action_certification import (
    ActionInvocation,
    ActionProbeMode,
    ActionProbeStatus,
    certify_actions,
    schema_valid_arguments,
)
from fi.alk.harness.source_model import SourceAction


@dataclass
class Adapter:
    state: list[str] = field(default_factory=list)
    baseline: list[str] = field(default_factory=list)

    def invoke(
        self, action: SourceAction, arguments: dict[str, object]
    ) -> ActionInvocation:
        self.state.append(f"{action.name}:{arguments['id']}")
        return ActionInvocation(result={"ok": True})

    def checkpoint(self) -> str:
        return "|".join(self.state)

    def reset(self) -> None:
        self.state = list(self.baseline)


def _action(*, effect: str = "mutating", kind: str = "callable") -> SourceAction:
    return SourceAction(
        name="update",
        input_schema={
            "type": "object",
            "properties": {"id": {"type": "string", "enum": ["known-id"]}},
            "required": ["id"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        },
        implementation_kind=kind,
        implementation_ref="agent:update",
        effect=effect,
    )


def test_schema_valid_arguments_are_deterministic() -> None:
    first = schema_valid_arguments(_action().input_schema)
    second = schema_valid_arguments(_action().input_schema)

    assert first == second == {"id": "known-id"}


def test_mutating_action_runs_in_disposable_world_and_verifies_reset() -> None:
    report = certify_actions((_action(),), adapter=Adapter())

    result = report.actions[0]
    assert result.mode is ActionProbeMode.DISPOSABLE_WORLD
    assert result.status is ActionProbeStatus.PASSED
    assert result.input_schema_validated is True
    assert result.output_schema_validated is True
    assert result.reset_verified is True


def test_unknown_or_external_action_is_not_executed_without_safe_adapter_policy() -> (
    None
):
    report = certify_actions(
        (_action(effect="unknown", kind="service"),), adapter=Adapter()
    )

    result = report.actions[0]
    assert result.mode is ActionProbeMode.RUNTIME_ONLY
    assert result.status is ActionProbeStatus.NOT_RUN


def test_reset_mismatch_fails_certification() -> None:
    adapter = Adapter(baseline=["unexpected"])
    report = certify_actions((_action(),), adapter=adapter)

    assert report.actions[0].status is ActionProbeStatus.FAILED
    assert report.actions[0].reset_verified is False


def test_business_refusal_certifies_reachable_action_boundary() -> None:
    class RefusingAdapter(Adapter):
        def invoke(
            self, action: SourceAction, arguments: dict[str, object]
        ) -> ActionInvocation:
            return ActionInvocation(refused=True, error="business rule refused request")

    report = certify_actions((_action(effect="read_only"),), adapter=RefusingAdapter())

    assert report.actions[0].status is ActionProbeStatus.REFUSED
    assert report.certified == report.executable == 1


def test_adapter_can_report_that_no_safe_direct_boundary_exists() -> None:
    class RuntimeOnlyAdapter(Adapter):
        def invoke(
            self, action: SourceAction, arguments: dict[str, object]
        ) -> ActionInvocation:
            return ActionInvocation(not_run=True, error="source action is agent-only")

    report = certify_actions((_action(effect="read_only"),), adapter=RuntimeOnlyAdapter())

    result = report.actions[0]
    assert result.status is ActionProbeStatus.NOT_RUN
    assert result.input_schema_validated is True
    assert result.reason == "source action is agent-only"
    assert report.certified == report.executable == 0
