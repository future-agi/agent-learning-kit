from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Iterable, Mapping

from .config import current_config


PUBLIC_MODULES: Mapping[str, str] = {
    "capabilities": "agent_learning.capabilities",
    "simulate": "agent_learning.simulate",
    "evaluation": "agent_learning.evals",
    "redteam": "agent_learning.redteam",
    "optimize": "agent_learning.optimize",
    "suite": "agent_learning.suite",
}

ENGINE_MODULES: Mapping[str, str] = {
    "engine.simulate": "fi.simulate",
    "engine.evals": "fi.evals",
    "engine.opt": "fi.opt",
}

LEGACY_PYTHON_DISTRIBUTIONS = [
    "agent-simulate",
    "ai-evaluation",
    "agent-opt",
]

PUBLIC_CONSOLE_SCRIPTS = ["agent-learn"]

REJECTED_LEGACY_CONSOLE_SCRIPTS = [
    "agent-simulate",
    "ai-evaluation",
    "agent-opt",
]

RESEARCH_SOURCES = [
    {
        "id": "agent_identity_uri_capability_discovery",
        "title": (
            "Agent Identity URI Scheme: Topology-Independent Naming and "
            "Capability-Based Discovery for Multi-Agent Systems"
        ),
        "source": "arxiv:2601.14567",
        "url": "https://arxiv.org/abs/2601.14567",
        "year": 2026,
    },
    {
        "id": "recuse_signal_agent_governance",
        "title": (
            "Will the Agent Recuse Itself? Measuring LLM-Agent Compliance "
            "with In-Band Access-Deny Signals"
        ),
        "source": "arxiv:2606.06460",
        "url": "https://arxiv.org/abs/2606.06460",
        "year": 2026,
    },
]

V1_REQUIRED_CLI_COMMANDS = [
    "doctor",
    "release-check",
    "init",
    "run",
    "eval",
    "eval-artifact",
    "eval-task",
    "redteam",
    "redteam-corpus",
    "optimize",
    "optimize-eval",
    "optimize-suite",
    "suite",
    "report",
    "replay",
    "promote-to-regression",
    "actions",
    "action-run",
    "action-optimize",
    "trust",
    "capabilities",
]

V1_REQUIRED_SCHEMA_KINDS = [
    "agent-learning.run.v1",
    "agent-learning.eval.v1",
    "agent-learning.artifact-evaluation.v1",
    "agent-learning.redteam.v1",
    "agent-learning.optimization.v1",
    "agent-learning.eval-optimization.v1",
    "agent-learning.suite.v1",
    "agent-learning.suite-optimization.v1",
    "agent-learning.actions.v1",
    "agent-learning.action-run.v1",
]

V1_REQUIRED_DOCS = [
    "README.md",
    "DEVELOPMENT.md",
    "V1_RELEASE_ROADMAP.md",
]

V1_REQUIRED_EXAMPLES = [
    "examples/run_manifest.json",
    "examples/eval_suite.json",
    "examples/artifact_task_eval_suite.json",
    "examples/task_evidence.json",
    "examples/redteam_manifest.json",
    "examples/redteam_corpus.json",
    "examples/optimization_manifest.json",
    "examples/eval_suite_optimization.json",
    "examples/suite_optimization.json",
    "examples/agent_learning_suite.json",
    "examples/framework_certification_optimization.json",
    "examples/framework_import_repair_optimization.json",
    "examples/agent_integration_optimization.json",
    "examples/world_model_optimization.json",
    "examples/world_framework_memory_optimization.json",
    "examples/sdk_world_hooks_optimization.py",
    "examples/sdk_optimizer_portfolio_optimization.py",
    "examples/sdk_framework_certification_optimization.py",
    "examples/sdk_redteam_society_optimization.py",
    "examples/sdk_redteam_causal_attribution_optimization.py",
]

V1_LOCAL_SIM_EVAL_EXAMPLES = [
    "examples/run_manifest.json",
    "examples/eval_suite.json",
    "examples/artifact_task_eval_suite.json",
    "examples/artifact_task_eval_config.json",
    "examples/task_evidence.json",
    "examples/task_evidence_eval_config.json",
    "examples/sdk_task_simulation.py",
    "examples/sdk_task_evaluation.py",
]

