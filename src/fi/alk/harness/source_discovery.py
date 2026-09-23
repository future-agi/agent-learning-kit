"""Framework-neutral source discovery and composition.

Adapters translate repository facts into the canonical source model.  Nothing in this module
selects behavior by agent name, provider, modality, or framework; those concerns stop at the
adapter boundary.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable, TypeVar

from .contract import AgentContract
from .packaging import inspect_packaging
from .source_model import (
    SourceAction,
    SourceEvidence,
    SourceInterface,
    SourceModel,
    SourceProcess,
)


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _json_type(declared: str) -> dict[str, object]:
    normalized = declared.strip().lower()
    if "bool" in normalized:
        return {"type": "boolean"}
    # Preserve the widest accepted numeric shape for unions such as
    # ``int | float`` instead of narrowing according to token order.
    if any(token in normalized for token in ("float", "number", "decimal")):
        return {"type": "number"}
    if "int" in normalized:
        return {"type": "integer"}
    if any(token in normalized for token in ("list", "array", "tuple", "set")):
        return {"type": "array"}
    if any(token in normalized for token in ("dict", "mapping", "object", "json")):
        return {"type": "object"}
    return {"type": "string"}


def _action_schema(tool: Any) -> dict[str, object]:
    properties: dict[str, object] = {}
    for name in tool.args:
        property_schema = _json_type(str(tool.arg_types.get(name, "")))
        values = tool.arg_values.get(name)
        if isinstance(values, (list, tuple)) and values:
            usable = [value for value in values if value not in (None, "null", "")]
            if usable:
                # An observed set of allowed values is more precise than an
                # inferred textual type. Contracts can mislabel a string-valued
                # Literal as int; keeping both yields an impossible schema and
                # makes an unrelated action probe abort the whole run.
                property_schema = {"enum": usable}
        properties[name] = property_schema
    return {
        "type": "object",
        "properties": properties,
        "required": list(tool.args),
        "additionalProperties": False,
    }


def _configuration_names(contract: AgentContract) -> tuple[str, ...]:
    names: set[str] = set()
    for dependency in [*contract.dependencies, *contract.runtime_dependencies]:
        reached = dependency.reached
        names.update(
            value
            for value in (
                reached.dsn_env,
                reached.config_key,
                reached.password_from,
            )
            if value
        )
    return tuple(sorted(names))


def discover_code_source_model(
    root: Path,
    contract: AgentContract,
    *,
    source_digest: str,
) -> SourceModel:
    """Discover runtime, interface, and action facts without executing submitted code."""

    root = root.resolve()
    packaging = inspect_packaging(root)
    evidence: list[SourceEvidence] = []
    selected = packaging.selected_path
    if selected:
        selected_path = root / selected
        if selected_path.is_file() and not selected_path.is_symlink():
            evidence.append(
                SourceEvidence(
                    path=Path(selected).as_posix(),
                    digest=_digest(selected_path),
                    kind="runtime_packaging",
                )
            )

    runtime = contract.runtime
    configuration_names = _configuration_names(contract)
    processes: list[SourceProcess] = []
    interfaces: list[SourceInterface] = []
    if runtime is not None and (selected or runtime.command):
        process_evidence = tuple(evidence)
        processes.append(
            SourceProcess(
                name="agent-runtime",
                kind=(
                    packaging.selected_kind.value
                    if packaging.selected_kind is not None
                    else (runtime.language.strip().lower() or "process")
                ),
                working_directory=runtime.workdir or ".",
                entrypoint=selected,
                command=tuple(runtime.command),
                configuration_names=configuration_names,
                ports=(
                    (runtime.interface.port,)
                    if runtime.interface is not None
                    and runtime.interface.port is not None
                    else ()
                ),
                evidence=process_evidence,
            )
        )
    if runtime is not None and runtime.interface is not None:
        interface = runtime.interface
        interfaces.append(
            SourceInterface(
                name="primary",
                kind=interface.kind or "runtime",
                protocol=interface.protocol or "unknown",
                process="agent-runtime" if processes else None,
                endpoint=interface.path or None,
                port=interface.port,
                configuration_names=configuration_names,
            )
        )

    entries: dict[str, list[Any]] = {}
    for entry in contract.tool_entrypoints:
        entries.setdefault(entry.tool, []).append(entry)
    actions: list[SourceAction] = []
    for tool in contract.tools:
        matches = entries.get(tool.name, [])
        if len(matches) == 1:
            entry = matches[0]
            implementation_kind = entry.mode or "unknown"
            implementation_ref = (
                f"{entry.module}:{entry.callable}"
                if entry.module and entry.callable
                else entry.endpoint or entry.service or "runtime"
            )
        else:
            implementation_kind = "unknown" if not matches else "ambiguous"
            implementation_ref = "runtime"
        actions.append(
            SourceAction(
                name=tool.name,
                input_schema=_action_schema(tool),
                output_schema=None,
                implementation_kind=implementation_kind,
                implementation_ref=implementation_ref,
                interface="primary"
                if interfaces and implementation_kind == "service"
                else None,
                effect="unknown",
            )
        )

    return SourceModel.create(
        source_digest=source_digest,
        engine="none",
        processes=tuple(processes),
        interfaces=tuple(interfaces),
        actions=tuple(actions),
        configuration_names=configuration_names,
        evidence=tuple(evidence),
    )


Item = TypeVar("Item")


def _merge_named(
    left: Iterable[Item], right: Iterable[Item], *, collection: str
) -> tuple[Item, ...]:
    merged: dict[str, Item] = {}
    for item in [*left, *right]:
        name = str(getattr(item, "name"))
        previous = merged.get(name)
        if previous is not None and previous != item:
            raise ValueError(f"source_model_{collection}_conflict: {name}")
        merged[name] = item
    return tuple(merged[name] for name in sorted(merged))


def compose_source_models(code: SourceModel, state: SourceModel) -> SourceModel:
    """Combine independently discovered code and state facts without guessing conflicts."""

    if code.source_digest != state.source_digest:
        raise ValueError("source_model_digest_conflict")
    engine = state.engine if state.engine != "none" else code.engine
    engine_version = state.engine_version or code.engine_version
    return SourceModel.create(
        source_digest=state.source_digest,
        engine=engine,
        engine_version=engine_version,
        tables=_merge_named(code.tables, state.tables, collection="tables"),
        processes=_merge_named(code.processes, state.processes, collection="processes"),
        interfaces=_merge_named(
            code.interfaces, state.interfaces, collection="interfaces"
        ),
        actions=_merge_named(code.actions, state.actions, collection="actions"),
        configuration_names=tuple(
            sorted(set(code.configuration_names) | set(state.configuration_names))
        ),
        migrations=tuple(
            sorted({*code.migrations, *state.migrations}, key=lambda item: item.path)
        ),
        seeds=tuple(sorted({*code.seeds, *state.seeds}, key=lambda item: item.path)),
        evidence=tuple(
            sorted({*code.evidence, *state.evidence}, key=lambda item: item.path)
        ),
        unsupported=tuple(
            sorted(
                {*code.unsupported, *state.unsupported},
                key=lambda item: (item.code, item.component, item.location or ""),
            )
        ),
    )


__all__ = ["compose_source_models", "discover_code_source_model"]
