from __future__ import annotations

import importlib
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
    "assert_trinity_ready",
    "consolidation_metadata",
    "module_status",
    "trinity_status",
]