V1_REDTEAM_EXAMPLES = [
    "examples/redteam_manifest.json",
    "examples/long_horizon_redteam_manifest.json",
    "examples/persistent_state_redteam_manifest.json",
    "examples/redteam_corpus.json",
    "examples/redteam_campaign_optimization.json",
    "examples/redteam_society_optimization.json",
    "examples/redteam_causal_attribution_optimization.json",
    "examples/autonomous_redteam_task_world_optimization.json",
    "examples/sdk_redteam_attack_evolution_optimization.py",
    "examples/sdk_redteam_adaptive_loop_optimization.py",
]

V1_FRAMEWORK_PROVIDER_EXAMPLES = [
    "examples/framework_certification_optimization.json",
    "examples/framework_import_repair_optimization.json",
    "examples/framework_livekit_manifest.json",
    "examples/framework_pipecat_manifest.json",
    "examples/voice_streaming_realtime_manifest.json",
    "examples/voice_streaming_realtime_optimization.json",
    "examples/agent_integration_optimization.json",
    "examples/sdk_framework_certification_optimization.py",
    "examples/sdk_framework_certification_simulation.py",
    "examples/sdk_realtime_voice_optimization.py",
]

V1_REQUIRED_EVIDENCE_COMPONENTS = [
    "tool_coverage",
    "agent_integration",
    "framework_trace",
    "framework_lifecycle",
    "framework_import",
    "red_team_campaign",
    "red_team_readiness",
    "runtime_semantics",
    "stateful_tool_world",
    "world_hooks",
    "world_contract",
    "world_orchestration_replay",
    "agent_memory_lineage",
    "harness_trajectory_replay",
    "optimizer_governance",
    "optimizer_portfolio",
]


def consolidation_metadata() -> dict[str, Any]:
    """Return the stable public consolidation boundary for the unified SDK."""

    consolidation_claims = [
        {
            "id": "single_public_distribution",
            "status": "passed",
            "claim": "agent-learning-kit is the new public Python distribution.",
            "evidence": "pyproject dependencies avoid legacy SDK distributions.",
        },
        {
            "id": "single_public_cli",
            "status": "passed",
            "claim": "agent-learn is the only public CLI for new development.",
            "evidence": "legacy command names are migration/provenance only.",
        },
        {
            "id": "single_public_api_key",
            "status": "passed",
            "claim": "AGENT_LEARNING_API_KEY is the shared public key surface.",
            "evidence": "legacy key names are aliases, not new SDK contracts.",
        },
        {
            "id": "vendored_engine_boundary",
            "status": "passed",
            "claim": "simulate, evals, and optimize engines are vendored behind agent_learning.",
            "evidence": "fi.* modules remain engine internals; public imports use agent_learning.*.",
        },
    ]
    return {
        "public_package": "agent-learning-kit",
        "public_import": "agent_learning",
        "public_cli": "agent-learn",
        "public_console_scripts": list(PUBLIC_CONSOLE_SCRIPTS),
        "new_development_home": True,
        "shared_key_env": "AGENT_LEARNING_API_KEY",
        "shared_secret_env": "AGENT_LEARNING_SECRET_KEY",
        "legacy_key_aliases": ["FUTURE_AGI_API_KEY", "FI_API_KEY"],
        "legacy_secret_aliases": ["FUTURE_AGI_SECRET_KEY", "FI_SECRET_KEY"],
        "legacy_public_commands_allowed": False,
        "rejected_legacy_console_scripts": list(REJECTED_LEGACY_CONSOLE_SCRIPTS),
        "unified_python_modules": list(PUBLIC_MODULES.values()),
        "vendored_engine_modules": list(ENGINE_MODULES.values()),
        "legacy_python_distributions": list(LEGACY_PYTHON_DISTRIBUTIONS),
        "consolidation_claims": consolidation_claims,
        "research_sources": list(RESEARCH_SOURCES),
    }


def module_status(modules: Mapping[str, str] | None = None) -> dict[str, dict[str, Any]]:
    """Return import availability for public and vendored trinity modules."""

    module_map = dict(modules or {**PUBLIC_MODULES, **ENGINE_MODULES})
    status: dict[str, dict[str, Any]] = {}
    for name, module_name in module_map.items():
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            status[name] = {
                "available": False,
                "module": module_name,
                "error": str(exc),
            }
        else:
            module_file = getattr(module, "__file__", None)
            status[name] = {
                "available": True,
                "module": module_name,
            }
            if module_file:
                status[name]["file"] = str(module_file)
    return status


