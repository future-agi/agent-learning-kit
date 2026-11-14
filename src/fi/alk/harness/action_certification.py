"""Safe, framework-neutral behavioral probes for source actions.

An adapter may execute a Python callable, an HTTP endpoint, a queue command, or a browser action.
The certification policy and result model do not know which agent framework owns that adapter.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, model_validator

from .source_model import SourceAction

ACTION_CERTIFICATION_SCHEMA_VERSION = "futureagi.action-certification.v1"


class ActionProbeMode(str, Enum):
    READ_ONLY = "read_only"
    DISPOSABLE_WORLD = "disposable_world"
    EXTERNAL_SANDBOX = "external_sandbox"
    RUNTIME_ONLY = "runtime_only"


class ActionProbeStatus(str, Enum):
    PASSED = "passed"
    REFUSED = "refused"
    FAILED = "failed"
    NOT_RUN = "not_run"


class ActionProbePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: ActionProbeMode
    arguments: dict[str, object] | None = None


@dataclass(frozen=True)
class ActionInvocation:
    result: object | None = None
    refused: bool = False
    not_run: bool = False
    error: str | None = None


class ActionProbeAdapter(Protocol):
    """Narrow execution boundary implemented by each supported runtime surface."""

    def invoke(
        self, action: SourceAction, arguments: dict[str, object]
    ) -> ActionInvocation: ...

    def checkpoint(self) -> str: ...

    def reset(self) -> None: ...


class ActionProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str
    mode: ActionProbeMode
    status: ActionProbeStatus
    input_schema_validated: bool
    output_schema_validated: bool
    reset_verified: bool
    reason: str


class ActionCertificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = ACTION_CERTIFICATION_SCHEMA_VERSION
    actions: tuple[ActionProbeResult, ...]
    passed: int
    executable: int
    total: int
    fingerprint: str

    @property
    def certified(self) -> int:
        """Actions whose adapter was reached without a harness/runtime failure.

        A schema-valid synthetic probe may be refused by the target's business
        rules.  That still proves the action boundary is callable; only FAILED
        probes are certification failures.
        """

        return sum(
            item.status in {ActionProbeStatus.PASSED, ActionProbeStatus.REFUSED}
            for item in self.actions
        )

    @classmethod
    def create(cls, actions: list[ActionProbeResult]) -> "ActionCertificationReport":
        ordered = tuple(sorted(actions, key=lambda item: item.action))
        raw: dict[str, object] = {
            "schema_version": ACTION_CERTIFICATION_SCHEMA_VERSION,
            "actions": ordered,
            "passed": sum(item.status is ActionProbeStatus.PASSED for item in ordered),
            "executable": sum(
                item.status is not ActionProbeStatus.NOT_RUN for item in ordered
            ),
            "total": len(ordered),
        }
        raw["fingerprint"] = _fingerprint(raw)
        return cls.model_validate(raw)

    @model_validator(mode="after")
    def _canonical(self) -> "ActionCertificationReport":
        if self.schema_version != ACTION_CERTIFICATION_SCHEMA_VERSION:
            raise ValueError("action_certification_schema_version_unsupported")
        if self.actions != tuple(sorted(self.actions, key=lambda item: item.action)):
            raise ValueError("action_certification_not_canonical")
        expected = _fingerprint(self.model_dump(mode="python", exclude={"fingerprint"}))
        if self.fingerprint != expected:
            raise ValueError("action_certification_fingerprint_mismatch")
        return self


def default_probe_policy(action: SourceAction) -> ActionProbePolicy:
    """Choose the safest policy from portable action facts, never from agent identity."""

    if action.effect == "read_only":
        mode = ActionProbeMode.READ_ONLY
    elif action.effect == "mutating":
        mode = ActionProbeMode.DISPOSABLE_WORLD
    elif action.effect == "external":
        mode = ActionProbeMode.EXTERNAL_SANDBOX
    elif action.implementation_kind in {"import", "construct", "callable"}:
        mode = ActionProbeMode.DISPOSABLE_WORLD
    else:
        mode = ActionProbeMode.RUNTIME_ONLY
    return ActionProbePolicy(mode=mode)


def schema_valid_arguments(schema: dict[str, object]) -> dict[str, object]:
    """Produce one deterministic JSON-schema-valid object for the supported core vocabulary."""

    value = _schema_value(schema)
    if not isinstance(value, dict):
        raise ValueError("action_input_schema_root_not_object")
    Draft202012Validator(schema).validate(value)
    return value


def _schema_value(schema: dict[str, object]) -> object:
    for key in ("const", "default"):
        if key in schema:
            return schema[key]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    choices = schema.get("oneOf") or schema.get("anyOf")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        return _schema_value(choices[0])
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((item for item in kind if item != "null"), "null")
    if kind == "object" or (kind is None and "properties" in schema):
        properties = schema.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        required = schema.get("required")
        names = required if isinstance(required, list) else sorted(properties)
        return {
            str(name): _schema_value(value)
            for name in names
            if isinstance((value := properties.get(name)), dict)
        }
    if kind == "array":
        items = schema.get("items")
        minimum = schema.get("minItems", 0)
        count = max(0, int(minimum)) if isinstance(minimum, int) else 0
        return (
            [_schema_value(items) for _ in range(count)]
            if isinstance(items, dict)
            else []
        )
    if kind == "integer":
        return int(schema.get("minimum", 0))
    if kind == "number":
        return float(schema.get("minimum", 0))
    if kind == "boolean":
        return False
    if kind == "null":
        return None
    if kind == "string" or kind is None:
        return (
            str(schema.get("pattern") or "test") if "pattern" not in schema else "test"
        )
    raise ValueError(f"action_input_schema_type_unsupported: {kind}")


def certify_actions(
    actions: tuple[SourceAction, ...],
    *,
    adapter: ActionProbeAdapter | None,
    policies: dict[str, ActionProbePolicy] | None = None,
) -> ActionCertificationReport:
    """Probe safe actions and explicitly mark everything else as runtime-only."""

    policies = policies or {}
    results: list[ActionProbeResult] = []
    for action in actions:
        policy = policies.get(action.name) or default_probe_policy(action)
        if adapter is None or policy.mode in {
            ActionProbeMode.RUNTIME_ONLY,
            ActionProbeMode.EXTERNAL_SANDBOX,
        }:
            results.append(
                ActionProbeResult(
                    action=action.name,
                    mode=policy.mode,
                    status=ActionProbeStatus.NOT_RUN,
                    input_schema_validated=False,
                    output_schema_validated=False,
                    reset_verified=False,
                    reason=(
                        "no safe probe adapter is available"
                        if adapter is None
                        else "behavior is verified only by its declared external/runtime adapter"
                    ),
                )
            )
            continue
        try:
            arguments = policy.arguments or schema_valid_arguments(action.input_schema)
            Draft202012Validator(action.input_schema).validate(arguments)
        except Exception as exc:
            results.append(
                ActionProbeResult(
                    action=action.name,
                    mode=policy.mode,
                    status=ActionProbeStatus.FAILED,
                    input_schema_validated=False,
                    output_schema_validated=False,
                    reset_verified=False,
                    reason=f"input probe is invalid: {type(exc).__name__}",
                )
            )
            continue
        baseline = adapter.checkpoint()
        try:
            invocation = adapter.invoke(action, arguments)
        except Exception as exc:
            invocation = ActionInvocation(error=type(exc).__name__)
        if invocation.not_run:
            results.append(
                ActionProbeResult(
                    action=action.name,
                    mode=policy.mode,
                    status=ActionProbeStatus.NOT_RUN,
                    input_schema_validated=True,
                    output_schema_validated=False,
                    reset_verified=False,
                    reason=invocation.error
                    or "the runtime exposes no safe direct action boundary",
                )
            )
            continue
        output_valid = invocation.refused or action.output_schema is None
        if (
            not invocation.refused
            and invocation.error is None
            and action.output_schema is not None
        ):
            try:
                Draft202012Validator(action.output_schema).validate(invocation.result)
                output_valid = True
            except Exception:
                output_valid = False
        reset_verified = policy.mode is ActionProbeMode.READ_ONLY
        if policy.mode is ActionProbeMode.DISPOSABLE_WORLD:
            try:
                adapter.reset()
                reset_verified = adapter.checkpoint() == baseline
            except Exception:
                reset_verified = False
        if invocation.refused and reset_verified:
            status = ActionProbeStatus.REFUSED
        elif invocation.error is not None or not output_valid or not reset_verified:
            status = ActionProbeStatus.FAILED
        else:
            status = ActionProbeStatus.PASSED
        reason = (
            "action refused schema-valid probe"
            if status is ActionProbeStatus.REFUSED
            else invocation.error
            or ("output did not match schema" if not output_valid else "")
            or (
                "reset did not restore baseline"
                if not reset_verified
                else "probe passed"
            )
        )
        results.append(
            ActionProbeResult(
                action=action.name,
                mode=policy.mode,
                status=status,
                input_schema_validated=True,
                output_schema_validated=output_valid,
                reset_verified=reset_verified,
                reason=reason,
            )
        )
    return ActionCertificationReport.create(results)


def _fingerprint(raw: dict[str, object]) -> str:
    def encode(value: object) -> object:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, tuple):
            return [encode(item) for item in value]
        if isinstance(value, dict):
            return {str(key): encode(value[key]) for key in sorted(value)}
        return value

    payload = json.dumps(
        encode(raw),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


__all__ = [
    "ACTION_CERTIFICATION_SCHEMA_VERSION",
    "ActionCertificationReport",
    "ActionInvocation",
    "ActionProbeAdapter",
    "ActionProbeMode",
    "ActionProbePolicy",
    "ActionProbeResult",
    "ActionProbeStatus",
    "certify_actions",
    "default_probe_policy",
    "schema_valid_arguments",
]
