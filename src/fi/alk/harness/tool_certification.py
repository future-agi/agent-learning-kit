"""Deterministic certification of the submitted agent tool inventory.

This layer never invents or executes agent behaviour.  It proves that every tool in the
contract has one unambiguous implementation seam in the authored bundle, and records when
behaviour can only be observed safely during a real call.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contract import AgentContract, runtime_owns_tools
from .world_door import READ_METHODS

TOOL_CERTIFICATION_SCHEMA_VERSION = "futureagi.tool-certification.v1"


class ToolProbeMode(str, Enum):
    READ_ONLY = "read_only"
    DISPOSABLE_WORLD = "disposable_world"
    PROVIDER_SANDBOX = "provider_sandbox"
    RUNTIME_ONLY = "runtime_only"


class ToolAvailability(str, Enum):
    CERTIFIED = "certified"
    RUNTIME_ONLY = "runtime_only"
    REJECTED = "rejected"


class ToolCertification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: str
    entrypoint_mode: str
    probe_mode: ToolProbeMode
    availability: ToolAvailability
    schema_validated: bool
    implementation_validated: bool
    reason: str


class ToolCertificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = TOOL_CERTIFICATION_SCHEMA_VERSION
    tools: tuple[ToolCertification, ...]
    certified_or_runtime_only: int = Field(ge=0)
    total: int = Field(ge=0)
    fingerprint: str

    @classmethod
    def create(cls, tools: list[ToolCertification]) -> "ToolCertificationReport":
        ordered = tuple(sorted(tools, key=lambda item: item.tool))
        raw: dict[str, Any] = {
            "schema_version": TOOL_CERTIFICATION_SCHEMA_VERSION,
            "tools": ordered,
            "certified_or_runtime_only": sum(
                item.availability is not ToolAvailability.REJECTED for item in ordered
            ),
            "total": len(ordered),
        }
        raw["fingerprint"] = _fingerprint(raw)
        return cls.model_validate(raw)

    @property
    def external_driver_blockers(self) -> tuple[ToolCertification, ...]:
        """Tools that stop this bundle serving a driver outside the submitted agent's process.

        An external RL trainer never runs the submitted agent, so a tool is only usable when
        this layer can execute it: ``CERTIFIED`` with ``DISPOSABLE_WORLD``, meaning a compiled
        handler exists in the bundle and its effects land in a world we own.

        ``RUNTIME_ONLY`` is the blocker that matters. Those tools are declared but execute
        inside the agent's own process or service, so a trainer calling one is acknowledged
        while nothing in the world changes -- see ``ProvisionedWorld.call``, which answers
        ``ok=True`` with ``execution="submitted_agent_runtime"``. Reward earned that way is
        measuring a tool that did nothing. ``REJECTED`` blocks for the plainer reason that no
        seam was found at all.
        """
        return tuple(
            item
            for item in self.tools
            if item.availability is not ToolAvailability.CERTIFIED
            or item.probe_mode is not ToolProbeMode.DISPOSABLE_WORLD
        )

    @property
    def serves_external_driver(self) -> bool:
        """Whether every declared tool can be driven without the submitted agent running.

        A bundle with no tools is not serviceable: there is nothing for a trainer to do.
        """
        return bool(self.tools) and not self.external_driver_blockers

    @model_validator(mode="after")
    def _canonical(self) -> "ToolCertificationReport":
        if self.schema_version != TOOL_CERTIFICATION_SCHEMA_VERSION:
            raise ValueError("tool_certification_schema_version_unsupported")
        if self.tools != tuple(sorted(self.tools, key=lambda item: item.tool)):
            raise ValueError("tool_certification_not_canonical")
        expected = _fingerprint(self.model_dump(mode="python", exclude={"fingerprint"}))
        if self.fingerprint != expected:
            raise ValueError("tool_certification_fingerprint_mismatch")
        return self


def _fingerprint(raw: dict[str, Any]) -> str:
    def jsonable(value: Any) -> Any:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, tuple):
            return [jsonable(item) for item in value]
        if isinstance(value, dict):
            return {str(key): jsonable(value[key]) for key in sorted(value)}
        return value

    encoded = json.dumps(
        jsonable(raw), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def framework_coordination(tool: Any, entry: Any, contract: AgentContract, source: Path) -> bool:
    """Whether a tool is the agent framework's own worker coordination, in a contract with workers, reading and
    changing no world table: imported from a package the submitted source does not contain, or, recorded as having no
    importable seam (a framework middleware injects it while the agent runs), defined nowhere in the source."""
    if not contract.workers or tool.tables_read or tool.tables_changed:
        return False
    if entry.mode in {"", "unreachable"}:
        # Only on positive evidence: a known framework injects this tool, and the source uses that framework.
        from .framework_servers import framework_injected

        return framework_injected(tool.name, entry.notes, lambda package: _source_imports(source, package)) is not None
    if entry.mode not in {"import", "construct"}:
        return False
    package = entry.module.strip().split(".")[0]
    if not package:
        return False
    found = _source_modules(source, package)
    # Absent from the source, or shipped there only as the framework's own library that the app depends on (a
    # framework's monorepo holds both the library and its example apps).
    return not found or _library_the_app_depends_on(source, package, found)


def _source_imports(source: Path, package: str) -> bool:
    """Whether the submitted source's own Python code imports `package`."""
    pattern = re.compile(rf"^\s*(?:from\s+{re.escape(package)}(?:\.|\s)|import\s+{re.escape(package)}\b)", re.M)
    for path in source.rglob("*.py"):
        if any(
            part.startswith(".") or part in {"site-packages", "node_modules"}
            for part in path.relative_to(source).parts
        ):
            continue
        try:
            if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                return True
        except OSError:
            continue
    return False