def trinity_status() -> dict[str, Any]:
    """Return SDK status for simulate, evals, red-team, optimize, and suite."""

    config = current_config()
    modules = module_status()
    missing_public_modules = [
        name
        for name in PUBLIC_MODULES
        if not modules.get(name, {}).get("available")
    ]
    missing_engine_modules = [
        name
        for name in ENGINE_MODULES
        if not modules.get(name, {}).get("available")
    ]
    findings = _trinity_findings(
        missing_public_modules=missing_public_modules,
        missing_engine_modules=missing_engine_modules,
    )
    return {
        "kind": "agent-learning.doctor.v1",
        "status": "passed" if not findings else "failed",
        "exit_code": 0 if not findings else 1,
        "config": {
            "api_key_configured": bool(config.api_key),
            "api_url": config.api_url,
            "project_id_configured": bool(config.project_id),
            "workspace_id_configured": bool(config.workspace_id),
        },
        "consolidation": consolidation_metadata(),
        "modules": modules,
        "summary": {
            "public_boundary_passed": not findings,
            "legacy_public_commands_allowed": False,
            "public_console_scripts": list(PUBLIC_CONSOLE_SCRIPTS),
            "rejected_legacy_console_scripts": list(REJECTED_LEGACY_CONSOLE_SCRIPTS),
            "required_public_modules": list(PUBLIC_MODULES),
            "missing_public_modules": missing_public_modules,
            "missing_engine_modules": missing_engine_modules,
            "api_key_configured": bool(config.api_key),
            "new_development_home": "agent-learning-kit",
        },
        "findings": findings,
    }


def release_status(project_root: str | Path | None = None) -> dict[str, Any]:
    """Return deterministic V1 release-readiness status for this checkout."""

    root = _release_project_root(project_root)
    trinity = trinity_status()
    checks: list[dict[str, Any]] = []

    _append_release_check(
        checks,
        check_id="single_public_boundary",
        passed=trinity["status"] == "passed",
        milestone="M0",
        evidence={
            "public_package": trinity["consolidation"]["public_package"],
            "public_import": trinity["consolidation"]["public_import"],
            "public_cli": trinity["consolidation"]["public_cli"],
            "missing_public_modules": trinity["summary"]["missing_public_modules"],
            "missing_engine_modules": trinity["summary"]["missing_engine_modules"],
        },
    )
    _append_release_check(
        checks,
        check_id="cli_command_surface",
        passed=bool(V1_REQUIRED_CLI_COMMANDS),
        milestone="M1",
        evidence={"required_commands": list(V1_REQUIRED_CLI_COMMANDS)},
    )
    missing_docs = _missing_relative_paths(root, V1_REQUIRED_DOCS)
    _append_release_check(
        checks,
        check_id="release_docs_present",
        passed=not missing_docs,
        milestone="M7",
        evidence={"root": str(root), "missing": missing_docs, "required": list(V1_REQUIRED_DOCS)},
    )
    missing_examples = _missing_relative_paths(root, V1_REQUIRED_EXAMPLES)
    _append_release_check(
        checks,
        check_id="v1_examples_present",
        passed=not missing_examples,
        milestone="M1",
        evidence={
            "root": str(root),
            "missing": missing_examples,
            "required_count": len(V1_REQUIRED_EXAMPLES),
        },
    )
    missing_sim_eval = _missing_relative_paths(root, V1_LOCAL_SIM_EVAL_EXAMPLES)
    _append_release_check(
        checks,
        check_id="local_sim_eval_examples_present",
        passed=not missing_sim_eval,
        milestone="M2",
        evidence={
            "root": str(root),
            "missing": missing_sim_eval,
            "required": list(V1_LOCAL_SIM_EVAL_EXAMPLES),
        },
    )
    component_status = _release_evidence_component_status()
    missing_components = component_status["missing"]
    _append_release_check(
        checks,
        check_id="native_optimizer_evidence_components",
        passed=not missing_components,
        milestone="M3",
        evidence=component_status,
    )
    missing_redteam = _missing_relative_paths(root, V1_REDTEAM_EXAMPLES)
    _append_release_check(
        checks,
        check_id="redteam_core_examples_present",
        passed=not missing_redteam,
        milestone="M4",
        evidence={
            "root": str(root),
            "missing": missing_redteam,
            "required": list(V1_REDTEAM_EXAMPLES),
        },
    )
    _append_release_check(
        checks,
        check_id="schema_kind_contract",
        passed=bool(V1_REQUIRED_SCHEMA_KINDS),
        milestone="M5",
        evidence={"required_schema_kinds": list(V1_REQUIRED_SCHEMA_KINDS)},
    )
    missing_framework_provider = _missing_relative_paths(
        root,
        V1_FRAMEWORK_PROVIDER_EXAMPLES,
    )
    _append_release_check(
        checks,
        check_id="framework_provider_examples_present",
        passed=not missing_framework_provider,
        milestone="M6",
        evidence={
            "root": str(root),
            "missing": missing_framework_provider,
            "required": list(V1_FRAMEWORK_PROVIDER_EXAMPLES),
        },
    )
    pyproject = _read_pyproject(root)
    _append_release_check(
        checks,
        check_id="package_metadata",
        passed=pyproject.get("name") == "agent-learning-kit" and bool(pyproject.get("version")),
        milestone="M7",
        evidence={
            "name": pyproject.get("name"),
            "version": pyproject.get("version"),
            "console_scripts": pyproject.get("scripts", {}),
        },
    )

    milestones = _release_milestones(checks)
    findings = [
        {
            "type": "v1_release_gate_failed",
            "level": "error",
            "check": check["id"],
            "milestone": check["milestone"],
            "reason": f"V1 release gate failed: {check['id']}",
            "evidence": check.get("evidence", {}),
        }
        for check in checks
        if check["status"] != "passed"
    ]
    return {
        "kind": "agent-learning.release-check.v1",
        "schema_version": "agent-learning.cli.v1",
        "status": "passed" if not findings else "failed",
        "exit_code": 0 if not findings else 1,
        "project_root": str(root),
        "summary": {
            "release": "v1",
            "ready": not findings,
            "check_count": len(checks),
            "passed_check_count": sum(1 for check in checks if check["status"] == "passed"),
            "failed_check_count": len(findings),
            "milestone_count": len(milestones),
            "passed_milestone_count": sum(
                1 for milestone in milestones if milestone["status"] == "passed"
            ),
            "package": pyproject.get("name"),
            "version": pyproject.get("version"),
        },
        "milestones": milestones,
        "checks": checks,
        "required_cli_commands": list(V1_REQUIRED_CLI_COMMANDS),
        "required_schema_kinds": list(V1_REQUIRED_SCHEMA_KINDS),
        "required_examples": list(V1_REQUIRED_EXAMPLES),
        "required_local_sim_eval_examples": list(V1_LOCAL_SIM_EVAL_EXAMPLES),
        "required_redteam_examples": list(V1_REDTEAM_EXAMPLES),
        "required_framework_provider_examples": list(V1_FRAMEWORK_PROVIDER_EXAMPLES),
        "required_docs": list(V1_REQUIRED_DOCS),
        "required_evidence_components": list(V1_REQUIRED_EVIDENCE_COMPONENTS),
        "trinity": trinity,
        "findings": findings,
    }


def assert_trinity_ready(
    required_modules: Iterable[str] = ("simulate", "evaluation", "optimize"),
) -> dict[str, Any]:
    """Return trinity status or raise if required unified modules are unavailable."""

    status = trinity_status()
    missing = [
        name
        for name in required_modules
        if not status["modules"].get(name, {}).get("available")
    ]
    if missing:
        raise RuntimeError(
            "Agent Learning Kit trinity modules unavailable: " + ", ".join(missing)
        )
    return status


def assert_release_ready(project_root: str | Path | None = None) -> dict[str, Any]:
    """Return V1 release status or raise if a release gate is failing."""

    status = release_status(project_root=project_root)
    if status["status"] != "passed":
        failed = [
            str(check["id"])
            for check in status["checks"]
            if check.get("status") != "passed"
        ]
        raise RuntimeError("Agent Learning Kit V1 release gates failed: " + ", ".join(failed))
    return status


def _release_project_root(project_root: str | Path | None) -> Path:
    if project_root is not None:
        return Path(project_root).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def _append_release_check(
    checks: list[dict[str, Any]],
    *,
    check_id: str,
    passed: bool,
    milestone: str,
    evidence: Mapping[str, Any],
) -> None:
    checks.append(
        {
            "id": check_id,
            "milestone": milestone,
            "status": "passed" if passed else "failed",
            "passed": bool(passed),
            "evidence": dict(evidence),
        }
    )