def _source_modules(source: Path, package: str) -> list[Path]:
    """Where the submitted source holds the top-level module or package `package`, outside hidden directories."""
    return [
        path
        for path in (*source.rglob(f"{package}.py"), *source.rglob(package))
        if not any(
            part.startswith(".") or part in {"site-packages", "node_modules"}
            for part in path.relative_to(source).parts
        )
    ]


def _library_the_app_depends_on(source: Path, package: str, found: list[Path]) -> bool:
    """Whether every place the source holds `package` is a distribution of that name (its nearest pyproject names it)
    which another project in the source depends on: a library the app installs, not the app's own code."""
    from packaging.utils import canonicalize_name

    from .generated_runtime import _pyproject_document

    name = canonicalize_name(package)
    owners: set[Path] = set()
    for path in found:
        owner = next(
            (
                folder
                for folder in (path, *path.parents)
                if folder.is_relative_to(source) and (folder / "pyproject.toml").is_file()
            ),
            None,
        )
        project = _pyproject_document(owner / "pyproject.toml").get("project", {}) if owner else {}
        if owner is None or canonicalize_name(str(project.get("name") or "")) != name:
            return False
        owners.add(owner)
    for manifest in source.rglob("pyproject.toml"):
        if manifest.parent in owners or any(part.startswith(".") for part in manifest.relative_to(source).parts):
            continue
        dependencies = _pyproject_document(manifest).get("project", {}).get("dependencies") or []
        if any(
            (match := re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", item)) and canonicalize_name(match.group(1)) == name
            for item in dependencies
            if isinstance(item, str)
        ):
            return True
    return False


def certify_tool_inventory(
    contract: AgentContract,
    bundle_dir: Path,
    *,
    external_provider: bool,
    source: Path | None = None,
    native_observation: bool = False,
) -> ToolCertificationReport:
    """Classify every declared tool, rejecting missing or ambiguous implementation seams.

    With ``source``, a voice worker that owns its tools needs no entrypoint for a tool its
    source defines; one it does not define is rejected.
    """

    entries: dict[str, list[Any]] = {}
    for entry in contract.tool_entrypoints:
        entries.setdefault(entry.tool, []).append(entry)
    defined: set[str] | None = None

    results: list[ToolCertification] = []
    for tool in contract.tools:
        matches = entries.get(tool.name, [])
        if external_provider:
            results.append(
                ToolCertification(
                    tool=tool.name,
                    entrypoint_mode="provider",
                    probe_mode=ToolProbeMode.RUNTIME_ONLY,
                    availability=ToolAvailability.RUNTIME_ONLY,
                    schema_validated=True,
                    implementation_validated=False,
                    reason="implementation is owned by the connected provider agent",
                )
            )
            continue
        if not matches and source is not None and runtime_owns_tools(contract):
            if defined is None:
                from .source_tool_evidence import defined_tool_names

                defined = defined_tool_names(source)
            found = tool.name in defined
            results.append(
                ToolCertification(
                    tool=tool.name,
                    entrypoint_mode="runtime" if found else "missing",
                    probe_mode=ToolProbeMode.RUNTIME_ONLY,
                    availability=(
                        ToolAvailability.RUNTIME_ONLY
                        if found
                        else ToolAvailability.REJECTED
                    ),
                    schema_validated=True,
                    implementation_validated=False,
                    reason=(
                        "the submitted voice worker runs this tool; effects are captured "
                        "during calls"
                        if found
                        else f"the submitted source defines no {tool.name}"
                    ),
                )
            )
            continue
        if len(matches) != 1:
            results.append(
                ToolCertification(
                    tool=tool.name,
                    entrypoint_mode="missing" if not matches else "ambiguous",
                    probe_mode=ToolProbeMode.RUNTIME_ONLY,
                    availability=ToolAvailability.REJECTED,
                    schema_validated=True,
                    implementation_validated=False,
                    reason=(
                        "no implementation entrypoint was discovered"
                        if not matches
                        else "multiple implementation entrypoints were discovered"
                    ),
                )
            )
            continue

        entry = matches[0]
        if native_observation and entry.mode in {"import", "construct", "service"}:
            if entry.mode == "service":
                declared = bool(
                    (entry.endpoint.strip() or entry.service.strip()) and entry.method.strip()
                )
            else:
                declared = bool(
                    entry.module.strip()
                    and entry.callable.strip()
                    and (entry.mode != "construct" or entry.factory.strip())
                )
            results.append(
                ToolCertification(
                    tool=tool.name,
                    entrypoint_mode=entry.mode,
                    probe_mode=ToolProbeMode.RUNTIME_ONLY,
                    availability=(
                        ToolAvailability.RUNTIME_ONLY if declared else ToolAvailability.REJECTED
                    ),
                    schema_validated=True,
                    implementation_validated=False,
                    reason=(
                        "native implementation is declared; execution is observed only in agent responses"
                        if declared else "native implementation declaration is incomplete"
                    ),
                )
            )
        elif source is not None and framework_coordination(tool, entry, contract, source):
            # The multi-agent framework's own tool (an async sub-agent launch or check): it starts and follows the
            # workers, so it has no world code to compile and touches no table; its calls are observed in the run.
            results.append(
                ToolCertification(
                    tool=tool.name,
                    entrypoint_mode=entry.mode,
                    probe_mode=ToolProbeMode.RUNTIME_ONLY,
                    availability=ToolAvailability.RUNTIME_ONLY,
                    schema_validated=True,
                    implementation_validated=False,
                    reason=f"provided by the agent framework ({entry.module.split('.')[0] or 'injected as it runs'}) "
                    "to coordinate its workers; not a world action",
                )
            )
        elif entry.mode in {"import", "construct"}:
            handler = bundle_dir / "handlers" / f"{tool.name}.py"
            reachable = handler.is_file() and not handler.is_symlink()
            results.append(
                ToolCertification(
                    tool=tool.name,
                    entrypoint_mode=entry.mode,
                    probe_mode=ToolProbeMode.DISPOSABLE_WORLD,
                    availability=(
                        ToolAvailability.CERTIFIED
                        if reachable
                        else ToolAvailability.REJECTED
                    ),
                    schema_validated=True,
                    implementation_validated=reachable,
                    reason=(
                        "compiled source handler is present; effects run in a disposable world"
                        if reachable
                        else "compiled source handler is absent from the bundle"
                    ),
                )
            )
        elif entry.mode == "service" and (entry.endpoint or entry.service) and entry.method:
            results.append(
                ToolCertification(
                    tool=tool.name,
                    entrypoint_mode=entry.mode,
                    probe_mode=ToolProbeMode.RUNTIME_ONLY,
                    availability=ToolAvailability.RUNTIME_ONLY,
                    schema_validated=True,
                    implementation_validated=True,
                    reason="service seam is declared; semantic effects are captured during calls",
                )
            )
        else:
            results.append(
                ToolCertification(
                    tool=tool.name,
                    entrypoint_mode=entry.mode or "unreachable",
                    probe_mode=ToolProbeMode.RUNTIME_ONLY,
                    availability=ToolAvailability.REJECTED,
                    schema_validated=True,
                    implementation_validated=False,
                    reason="tool has no runnable implementation seam",
                )
            )

    # Entrypoints not grounded in a declared tool are also a contract mismatch.
    declared = {tool.name for tool in contract.tools}
    for name in sorted(set(entries) - declared):
        results.append(
            ToolCertification(
                tool=name,
                entrypoint_mode="orphan",
                probe_mode=ToolProbeMode.RUNTIME_ONLY,
                availability=ToolAvailability.REJECTED,
                schema_validated=False,
                implementation_validated=False,
                reason="implementation entrypoint has no declared tool contract",
            )
        )
    return ToolCertificationReport.create(results)



def compare_declared_tables(
    report: ToolCertificationReport, contract: AgentContract, proofs: Mapping[str, Any]
) -> tuple[ToolCertificationReport, list[str]]:
    """Each tool's declared `tables_changed` against the tables its scenarios' proof runs changed. A proof run counts
    for a tool only when that tool was the one tool that wrote in it; any other run that changed tables is skipped and
    named. A changed table the tool does not declare is flagged in its row's reason, its availability unchanged."""
    reads = {
        entry.tool
        for entry in contract.tool_entrypoints
        if entry.effect == "read" or (entry.effect != "write" and entry.method.upper() in READ_METHODS)
    }
    changed_by: dict[str, set[str]] = {}
    skipped: list[str] = []
    for key, proof in sorted(proofs.items()):
        trial = proof.get("trial") if isinstance(proof, Mapping) else None
        if not isinstance(trial, Mapping) or not trial.get("changed"):
            continue
        writers = sorted({str(name) for name in trial.get("calls") or () if name not in reads})
        if trial.get("unattributable"):
            skipped.append(f"{key}: {trial['unattributable']}")
        elif len(writers) != 1:
            skipped.append(f"{key}: {len(writers)} tools wrote in it, so no one tool changed its tables")
        else:
            changed_by.setdefault(writers[0], set()).update(str(name) for name in trial["changed"])
    declared = {tool.name: set(tool.tables_changed) for tool in contract.tools}
    rows = []
    for row in report.tools:
        undeclared = sorted(changed_by.get(row.tool, set()) - declared.get(row.tool, set()))
        if undeclared:
            row = row.model_copy(
                update={
                    "reason": f"{row.reason}; its proof runs changed {', '.join(undeclared)}, "
                    "which its declared tables_changed does not list"
                }
            )
        rows.append(row)
    return ToolCertificationReport.create(rows), skipped


__all__ = [
    "TOOL_CERTIFICATION_SCHEMA_VERSION",
    "ToolAvailability",
    "ToolCertification",
    "ToolCertificationReport",
    "ToolProbeMode",
    "certify_tool_inventory",
    "compare_declared_tables",
]