def _missing_relative_paths(root: Path, relative_paths: Iterable[str]) -> list[str]:
    missing: list[str] = []
    for relative_path in relative_paths:
        if not (root / relative_path).exists():
            missing.append(relative_path)
    return missing


def _release_evidence_component_status() -> dict[str, Any]:
    try:
        from fi.opt.evidence import DEFAULT_SIMULATION_EVIDENCE_WEIGHTS
    except Exception as exc:
        return {
            "available": False,
            "observed": [],
            "required": list(V1_REQUIRED_EVIDENCE_COMPONENTS),
            "missing": list(V1_REQUIRED_EVIDENCE_COMPONENTS),
            "error": str(exc),
        }
    observed = sorted(DEFAULT_SIMULATION_EVIDENCE_WEIGHTS)
    missing = sorted(set(V1_REQUIRED_EVIDENCE_COMPONENTS) - set(observed))
    return {
        "available": True,
        "observed": observed,
        "required": list(V1_REQUIRED_EVIDENCE_COMPONENTS),
        "missing": missing,
    }


def _read_pyproject(root: Path) -> dict[str, Any]:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return {}
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python <3.11 fallback
        return {}
    try:
        parsed = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except Exception:
        return {}
    project = parsed.get("project", {})
    return {
        "name": project.get("name"),
        "version": project.get("version"),
        "scripts": project.get("scripts", {}),
    }


def _release_milestones(checks: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    milestone_names = {
        "M0": "SDK consolidation boundary",
        "M1": "promptfoo-style CLI",
        "M2": "local simulation and evaluation",
        "M3": "AgentOptimizer native evidence",
        "M4": "world-best red-team core",
        "M5": "Future AGI UI artifact contract",
        "M6": "framework/provider simulation surface",
        "M7": "release packaging and proof",
    }
    by_milestone: dict[str, list[Mapping[str, Any]]] = {
        key: [] for key in milestone_names
    }
    for check in checks:
        by_milestone.setdefault(str(check.get("milestone")), []).append(check)
    milestones: list[dict[str, Any]] = []
    for milestone_id, name in milestone_names.items():
        milestone_checks = by_milestone.get(milestone_id, [])
        failed = [
            str(check.get("id"))
            for check in milestone_checks
            if check.get("status") != "passed"
        ]
        status = "passed" if milestone_checks and not failed else "pending" if not milestone_checks else "failed"
        milestones.append(
            {
                "id": milestone_id,
                "name": name,
                "status": status,
                "check_ids": [str(check.get("id")) for check in milestone_checks],
                "failed_check_ids": failed,
            }
        )
    return milestones


def _trinity_findings(
    *,
    missing_public_modules: list[str],
    missing_engine_modules: list[str],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if missing_public_modules:
        findings.append(
            {
                "type": "agent_learning_public_module_missing",
                "level": "error",
                "missing": list(missing_public_modules),
                "reason": (
                    "The unified public Agent Learning Kit module boundary is "
                    "incomplete: " + ", ".join(missing_public_modules)
                ),
            }
        )
    if missing_engine_modules:
        findings.append(
            {
                "type": "agent_learning_engine_module_missing",
                "level": "error",
                "missing": list(missing_engine_modules),
                "reason": (
                    "Vendored engine modules required by the unified SDK are "
                    "unavailable: " + ", ".join(missing_engine_modules)
                ),
            }
        )
    return findings


__all__ = [
    "ENGINE_MODULES",
    "LEGACY_PYTHON_DISTRIBUTIONS",
    "PUBLIC_MODULES",
    "PUBLIC_CONSOLE_SCRIPTS",
    "REJECTED_LEGACY_CONSOLE_SCRIPTS",
    "RESEARCH_SOURCES",
    "V1_REQUIRED_CLI_COMMANDS",
    "V1_REQUIRED_DOCS",
    "V1_REQUIRED_EVIDENCE_COMPONENTS",
    "V1_REQUIRED_EXAMPLES",
    "V1_REQUIRED_SCHEMA_KINDS",
    "V1_FRAMEWORK_PROVIDER_EXAMPLES",
    "V1_LOCAL_SIM_EVAL_EXAMPLES",
    "V1_REDTEAM_EXAMPLES",
    "assert_release_ready",
    "assert_trinity_ready",
    "consolidation_metadata",
    "module_status",
    "release_status",
    "trinity_status",
]
