from __future__ import annotations

import copy
import importlib
import json
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

LEGACY_TYPESCRIPT_PACKAGES = [
    "@future-agi/ai-evaluation",
]

PUBLIC_CONSOLE_SCRIPTS = ["agent-learn"]

REJECTED_LEGACY_CONSOLE_SCRIPTS = [
    "agent-simulate",
    "ai-evaluation",
    "agent-opt",
]

TYPESCRIPT_PUBLIC_PACKAGE = "@future-agi/agent-learning-kit"

V1_TYPESCRIPT_SDK_REQUIRED_FILES = [
    "typescript/package.json",
    "typescript/pnpm-workspace.yaml",
    "typescript/pnpm-lock.yaml",
    "typescript/tsconfig.json",
    "typescript/agent-learning-kit/package.json",
    "typescript/agent-learning-kit/src/index.ts",
    "typescript/agent-learning-kit/src/local/index.ts",
    "typescript/agent-learning-kit/examples/02-local-heuristic-metrics.ts",
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
    "release-proof",
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
    "agent-learning.release-proof.v1",
]

V1_RELEASE_PROOF_REQUIRED_CHECKS = [
    "release_check",
    "ruff",
    "pytest",
    "build",
    "typescript_build",
    "typescript_test",
    "git_diff_check",
]

V1_UI_ACTION_REPORT_ARTIFACTS = [
    {
        "path": "examples/fixtures/task_artifacts/refund_task_run.json",
        "source_kind": "agent-learning.run.v1",
        "required_report_sections": ["summary", "orchestration_strategy"],
        "required_report_card_keys": ["orchestration_strategy"],
        "required_action_ids": [
            "report_artifact",
            "report_orchestration_strategy",
            "rerun_orchestration_simulation",
            "optimize_orchestration_strategy",
        ],
        "requires_outputs_written": False,
    },
    {
        "path": "examples/artifacts/action-loop/action-run.json",
        "source_kind": "agent-learning.action-run.v1",
        "required_report_sections": ["summary"],
        "required_report_card_keys": [],
        "required_action_ids": ["report_artifact"],
        "requires_outputs_written": True,
    },
    {
        "path": "examples/optimization_manifest.json",
        "source_kind": "agent-learning.optimization.v1",
        "required_report_sections": ["summary", "optimization"],
        "required_report_card_keys": ["optimizer_replay"],
        "required_action_ids": ["report_artifact", "promote_to_regression"],
        "requires_outputs_written": False,
    },
    {
        "path": "examples/redteam_manifest.json",
        "source_kind": "agent-learning.redteam.v1",
        "required_report_sections": ["summary", "redteam", "redteam_strategy"],
        "required_report_card_keys": ["redteam_strategy"],
        "required_action_ids": [
            "report_artifact",
            "report_redteam_strategy",
            "optimize_redteam_strategy",
        ],
        "requires_outputs_written": False,
    },
    {
        "path": "examples/redteam_campaign_optimization.json",
        "source_kind": "agent-learning.optimization.v1",
        "required_report_sections": [
            "summary",
            "redteam",
            "redteam_strategy",
            "optimization",
        ],
        "required_report_card_keys": ["optimizer_replay", "redteam_strategy"],
        "required_action_ids": [
            "report_artifact",
            "promote_to_regression",
            "report_redteam_strategy",
            "optimize_redteam_strategy",
        ],
        "requires_outputs_written": False,
    },
    {
        "path": "examples/agent_integration_optimization.json",
        "source_kind": "agent-learning.optimization.v1",
        "required_report_sections": ["summary", "optimization"],
        "required_report_card_keys": ["optimizer_replay"],
        "required_action_ids": ["report_artifact", "promote_to_regression"],
        "requires_outputs_written": False,
    },
    {
        "path": "examples/agent_learning_suite.json",
        "source_kind": "agent-learning.suite.v1",
        "required_report_sections": ["summary"],
        "required_report_card_keys": [],
        "required_action_ids": ["report_artifact"],
        "requires_outputs_written": False,
    },
]

V1_UI_FORBIDDEN_SECRET_MARKERS = [
    "real-local",
    "AGENT_LEARNING_API_KEY",
    "AGENT_LEARNING_SECRET_KEY",
    "FUTURE_AGI_API_KEY",
    "FUTURE_AGI_SECRET_KEY",
    "FI_API_KEY",
    "FI_SECRET_KEY",
    "api_key",
    "secret_key",
    "authorization",
    "bearer ",
]

V1_HARNESS_DIAGNOSIS_SOURCE = "examples/sdk_retrospective_harness_optimization.py"

V1_HARNESS_DIAGNOSIS_REQUIRED_ACTIONS = [
    "report_harness_diagnosis",
    "rerun_optimization_for_diagnosed_layers",
    "promote_diagnosed_regression",
]

V1_HARNESS_DIAGNOSIS_REQUIRED_LAYERS = [
    "observability",
    "verification",
]

V1_HARNESS_DIAGNOSIS_REQUIRED_RESEARCH_SOURCES = [
    "https://arxiv.org/abs/2606.06324",
    "https://arxiv.org/abs/2606.05922",
    "https://arxiv.org/abs/2606.06284",
    "https://arxiv.org/abs/2606.06473",
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
    "examples/long_horizon_redteam_optimization.json",
    "examples/persistent_state_redteam_optimization.json",
    "examples/redteam_autogen_optimization.json",
    "examples/redteam_corpus.json",
    "examples/redteam_campaign_optimization.json",
    "examples/redteam_society_optimization.json",
    "examples/redteam_causal_attribution_optimization.json",
    "examples/autonomous_redteam_task_world_optimization.json",
    "examples/sdk_redteam_attack_evolution_optimization.py",
    "examples/sdk_redteam_adaptive_loop_optimization.py",
]

V1_REDTEAM_RESEARCH_FILES = [
    "examples/redteam_corpus.json",
    "examples/redteam_campaign_optimization.json",
    "examples/redteam_autogen_optimization.json",
    "examples/long_horizon_redteam_optimization.json",
    "examples/persistent_state_redteam_optimization.json",
    "examples/redteam_society_optimization.json",
    "examples/redteam_causal_attribution_optimization.json",
    "examples/autonomous_redteam_task_world_optimization.json",
    "examples/sdk_redteam_attack_evolution_optimization.py",
    "examples/sdk_redteam_adaptive_loop_optimization.py",
]

V1_REDTEAM_RESEARCH_CORPUS_FILE = "examples/redteam_corpus.json"

V1_REDTEAM_RESEARCH_ATTACK_TYPES = [
    "prompt_injection",
    "indirect_prompt_injection",
    "adaptive_indirect_prompt_injection",
    "credential_exfiltration",
    "monitor_evasion",
    "memory_poisoning",
    "sleeper_memory_poisoning",
    "knowledge_corruption",
    "tool_chaining",
    "objective_drift",
]

V1_REDTEAM_RESEARCH_SURFACES = [
    "instruction",
    "tool",
    "memory",
    "retrieval",
    "environment",
    "long_context",
]

V1_REDTEAM_RESEARCH_SOURCE_URLS = [
    "https://arxiv.org/abs/2601.03699",
    "https://arxiv.org/abs/2601.13518",
    "https://arxiv.org/abs/2602.09222",
    "https://arxiv.org/abs/2604.28157",
    "https://arxiv.org/abs/2605.04808",
    "https://arxiv.org/abs/2605.09684",
    "https://arxiv.org/abs/2605.15338",
    "https://arxiv.org/abs/2605.17075",
    "https://arxiv.org/abs/2606.04329",
]

V1_REDTEAM_CORPUS_EXECUTION_FILE = V1_REDTEAM_RESEARCH_CORPUS_FILE

V1_REDTEAM_CORPUS_EXECUTION_FRAMEWORKS = ["agent_learning_kit"]

V1_REDTEAM_CORPUS_EXECUTION_PROVIDERS = ["local_cli"]

V1_REDTEAM_CORPUS_EXECUTION_CHANNELS = ["chat"]

V1_FRAMEWORK_PROVIDER_EXAMPLES = [
    "examples/framework_certification_optimization.json",
    "examples/framework_import_repair_optimization.json",
    "examples/multi_framework_simulation_suite.json",
    "examples/framework_langchain_manifest.json",
    "examples/framework_langgraph_manifest.json",
    "examples/framework_llamaindex_manifest.json",
    "examples/framework_openai_agents_manifest.json",
    "examples/framework_autogen_manifest.json",
    "examples/framework_crewai_manifest.json",
    "examples/framework_pydantic_ai_manifest.json",
    "examples/framework_livekit_manifest.json",
    "examples/framework_pipecat_manifest.json",
    "examples/voice_streaming_realtime_manifest.json",
    "examples/voice_streaming_realtime_optimization.json",
    "examples/agent_integration_optimization.json",
    "examples/sdk_multi_framework_simulation.py",
    "examples/sdk_framework_certification_optimization.py",
    "examples/sdk_framework_certification_simulation.py",
    "examples/sdk_realtime_voice_optimization.py",
]

V1_FRAMEWORK_PROVIDER_FRAMEWORKS = [
    "langchain",
    "langgraph",
    "llamaindex",
    "openai_agents",
    "autogen",
    "crewai",
    "pydantic_ai",
    "livekit",
    "pipecat",
]

V1_FRAMEWORK_PROVIDER_REQUIRED_MODALITIES = ["text", "voice"]

V1_FRAMEWORK_PROVIDER_REQUIRED_TRANSPORTS = ["in_process"]

V1_FRAMEWORK_PROVIDER_REQUIRED_TARGET_SCHEMES = ["agent-learning-fixture"]

V1_FRAMEWORK_PROVIDER_MANIFEST_CONTRACTS = [
    {
        "path": "examples/framework_langchain_manifest.json",
        "kind": "agent-learning.run.v1",
        "framework": "langchain",
        "modality": "text",
        "agent_type": "framework",
        "required_environment_types": ["framework_trace"],
    },
    {
        "path": "examples/framework_langgraph_manifest.json",
        "kind": "agent-learning.run.v1",
        "framework": "langgraph",
        "modality": "text",
        "agent_type": "framework",
        "required_environment_types": ["framework_trace"],
    },
    {
        "path": "examples/framework_llamaindex_manifest.json",
        "kind": "agent-learning.run.v1",
        "framework": "llamaindex",
        "modality": "text",
        "agent_type": "framework",
        "required_environment_types": ["framework_trace"],
    },
    {
        "path": "examples/framework_openai_agents_manifest.json",
        "kind": "agent-learning.run.v1",
        "framework": "openai_agents",
        "modality": "text",
        "agent_type": "framework",
        "required_environment_types": ["framework_trace"],
    },
    {
        "path": "examples/framework_autogen_manifest.json",
        "kind": "agent-learning.run.v1",
        "framework": "autogen",
        "modality": "text",
        "agent_type": "framework",
        "required_environment_types": ["framework_trace"],
    },
    {
        "path": "examples/framework_crewai_manifest.json",
        "kind": "agent-learning.run.v1",
        "framework": "crewai",
        "modality": "text",
        "agent_type": "framework",
        "required_environment_types": ["framework_trace"],
    },
    {
        "path": "examples/framework_pydantic_ai_manifest.json",
        "kind": "agent-learning.run.v1",
        "framework": "pydantic_ai",
        "modality": "text",
        "agent_type": "framework",
        "required_environment_types": ["framework_trace"],
    },
    {
        "path": "examples/framework_livekit_manifest.json",
        "kind": "agent-learning.run.v1",
        "framework": "livekit",
        "modality": "voice",
        "agent_type": "framework",
        "required_environment_types": ["framework_trace"],
    },
    {
        "path": "examples/framework_pipecat_manifest.json",
        "kind": "agent-learning.run.v1",
        "framework": "pipecat",
        "modality": "voice",
        "agent_type": "framework",
        "required_environment_types": ["framework_trace"],
    },
    {
        "path": "examples/voice_streaming_realtime_manifest.json",
        "kind": "agent-learning.run.v1",
        "framework": "livekit",
        "modality": "voice",
        "agent_type": "scripted",
        "required_environment_types": ["voice", "streaming_trace"],
    },
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
    typescript_consolidation = _release_typescript_sdk_consolidation_status(root)
    _append_release_check(
        checks,
        check_id="typescript_sdk_consolidation_boundary",
        passed=(
            not typescript_consolidation["missing_files"]
            and not typescript_consolidation["metadata_errors"]
            and not typescript_consolidation["forbidden_token_findings"]
            and not typescript_consolidation["legacy_sibling_errors"]
        ),
        milestone="M0",
        evidence=typescript_consolidation,
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
    redteam_research = _release_redteam_research_status(root)
    _append_release_check(
        checks,
        check_id="redteam_research_coverage",
        passed=(
            not redteam_research["missing_attack_types"]
            and not redteam_research["missing_surfaces"]
            and not redteam_research["missing_source_urls"]
            and not redteam_research["missing_files"]
            and not redteam_research["corpus_missing_attack_types"]
            and not redteam_research["corpus_missing_surfaces"]
            and not redteam_research["corpus_missing_source_urls"]
        ),
        milestone="M4",
        evidence=redteam_research,
    )
    redteam_corpus_execution = _release_redteam_corpus_execution_status(root)
    _append_release_check(
        checks,
        check_id="redteam_corpus_execution_readiness",
        passed=(
            not redteam_corpus_execution["missing_files"]
            and not redteam_corpus_execution["parse_errors"]
            and not redteam_corpus_execution["campaign_errors"]
            and not redteam_corpus_execution["coverage_errors"]
            and not redteam_corpus_execution["blocking_gaps"]
            and not redteam_corpus_execution["missing_attack_types"]
            and not redteam_corpus_execution["missing_surfaces"]
            and not redteam_corpus_execution["missing_channels"]
            and not redteam_corpus_execution["missing_providers"]
            and not redteam_corpus_execution["missing_frameworks"]
        ),
        milestone="M4",
        evidence=redteam_corpus_execution,
    )
    _append_release_check(
        checks,
        check_id="schema_kind_contract",
        passed=bool(V1_REQUIRED_SCHEMA_KINDS),
        milestone="M5",
        evidence={"required_schema_kinds": list(V1_REQUIRED_SCHEMA_KINDS)},
    )
    ui_action_report = _release_ui_action_report_status(root)
    _append_release_check(
        checks,
        check_id="ui_action_report_readiness",
        passed=(
            not ui_action_report["missing_files"]
            and not ui_action_report["failing_reports"]
            and not ui_action_report["missing_report_sections"]
            and not ui_action_report["missing_report_card_keys"]
            and not ui_action_report["missing_action_ids"]
            and not ui_action_report["missing_output_evidence"]
            and not ui_action_report["secret_marker_findings"]
            and not ui_action_report["errors"]
        ),
        milestone="M5",
        evidence=ui_action_report,
    )
    harness_diagnosis = _release_harness_diagnosis_status(root)
    _append_release_check(
        checks,
        check_id="harness_diagnosis_readiness",
        passed=(
            not harness_diagnosis["missing_files"]
            and not harness_diagnosis["optimization_errors"]
            and not harness_diagnosis["report_errors"]
            and not harness_diagnosis["diagnosis_errors"]
            and not harness_diagnosis["action_errors"]
            and not harness_diagnosis["rollout_errors"]
            and not harness_diagnosis["proof_errors"]
            and not harness_diagnosis["secret_marker_findings"]
        ),
        milestone="M5",
        evidence=harness_diagnosis,
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
    framework_provider_contract = _release_framework_provider_contract_status(root)
    _append_release_check(
        checks,
        check_id="framework_provider_contract_readiness",
        passed=(
            not framework_provider_contract["missing_files"]
            and not framework_provider_contract["matrix_errors"]
            and not framework_provider_contract["contract_errors"]
            and not framework_provider_contract["manifest_errors"]
            and not framework_provider_contract["external_value_findings"]
            and not framework_provider_contract["errors"]
        ),
        milestone="M6",
        evidence=framework_provider_contract,
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
        "typescript_public_package": TYPESCRIPT_PUBLIC_PACKAGE,
        "legacy_typescript_packages": list(LEGACY_TYPESCRIPT_PACKAGES),
        "required_typescript_sdk_files": list(V1_TYPESCRIPT_SDK_REQUIRED_FILES),
        "required_schema_kinds": list(V1_REQUIRED_SCHEMA_KINDS),
        "required_examples": list(V1_REQUIRED_EXAMPLES),
        "required_local_sim_eval_examples": list(V1_LOCAL_SIM_EVAL_EXAMPLES),
        "required_redteam_examples": list(V1_REDTEAM_EXAMPLES),
        "required_redteam_research_corpus_file": V1_REDTEAM_RESEARCH_CORPUS_FILE,
        "required_redteam_research_files": list(V1_REDTEAM_RESEARCH_FILES),
        "required_redteam_research_attack_types": list(V1_REDTEAM_RESEARCH_ATTACK_TYPES),
        "required_redteam_research_surfaces": list(V1_REDTEAM_RESEARCH_SURFACES),
        "required_redteam_research_source_urls": list(V1_REDTEAM_RESEARCH_SOURCE_URLS),
        "required_redteam_corpus_execution_file": V1_REDTEAM_CORPUS_EXECUTION_FILE,
        "required_redteam_corpus_execution_frameworks": list(
            V1_REDTEAM_CORPUS_EXECUTION_FRAMEWORKS
        ),
        "required_redteam_corpus_execution_providers": list(
            V1_REDTEAM_CORPUS_EXECUTION_PROVIDERS
        ),
        "required_redteam_corpus_execution_channels": list(
            V1_REDTEAM_CORPUS_EXECUTION_CHANNELS
        ),
        "required_ui_action_report_artifacts": copy.deepcopy(
            V1_UI_ACTION_REPORT_ARTIFACTS
        ),
        "forbidden_ui_secret_markers": list(V1_UI_FORBIDDEN_SECRET_MARKERS),
        "required_harness_diagnosis_source": V1_HARNESS_DIAGNOSIS_SOURCE,
        "required_harness_diagnosis_actions": list(
            V1_HARNESS_DIAGNOSIS_REQUIRED_ACTIONS
        ),
        "required_harness_diagnosis_layers": list(
            V1_HARNESS_DIAGNOSIS_REQUIRED_LAYERS
        ),
        "required_harness_diagnosis_research_sources": list(
            V1_HARNESS_DIAGNOSIS_REQUIRED_RESEARCH_SOURCES
        ),
        "required_release_proof_checks": list(V1_RELEASE_PROOF_REQUIRED_CHECKS),
        "required_framework_provider_examples": list(V1_FRAMEWORK_PROVIDER_EXAMPLES),
        "required_framework_provider_frameworks": list(
            V1_FRAMEWORK_PROVIDER_FRAMEWORKS
        ),
        "required_framework_provider_modalities": list(
            V1_FRAMEWORK_PROVIDER_REQUIRED_MODALITIES
        ),
        "required_framework_provider_transports": list(
            V1_FRAMEWORK_PROVIDER_REQUIRED_TRANSPORTS
        ),
        "required_framework_provider_target_schemes": list(
            V1_FRAMEWORK_PROVIDER_REQUIRED_TARGET_SCHEMES
        ),
        "required_framework_provider_manifest_contracts": copy.deepcopy(
            V1_FRAMEWORK_PROVIDER_MANIFEST_CONTRACTS
        ),
        "required_docs": list(V1_REQUIRED_DOCS),
        "required_evidence_components": list(V1_REQUIRED_EVIDENCE_COMPONENTS),
        "trinity": trinity,
        "findings": findings,
    }


def release_proof_status(
    project_root: str | Path | None = None,
    *,
    command_results: Mapping[str, Mapping[str, Any]] | None = None,
    selected_check_ids: Iterable[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Return a Future AGI-ready V1 release-proof artifact.

    ``release_status()`` is intentionally fast and deterministic. This artifact
    records the heavier local proof stack used when cutting V1: release-check,
    ruff, pytest, package build, TypeScript build/test, and git diff hygiene.
    """

    root = _release_project_root(project_root)
    required_checks = list(V1_RELEASE_PROOF_REQUIRED_CHECKS)
    raw_selected = [str(item) for item in (selected_check_ids or required_checks)]
    selected: list[str] = []
    seen_selected: set[str] = set()
    for check_id in raw_selected:
        if check_id in seen_selected:
            continue
        selected.append(check_id)
        seen_selected.add(check_id)
    required_set = set(required_checks)
    unknown_selected = [check_id for check_id in selected if check_id not in required_set]
    selected_required = [
        check_id for check_id in required_checks if check_id in seen_selected
    ]
    selected_set = set(selected_required)
    results = {
        str(key): dict(value)
        for key, value in dict(command_results or {}).items()
    }
    checks: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []

    for check_id in unknown_selected:
        findings.append(
            {
                "type": "v1_release_proof_unknown_check",
                "level": "error",
                "check": check_id,
                "reason": f"Unknown V1 release proof check: {check_id}",
                "allowed_check_ids": required_checks,
            }
        )

    for check_id in required_checks:
        required = check_id in selected_set
        raw = results.get(check_id)
        if raw is None:
            status = "skipped" if not required else "pending" if dry_run else "failed"
            exit_code = None
            evidence: dict[str, Any] = {
                "reason": "check was not selected" if not required else "check did not run"
            }
        else:
            exit_code = raw.get("exit_code")
            status = "passed" if exit_code == 0 else "failed"
            evidence = dict(raw)
        check = {
            "id": check_id,
            "required": required,
            "status": status,
            "passed": status == "passed" or (status == "skipped" and not required),
            "exit_code": exit_code,
            "evidence": evidence,
        }
        checks.append(check)
        if required and status != "passed":
            pending = dry_run and status == "pending"
            findings.append(
                {
                    "type": (
                        "v1_release_proof_check_pending"
                        if pending
                        else "v1_release_proof_check_failed"
                    ),
                    "level": "warning" if pending else "error",
                    "check": check_id,
                    "reason": (
                        f"V1 release proof check pending: {check_id}"
                        if pending
                        else f"V1 release proof check failed: {check_id}"
                    ),
                    "evidence": evidence,
                }
            )

    full_proof = not unknown_selected and selected_set == required_set
    if not full_proof:
        findings.append(
            {
                "type": "v1_release_proof_partial",
                "level": "warning",
                "selected_check_ids": selected_required,
                "required_check_ids": required_checks,
                "unknown_selected_check_ids": unknown_selected,
                "reason": "This artifact proves only the selected release checks.",
            }
        )
    error_findings = [item for item in findings if item["level"] == "error"]
    if error_findings:
        status = "failed"
    elif dry_run:
        status = "planned"
    else:
        status = "passed"
    return {
        "kind": "agent-learning.release-proof.v1",
        "schema_version": "agent-learning.cli.v1",
        "status": status,
        "exit_code": 1 if error_findings else 0,
        "project_root": str(root),
        "dry_run": bool(dry_run),
        "summary": {
            "release": "v1",
            "ready": status == "passed" and full_proof,
            "full_proof": full_proof,
            "required_check_count": len(required_checks),
            "selected_check_count": len(selected_required),
            "unknown_selected_check_count": len(unknown_selected),
            "passed_check_count": sum(
                1
                for check in checks
                if check["required"] and check["status"] == "passed"
            ),
            "failed_check_count": sum(
                1
                for check in checks
                if check["required"] and check["status"] == "failed"
            ),
            "pending_check_count": sum(
                1
                for check in checks
                if check["required"] and check["status"] == "pending"
            ),
            "skipped_check_count": sum(1 for check in checks if check["status"] == "skipped"),
        },
        "required_check_ids": required_checks,
        "selected_check_ids": selected_required,
        "unknown_selected_check_ids": unknown_selected,
        "checks": checks,
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


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _release_typescript_sdk_consolidation_status(root: Path) -> dict[str, Any]:
    package_root = root / "typescript" / "agent-learning-kit"
    package_json_path = package_root / "package.json"
    workspace_package_json_path = root / "typescript" / "package.json"
    package_json = _read_json_file(package_json_path)
    workspace_package_json = _read_json_file(workspace_package_json_path)
    missing_files = _missing_relative_paths(root, V1_TYPESCRIPT_SDK_REQUIRED_FILES)
    metadata_errors: list[dict[str, Any]] = []

    if package_json.get("name") != TYPESCRIPT_PUBLIC_PACKAGE:
        metadata_errors.append(
            {
                "field": "typescript/agent-learning-kit/package.json:name",
                "expected": TYPESCRIPT_PUBLIC_PACKAGE,
                "actual": package_json.get("name"),
            }
        )
    exports = package_json.get("exports", {})
    if not isinstance(exports, dict) or "./evals" not in exports:
        metadata_errors.append(
            {
                "field": "typescript/agent-learning-kit/package.json:exports",
                "expected": "./evals",
                "actual": sorted(exports) if isinstance(exports, dict) else exports,
            }
        )
    if not isinstance(exports, dict) or "./evals/local" not in exports:
        metadata_errors.append(
            {
                "field": "typescript/agent-learning-kit/package.json:exports",
                "expected": "./evals/local",
                "actual": sorted(exports) if isinstance(exports, dict) else exports,
            }
        )
    package_bin = package_json.get("bin", {})
    if isinstance(package_bin, dict) and "fi" in package_bin:
        metadata_errors.append(
            {
                "field": "typescript/agent-learning-kit/package.json:bin.fi",
                "expected": "absent",
                "actual": package_bin["fi"],
            }
        )
    workspace_deps = workspace_package_json.get("dependencies", {})
    if not isinstance(workspace_deps, dict) or (
        workspace_deps.get(TYPESCRIPT_PUBLIC_PACKAGE) != "workspace:*"
    ):
        metadata_errors.append(
            {
                "field": "typescript/package.json:dependencies",
                "expected": {TYPESCRIPT_PUBLIC_PACKAGE: "workspace:*"},
                "actual": workspace_deps,
            }
        )

    forbidden_token_findings: list[dict[str, Any]] = []
    scan_suffixes = {".cjs", ".json", ".md", ".ts", ".yaml", ".yml"}
    scan_roots = [root / "typescript"]
    for scan_root in scan_roots:
        if not scan_root.exists():
            continue
        for path in sorted(scan_root.rglob("*")):
            if not path.is_file() or path.suffix not in scan_suffixes:
                continue
            if any(part in {"dist", "node_modules"} for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for forbidden in LEGACY_TYPESCRIPT_PACKAGES:
                if forbidden in text:
                    forbidden_token_findings.append(
                        {
                            "path": str(path.relative_to(root)),
                            "token": forbidden,
                        }
                    )
            if '"fi"' in text or "dist/src/cli/main.js" in text:
                forbidden_token_findings.append(
                    {
                        "path": str(path.relative_to(root)),
                        "token": "legacy fi TypeScript CLI",
                    }
                )

    legacy_sibling = root.parent / "ai-evaluation" / "typescript" / "ai-evaluation"
    legacy_sibling_errors: list[dict[str, Any]] = []
    legacy_sibling_status: dict[str, Any]
    if legacy_sibling.exists():
        legacy_package_json = _read_json_file(legacy_sibling / "package.json")
        legacy_source_files = [
            str(path.relative_to(legacy_sibling))
            for path in sorted((legacy_sibling / "src").rglob("*"))
            if path.is_file()
        ] if (legacy_sibling / "src").exists() else []
        legacy_name = legacy_package_json.get("name")
        legacy_sibling_status = {
            "path": str(legacy_sibling),
            "exists": True,
            "package_name": legacy_name,
            "source_file_count": len(legacy_source_files),
            "source_files_sample": legacy_source_files[:10],
        }
        if legacy_name in LEGACY_TYPESCRIPT_PACKAGES and legacy_source_files:
            legacy_sibling_errors.append(
                {
                    "path": str(legacy_sibling),
                    "reason": "legacy TypeScript eval SDK still has active source files",
                    "package_name": legacy_name,
                    "source_file_count": len(legacy_source_files),
                }
            )
    else:
        legacy_sibling_status = {"exists": False}

    return {
        "package_root": str(package_root),
        "package_name": package_json.get("name"),
        "workspace_dependencies": workspace_package_json.get("dependencies", {}),
        "required_files": list(V1_TYPESCRIPT_SDK_REQUIRED_FILES),
        "missing_files": missing_files,
        "metadata_errors": metadata_errors,
        "forbidden_tokens": list(LEGACY_TYPESCRIPT_PACKAGES),
        "forbidden_token_findings": forbidden_token_findings,
        "legacy_sibling": legacy_sibling_status,
        "legacy_sibling_errors": legacy_sibling_errors,
    }


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


def _release_redteam_research_status(root: Path) -> dict[str, Any]:
    missing_files = _missing_relative_paths(root, V1_REDTEAM_RESEARCH_FILES)
    tokens: set[str] = set()
    corpus_tokens: set[str] = set()
    observed_source_urls: set[str] = set()
    corpus_source_urls: set[str] = set()
    scanned_files: list[str] = []
    parse_errors: dict[str, str] = {}

    for relative_path in V1_REDTEAM_RESEARCH_FILES:
        path = root / relative_path
        if not path.exists():
            continue
        scanned_files.append(relative_path)
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:
            parse_errors[relative_path] = str(exc)
            continue
        file_tokens: set[str] = set()
        file_source_urls = {
            source_url
            for source_url in V1_REDTEAM_RESEARCH_SOURCE_URLS
            if source_url in text
        }
        observed_source_urls.update(file_source_urls)
        if path.suffix == ".json":
            try:
                payload = json.loads(text)
            except Exception as exc:
                parse_errors[relative_path] = str(exc)
                continue
            _collect_release_redteam_tokens(payload, file_tokens)
        else:
            _collect_release_text_tokens(text, file_tokens)
        tokens.update(file_tokens)
        if relative_path == V1_REDTEAM_RESEARCH_CORPUS_FILE:
            corpus_tokens.update(file_tokens)
            corpus_source_urls.update(file_source_urls)

    required_attacks = {_release_norm(item) for item in V1_REDTEAM_RESEARCH_ATTACK_TYPES}
    required_surfaces = {_release_norm(item) for item in V1_REDTEAM_RESEARCH_SURFACES}
    required_sources = set(V1_REDTEAM_RESEARCH_SOURCE_URLS)
    observed_attack_types = sorted(required_attacks & tokens)
    observed_surfaces = sorted(required_surfaces & tokens)
    corpus_observed_attack_types = sorted(required_attacks & corpus_tokens)
    corpus_observed_surfaces = sorted(required_surfaces & corpus_tokens)
    return {
        "scanned_files": scanned_files,
        "missing_files": missing_files,
        "parse_errors": parse_errors,
        "corpus_file": V1_REDTEAM_RESEARCH_CORPUS_FILE,
        "required_attack_types": list(V1_REDTEAM_RESEARCH_ATTACK_TYPES),
        "observed_attack_types": observed_attack_types,
        "missing_attack_types": sorted(required_attacks - set(observed_attack_types)),
        "corpus_observed_attack_types": corpus_observed_attack_types,
        "corpus_missing_attack_types": sorted(
            required_attacks - set(corpus_observed_attack_types)
        ),
        "required_surfaces": list(V1_REDTEAM_RESEARCH_SURFACES),
        "observed_surfaces": observed_surfaces,
        "missing_surfaces": sorted(required_surfaces - set(observed_surfaces)),
        "corpus_observed_surfaces": corpus_observed_surfaces,
        "corpus_missing_surfaces": sorted(
            required_surfaces - set(corpus_observed_surfaces)
        ),
        "required_source_urls": list(V1_REDTEAM_RESEARCH_SOURCE_URLS),
        "observed_source_urls": sorted(observed_source_urls),
        "missing_source_urls": sorted(required_sources - observed_source_urls),
        "corpus_observed_source_urls": sorted(corpus_source_urls),
        "corpus_missing_source_urls": sorted(required_sources - corpus_source_urls),
    }


def _release_redteam_corpus_execution_status(root: Path) -> dict[str, Any]:
    corpus_file = V1_REDTEAM_CORPUS_EXECUTION_FILE
    path = root / corpus_file
    missing_files = [] if path.exists() else [corpus_file]
    parse_errors: dict[str, str] = {}
    campaign_errors: list[dict[str, Any]] = []
    coverage_errors: list[dict[str, Any]] = []
    blocking_gaps: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    campaign: Mapping[str, Any] = {}
    summary: Mapping[str, Any] = {}
    metadata: Mapping[str, Any] = {}
    coverage_matrix: list[Mapping[str, Any]] = []

    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                rows = [dict(item) for item in payload if isinstance(item, Mapping)]
            elif isinstance(payload, Mapping):
                raw_rows = (
                    payload.get("rows")
                    or payload.get("corpus_rows")
                    or payload.get("attacks")
                    or payload.get("cases")
                    or []
                )
                rows = [dict(item) for item in raw_rows if isinstance(item, Mapping)]
            else:
                parse_errors[corpus_file] = (
                    f"corpus root is {type(payload).__name__}, expected object/list"
                )
        except Exception as exc:
            parse_errors[corpus_file] = str(exc)

    required_attacks = {_release_norm(item) for item in V1_REDTEAM_RESEARCH_ATTACK_TYPES}
    required_surfaces = {_release_norm(item) for item in V1_REDTEAM_RESEARCH_SURFACES}
    required_channels = {_release_norm(item) for item in V1_REDTEAM_CORPUS_EXECUTION_CHANNELS}
    required_providers = {_release_norm(item) for item in V1_REDTEAM_CORPUS_EXECUTION_PROVIDERS}
    required_frameworks = {_release_norm(item) for item in V1_REDTEAM_CORPUS_EXECUTION_FRAMEWORKS}

    if not missing_files and not parse_errors:
        if not rows:
            parse_errors[corpus_file] = "corpus contains no mapping rows"
        else:
            try:
                from agent_learning import redteam

                raw_campaign = redteam.build_redteam_corpus_campaign(
                    name="release-check-redteam-corpus",
                    corpus_rows=rows,
                    frameworks=V1_REDTEAM_CORPUS_EXECUTION_FRAMEWORKS,
                )
                if isinstance(raw_campaign, Mapping):
                    campaign = raw_campaign
                    summary = dict(campaign.get("summary") or {})
                    metadata = dict(campaign.get("metadata") or {})
                    coverage_matrix = [
                        item
                        for item in summary.get("coverage_matrix") or []
                        if isinstance(item, Mapping)
                    ]
                else:
                    campaign_errors.append(
                        {
                            "field": "campaign",
                            "expected": "mapping",
                            "observed": type(raw_campaign).__name__,
                        }
                    )
            except Exception as exc:
                campaign_errors.append({"field": "build_redteam_corpus_campaign", "error": str(exc)})

    row_count = len(rows)
    observed_attacks = {
        _release_norm(item)
        for item in summary.get("observed_attack_types") or []
    }
    observed_surfaces = {
        _release_norm(item)
        for item in summary.get("observed_surfaces") or []
    }
    observed_channels = {
        _release_norm(item)
        for item in summary.get("observed_channels") or []
    }
    observed_providers = {
        _release_norm(item)
        for item in summary.get("observed_providers") or []
    }
    observed_frameworks = {
        _release_norm(item)
        for item in summary.get("frameworks")
        or metadata.get("frameworks")
        or []
    }
    expected_counts = {
        "row_count": row_count,
        "summary.run_count": row_count,
        "summary.passed_run_count": row_count,
        "summary.failed_run_count": 0,
        "summary.coverage_cell_count": row_count,
        "summary.covered_cell_count": row_count,
        "summary.executed_cell_count": row_count,
        "summary.finding_count": row_count,
        "summary.finding_mapped_count": row_count,
        "summary.mitigation_count": row_count,
        "summary.implemented_mitigation_count": row_count,
    }
    if campaign:
        if campaign.get("kind") != "red_team_campaign":
            campaign_errors.append(
                {
                    "field": "kind",
                    "expected": "red_team_campaign",
                    "observed": campaign.get("kind"),
                }
            )
        if int(metadata.get("row_count") or 0) != row_count:
            campaign_errors.append(
                {
                    "field": "metadata.row_count",
                    "expected": row_count,
                    "observed": metadata.get("row_count"),
                }
            )
        for field, expected in expected_counts.items():
            if field == "row_count":
                continue
            summary_field = field.removeprefix("summary.")
            observed = summary.get(summary_field)
            if observed != expected:
                campaign_errors.append(
                    {
                        "field": field,
                        "expected": expected,
                        "observed": observed,
                    }
                )
        if int(summary.get("artifact_count") or 0) < row_count:
            campaign_errors.append(
                {
                    "field": "summary.artifact_count",
                    "expected_minimum": row_count,
                    "observed": summary.get("artifact_count"),
                }
            )
        matrix_len = len(coverage_matrix)
        if matrix_len != row_count:
            coverage_errors.append(
                {
                    "field": "summary.coverage_matrix",
                    "expected_count": row_count,
                    "observed_count": matrix_len,
                }
            )
        for item in coverage_matrix:
            cell_id = str(item.get("id") or "")
            for flag in (
                "has_scenario",
                "has_run",
                "has_passed_run",
                "has_executed_evidence",
                "has_artifact",
                "has_finding",
                "has_mitigation",
            ):
                if item.get(flag) is not True:
                    coverage_errors.append(
                        {
                            "cell": cell_id,
                            "field": flag,
                            "expected": True,
                            "observed": item.get(flag),
                        }
                    )
    for field in (
        "missing_coverage_cells",
        "missing_executed_cells",
        "missing_run_artifact_cells",
        "missing_mitigation_cells",
        "unmapped_findings",
        "failed_runs",
        "open_high_findings",
    ):
        for value in summary.get(field) or []:
            blocking_gaps.append({"field": f"summary.{field}", "value": value})

    return {
        "corpus_file": corpus_file,
        "required_row_count": row_count,
        "required_attack_types": list(V1_REDTEAM_RESEARCH_ATTACK_TYPES),
        "observed_attack_types": sorted(observed_attacks),
        "missing_attack_types": sorted(required_attacks - observed_attacks),
        "required_surfaces": list(V1_REDTEAM_RESEARCH_SURFACES),
        "observed_surfaces": sorted(observed_surfaces),
        "missing_surfaces": sorted(required_surfaces - observed_surfaces),
        "required_channels": list(V1_REDTEAM_CORPUS_EXECUTION_CHANNELS),
        "observed_channels": sorted(observed_channels),
        "missing_channels": sorted(required_channels - observed_channels),
        "required_providers": list(V1_REDTEAM_CORPUS_EXECUTION_PROVIDERS),
        "observed_providers": sorted(observed_providers),
        "missing_providers": sorted(required_providers - observed_providers),
        "required_frameworks": list(V1_REDTEAM_CORPUS_EXECUTION_FRAMEWORKS),
        "observed_frameworks": sorted(observed_frameworks),
        "missing_frameworks": sorted(required_frameworks - observed_frameworks),
        "campaign_kind": campaign.get("kind"),
        "campaign_summary": dict(summary),
        "campaign_metadata": {
            "source": metadata.get("source"),
            "cookbook": metadata.get("cookbook"),
            "row_count": metadata.get("row_count"),
            "frameworks": list(metadata.get("frameworks") or []),
        },
        "coverage_cell_ids": [
            str(item.get("id") or "") for item in coverage_matrix if item.get("id")
        ],
        "missing_files": missing_files,
        "parse_errors": parse_errors,
        "campaign_errors": campaign_errors,
        "coverage_errors": coverage_errors,
        "blocking_gaps": blocking_gaps,
    }


def _collect_release_redteam_tokens(value: Any, tokens: set[str]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _collect_release_text_tokens(str(key), tokens)
            _collect_release_redteam_tokens(item, tokens)
        return
    if isinstance(value, list | tuple | set):
        for item in value:
            _collect_release_redteam_tokens(item, tokens)
        return
    if isinstance(value, str | int | float | bool):
        _collect_release_text_tokens(str(value), tokens)


def _collect_release_text_tokens(text: str, tokens: set[str]) -> None:
    normalized = _release_norm(text)
    if normalized:
        tokens.add(normalized)
    split_text = text
    for delimiter in (
        "|",
        "/",
        ":",
        ",",
        ";",
        ".",
        "(",
        ")",
        "[",
        "]",
        "{",
        "}",
        "\"",
        "'",
        "\n",
        "\t",
    ):
        split_text = split_text.replace(delimiter, " ")
    for part in split_text.split():
        normalized_part = _release_norm(part)
        if normalized_part:
            tokens.add(normalized_part)


def _release_norm(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _release_ui_action_report_status(root: Path) -> dict[str, Any]:
    missing_files: list[str] = []
    failing_reports: list[dict[str, Any]] = []
    missing_report_sections: list[dict[str, Any]] = []
    missing_report_card_keys: list[dict[str, Any]] = []
    missing_action_ids: list[dict[str, Any]] = []
    missing_output_evidence: list[dict[str, Any]] = []
    secret_marker_findings: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    artifacts: list[dict[str, Any]] = []

    try:
        from agent_learning import actions, simulate
    except Exception as exc:
        return {
            "required_artifacts": copy.deepcopy(V1_UI_ACTION_REPORT_ARTIFACTS),
            "forbidden_secret_markers": list(V1_UI_FORBIDDEN_SECRET_MARKERS),
            "artifacts": [],
            "missing_files": [],
            "failing_reports": [],
            "missing_report_sections": [],
            "missing_report_card_keys": [],
            "missing_action_ids": [],
            "missing_output_evidence": [],
            "secret_marker_findings": [],
            "errors": [{"path": ".", "error": str(exc)}],
        }

    for spec in V1_UI_ACTION_REPORT_ARTIFACTS:
        relative_path = str(spec["path"])
        path = root / relative_path
        if not path.exists():
            missing_files.append(relative_path)
            continue
        try:
            artifact = actions.load_artifact_file(path)
            report = simulate.render_report(artifact, source_path=path)
            catalog = actions.action_catalog(artifact, source_path=path)
        except Exception as exc:
            errors.append({"path": relative_path, "error": str(exc)})
            continue

        source_kind = str(
            artifact.get("kind")
            or artifact.get("version")
            or artifact.get("schema_version")
            or ""
        )
        expected_source_kind = str(spec.get("source_kind") or "")
        if expected_source_kind and source_kind != expected_source_kind:
            errors.append(
                {
                    "path": relative_path,
                    "error": (
                        f"source kind {source_kind!r} != "
                        f"{expected_source_kind!r}"
                    ),
                }
            )

        report_summary = dict(report.get("summary") or {})
        report_body = dict(report.get("report") or {})
        report_sections = list(
            report_summary.get("sections") or report_body.get("sections") or []
        )
        report_markdown = str(report_body.get("markdown") or "")
        report_card_keys = sorted(
            key
            for key in report_body
            if key not in {"format", "markdown", "sections", "source_path"}
        )
        report_core_missing = [
            field
            for field in ("kind", "schema_version", "status", "summary", "report")
            if field not in report or report.get(field) in (None, "", {}, [])
        ]
        if (
            report.get("kind") != "agent-learning.report.v1"
            or report.get("status") != "passed"
            or not report_markdown.strip()
            or report_core_missing
        ):
            failing_reports.append(
                {
                    "path": relative_path,
                    "kind": report.get("kind"),
                    "status": report.get("status"),
                    "missing_core_fields": report_core_missing,
                    "markdown_present": bool(report_markdown.strip()),
                }
            )

        required_sections = [str(item) for item in spec["required_report_sections"]]
        missing_sections = sorted(set(required_sections) - set(report_sections))
        if missing_sections:
            missing_report_sections.append(
                {
                    "path": relative_path,
                    "required": required_sections,
                    "observed": report_sections,
                    "missing": missing_sections,
                }
            )
        required_card_keys = [
            str(item) for item in spec.get("required_report_card_keys") or []
        ]
        missing_card_keys = sorted(set(required_card_keys) - set(report_card_keys))
        if missing_card_keys:
            missing_report_card_keys.append(
                {
                    "path": relative_path,
                    "required": required_card_keys,
                    "observed": report_card_keys,
                    "missing": missing_card_keys,
                }
            )

        action_ids = [
            str(action.get("id"))
            for action in catalog.get("actions") or []
            if isinstance(action, Mapping) and action.get("id")
        ]
        report_action_ids = [
            str(action.get("id"))
            for action in actions.extract_actions(report)
            if action.get("id")
        ]
        catalog_core_missing = [
            field
            for field in ("kind", "schema_version", "status", "summary", "actions")
            if field not in catalog or catalog.get(field) in (None, "", {})
        ]
        if (
            catalog.get("kind") != "agent-learning.actions.v1"
            or catalog.get("status") != "passed"
            or catalog_core_missing
        ):
            missing_action_ids.append(
                {
                    "path": relative_path,
                    "required": list(spec["required_action_ids"]),
                    "observed": action_ids,
                    "missing": [],
                    "catalog_status": catalog.get("status"),
                    "catalog_missing_core_fields": catalog_core_missing,
                }
            )
        required_action_ids = [str(item) for item in spec["required_action_ids"]]
        missing_actions = sorted(set(required_action_ids) - set(action_ids))
        if missing_actions:
            missing_action_ids.append(
                {
                    "path": relative_path,
                    "required": required_action_ids,
                    "observed": action_ids,
                    "missing": missing_actions,
                }
            )

        outputs_written = list(artifact.get("outputs_written") or [])
        output_completion_rate = (
            dict(artifact.get("summary") or {}).get("output_completion_rate")
        )
        if spec.get("requires_outputs_written") and (
            not outputs_written or output_completion_rate != 1.0
        ):
            missing_output_evidence.append(
                {
                    "path": relative_path,
                    "outputs_written_count": len(outputs_written),
                    "output_completion_rate": output_completion_rate,
                }
            )

        secret_marker_findings.extend(
            _release_secret_marker_findings(
                relative_path,
                {
                    "source": artifact,
                    "report": report,
                    "actions": catalog,
                },
            )
        )
        artifacts.append(
            {
                "path": relative_path,
                "source_kind": source_kind,
                "report_kind": report.get("kind"),
                "report_status": report.get("status"),
                "report_sections": report_sections,
                "report_card_keys": report_card_keys,
                "report_action_ids": report_action_ids,
                "action_catalog_kind": catalog.get("kind"),
                "action_catalog_status": catalog.get("status"),
                "action_ids": action_ids,
                "source_card_paths": list(
                    dict(catalog.get("summary") or {}).get("source_card_paths")
                    or []
                ),
                "outputs_written_count": len(outputs_written),
                "output_completion_rate": output_completion_rate,
            }
        )

    return {
        "required_artifacts": copy.deepcopy(V1_UI_ACTION_REPORT_ARTIFACTS),
        "forbidden_secret_markers": list(V1_UI_FORBIDDEN_SECRET_MARKERS),
        "artifacts": artifacts,
        "missing_files": missing_files,
        "failing_reports": failing_reports,
        "missing_report_sections": missing_report_sections,
        "missing_report_card_keys": missing_report_card_keys,
        "missing_action_ids": missing_action_ids,
        "missing_output_evidence": missing_output_evidence,
        "secret_marker_findings": secret_marker_findings,
        "errors": errors,
    }


def _release_harness_diagnosis_status(root: Path) -> dict[str, Any]:
    source = V1_HARNESS_DIAGNOSIS_SOURCE
    source_path = root / source
    missing_files = [] if source_path.exists() else [source]
    optimization_errors: list[dict[str, Any]] = []
    report_errors: list[dict[str, Any]] = []
    diagnosis_errors: list[dict[str, Any]] = []
    action_errors: list[dict[str, Any]] = []
    rollout_errors: list[dict[str, Any]] = []
    proof_errors: list[dict[str, Any]] = []
    secret_marker_findings: list[dict[str, str]] = []
    evidence: dict[str, Any] = {
        "source": source,
        "result_status": None,
        "report_status": None,
        "report_sections": [],
        "diagnosis_kind": None,
        "diagnosis_status": None,
        "primary_layers": [],
        "observed_layers": [],
        "target_layers": [],
        "repair_operator_layers": [],
        "diagnosis_action_ids": [],
        "report_action_ids": [],
        "research_sources": [],
        "rollout_kind": None,
        "rollout_status": None,
        "rollout_candidate_count": 0,
        "rollout_step_ids": [],
        "proof_kind": None,
        "proof_status": None,
        "proof_failed_check_ids": [],
        "proof_warning_check_ids": [],
    }
    if missing_files:
        return {
            "source": source,
            "required_actions": list(V1_HARNESS_DIAGNOSIS_REQUIRED_ACTIONS),
            "required_layers": list(V1_HARNESS_DIAGNOSIS_REQUIRED_LAYERS),
            "required_research_sources": list(
                V1_HARNESS_DIAGNOSIS_REQUIRED_RESEARCH_SOURCES
            ),
            "evidence": evidence,
            "missing_files": missing_files,
            "optimization_errors": optimization_errors,
            "report_errors": report_errors,
            "diagnosis_errors": diagnosis_errors,
            "action_errors": action_errors,
            "rollout_errors": rollout_errors,
            "proof_errors": proof_errors,
            "secret_marker_findings": secret_marker_findings,
        }

    result: Mapping[str, Any] = {}
    report: Mapping[str, Any] = {}
    diagnosis: Mapping[str, Any] = {}
    rollout: Mapping[str, Any] = {}
    proof: Mapping[str, Any] = {}
    try:
        from agent_learning import actions, optimize, simulate

        result = optimize.optimize_retrospective_harness(
            name="release-harness-diagnosis-readiness",
            required_env=[],
            target_metadata={"release_check": "harness_diagnosis_readiness"},
            manifest_path=source_path,
        )
        report = simulate.render_report(result, source_path=source_path)
        report_actions = [
            str(action.get("id"))
            for action in actions.extract_actions(report)
            if action.get("id")
        ]
    except Exception as exc:
        optimization_errors.append({"path": source, "error": str(exc)})
        report_actions = []
    else:
        result_summary = dict(result.get("summary") or {})
        report_summary = dict(report.get("summary") or {})
        report_body = (
            report.get("report")
            if isinstance(report.get("report"), Mapping)
            else {}
        )
        diagnosis = (
            report_body.get("harness_diagnosis")
            if isinstance(report_body.get("harness_diagnosis"), Mapping)
            else {}
        )
        rollout = (
            diagnosis.get("retrospective_rollout_plan")
            if isinstance(diagnosis.get("retrospective_rollout_plan"), Mapping)
            else {}
        )
        proof = (
            result.get("retrospective_harness_proof")
            if isinstance(result.get("retrospective_harness_proof"), Mapping)
            else {}
        )
        report_sections = list(
            report_summary.get("sections") or report_body.get("sections") or []
        )
        diagnosis_actions = [
            str(action.get("id"))
            for action in diagnosis.get("actions") or []
            if isinstance(action, Mapping) and action.get("id")
        ]
        layer_records = [
            item
            for item in diagnosis.get("layers") or []
            if isinstance(item, Mapping)
        ]
        target_layers = sorted(
            {
                str(layer)
                for action in diagnosis.get("actions") or []
                if isinstance(action, Mapping)
                for layer in action.get("target_layers") or []
                if layer
            }
        )
        repair_operator_layers = sorted(
            {
                str(operator.get("layer"))
                for operator in diagnosis.get("repair_operators") or []
                if isinstance(operator, Mapping) and operator.get("layer")
            }
        )
        evidence.update(
            {
                "result_status": result.get("status"),
                "optimization_score": result_summary.get("optimization_score"),
                "report_status": report.get("status"),
                "report_sections": report_sections,
                "diagnosis_kind": diagnosis.get("kind"),
                "diagnosis_status": diagnosis.get("status"),
                "primary_layers": list(diagnosis.get("primary_layers") or []),
                "observed_layers": sorted(
                    str(item.get("layer"))
                    for item in layer_records
                    if item.get("layer")
                ),
                "target_layers": target_layers,
                "repair_operator_layers": repair_operator_layers,
                "diagnosis_action_ids": diagnosis_actions,
                "report_action_ids": report_actions,
                "research_sources": list(diagnosis.get("research_sources") or []),
                "rollout_kind": rollout.get("kind"),
                "rollout_status": rollout.get("status"),
                "rollout_candidate_count": rollout.get("candidate_count") or 0,
                "rollout_step_ids": [
                    str(step.get("id"))
                    for step in rollout.get("rollout_steps") or []
                    if isinstance(step, Mapping) and step.get("id")
                ],
                "proof_kind": proof.get("kind"),
                "proof_status": proof.get("status"),
                "proof_failed_check_ids": list(proof.get("failed_check_ids") or []),
                "proof_warning_check_ids": list(
                    proof.get("warning_check_ids") or []
                ),
            }
        )
        if result.get("status") != "passed":
            optimization_errors.append(
                {
                    "path": source,
                    "field": "result.status",
                    "expected": "passed",
                    "observed": result.get("status"),
                }
            )
        if report.get("kind") != "agent-learning.report.v1" or report.get("status") != "passed":
            report_errors.append(
                {
                    "path": source,
                    "field": "report",
                    "expected": "agent-learning.report.v1/passed",
                    "observed": {
                        "kind": report.get("kind"),
                        "status": report.get("status"),
                    },
                }
            )
        if "harness_diagnosis" not in report_sections:
            report_errors.append(
                {
                    "path": source,
                    "field": "report.sections",
                    "expected": "harness_diagnosis",
                    "observed": report_sections,
                }
            )
        if diagnosis.get("kind") != "harness_layer_diagnosis" or diagnosis.get("status") != "passed":
            diagnosis_errors.append(
                {
                    "path": source,
                    "field": "report.harness_diagnosis",
                    "expected": "harness_layer_diagnosis/passed",
                    "observed": {
                        "kind": diagnosis.get("kind"),
                        "status": diagnosis.get("status"),
                    },
                }
            )
        missing_layers = sorted(
            set(V1_HARNESS_DIAGNOSIS_REQUIRED_LAYERS)
            - set(evidence["observed_layers"])
        )
        if missing_layers:
            diagnosis_errors.append(
                {
                    "path": source,
                    "field": "report.harness_diagnosis.layers",
                    "required": list(V1_HARNESS_DIAGNOSIS_REQUIRED_LAYERS),
                    "observed": evidence["observed_layers"],
                    "missing": missing_layers,
                }
            )
        missing_research = sorted(
            set(V1_HARNESS_DIAGNOSIS_REQUIRED_RESEARCH_SOURCES)
            - set(evidence["research_sources"])
        )
        if missing_research:
            diagnosis_errors.append(
                {
                    "path": source,
                    "field": "report.harness_diagnosis.research_sources",
                    "required": list(V1_HARNESS_DIAGNOSIS_REQUIRED_RESEARCH_SOURCES),
                    "observed": evidence["research_sources"],
                    "missing": missing_research,
                }
            )
        missing_actions = sorted(
            set(V1_HARNESS_DIAGNOSIS_REQUIRED_ACTIONS) - set(diagnosis_actions)
        )
        missing_report_actions = sorted(
            set(V1_HARNESS_DIAGNOSIS_REQUIRED_ACTIONS) - set(report_actions)
        )
        if missing_actions or missing_report_actions:
            action_errors.append(
                {
                    "path": source,
                    "required": list(V1_HARNESS_DIAGNOSIS_REQUIRED_ACTIONS),
                    "diagnosis_action_ids": diagnosis_actions,
                    "report_action_ids": report_actions,
                    "missing_diagnosis_actions": missing_actions,
                    "missing_report_actions": missing_report_actions,
                }
            )
        if (
            rollout.get("kind") != "retrospective_harness_rollout_plan"
            or rollout.get("status") != "ready"
            or int(rollout.get("candidate_count") or 0) < 2
        ):
            rollout_errors.append(
                {
                    "path": source,
                    "field": "retrospective_rollout_plan",
                    "expected": "ready plan with at least two candidates",
                    "observed": {
                        "kind": rollout.get("kind"),
                        "status": rollout.get("status"),
                        "candidate_count": rollout.get("candidate_count"),
                    },
                }
            )
        missing_rollout_steps = sorted(
            {"replay_selected_candidate", "repair_weak_layers", "promote_or_hold"}
            - set(evidence["rollout_step_ids"])
        )
        if missing_rollout_steps:
            rollout_errors.append(
                {
                    "path": source,
                    "field": "retrospective_rollout_plan.rollout_steps",
                    "observed": evidence["rollout_step_ids"],
                    "missing": missing_rollout_steps,
                }
            )
        if (
            proof.get("kind")
            != "agent-learning.optimization.retrospective-harness-proof.v1"
            or proof.get("status") != "passed"
            or proof.get("failed_check_ids")
            or proof.get("warning_check_ids")
        ):
            proof_errors.append(
                {
                    "path": source,
                    "field": "retrospective_harness_proof",
                    "expected": "passed proof with no failed/warning checks",
                    "observed": {
                        "kind": proof.get("kind"),
                        "status": proof.get("status"),
                        "failed_check_ids": proof.get("failed_check_ids"),
                        "warning_check_ids": proof.get("warning_check_ids"),
                    },
                }
            )
        secret_marker_findings.extend(
            _release_secret_marker_findings(
                source,
                {"result": result, "report": report},
            )
        )

    return {
        "source": source,
        "required_actions": list(V1_HARNESS_DIAGNOSIS_REQUIRED_ACTIONS),
        "required_layers": list(V1_HARNESS_DIAGNOSIS_REQUIRED_LAYERS),
        "required_research_sources": list(
            V1_HARNESS_DIAGNOSIS_REQUIRED_RESEARCH_SOURCES
        ),
        "evidence": evidence,
        "missing_files": missing_files,
        "optimization_errors": optimization_errors,
        "report_errors": report_errors,
        "diagnosis_errors": diagnosis_errors,
        "action_errors": action_errors,
        "rollout_errors": rollout_errors,
        "proof_errors": proof_errors,
        "secret_marker_findings": secret_marker_findings,
    }


def _release_framework_provider_contract_status(root: Path) -> dict[str, Any]:
    required_frameworks = list(V1_FRAMEWORK_PROVIDER_FRAMEWORKS)
    required_framework_set = set(required_frameworks)
    required_modalities = set(V1_FRAMEWORK_PROVIDER_REQUIRED_MODALITIES)
    required_transports = set(V1_FRAMEWORK_PROVIDER_REQUIRED_TRANSPORTS)
    required_target_schemes = set(V1_FRAMEWORK_PROVIDER_REQUIRED_TARGET_SCHEMES)
    required_capabilities = {"messages", "tool_calls", "runtime_trace"}
    required_evidence = {
        "framework_runtime",
        "framework_trace",
        "tool_calls",
        "adapter_conformance",
        "metric_evidence",
    }
    missing_files = _missing_relative_paths(
        root,
        [spec["path"] for spec in V1_FRAMEWORK_PROVIDER_MANIFEST_CONTRACTS],
    )
    matrix_errors: list[dict[str, Any]] = []
    contract_errors: list[dict[str, Any]] = []
    manifest_errors: list[dict[str, Any]] = []
    external_value_findings: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    manifest_contracts: list[dict[str, Any]] = []
    matrix: Mapping[str, Any] = {}
    matrix_summary: Mapping[str, Any] = {}

    try:
        from agent_learning import simulate

        raw_matrix = simulate.framework_adapter_contract_matrix(required_frameworks)
        if isinstance(raw_matrix, Mapping):
            matrix = raw_matrix
            matrix_summary = dict(raw_matrix.get("summary") or {})
        else:
            errors.append(
                {
                    "path": ".",
                    "error": (
                        "framework_adapter_contract_matrix returned "
                        f"{type(raw_matrix).__name__}, expected mapping"
                    ),
                }
            )
    except Exception as exc:
        errors.append({"path": ".", "error": str(exc)})

    if matrix:
        observed_frameworks = list(matrix.get("frameworks") or [])
        observed_modalities = set(matrix_summary.get("modalities") or [])
        observed_transports = set(matrix_summary.get("transports") or [])
        observed_target_schemes = set(matrix_summary.get("target_schemes") or [])
        if matrix.get("kind") != "agent-learning.framework-adapter-contract-matrix.v1":
            matrix_errors.append(
                {
                    "field": "kind",
                    "expected": "agent-learning.framework-adapter-contract-matrix.v1",
                    "observed": matrix.get("kind"),
                }
            )
        if matrix.get("status") != "passed":
            matrix_errors.append(
                {"field": "status", "expected": "passed", "observed": matrix.get("status")}
            )
        if matrix.get("requires_external_service") is not False:
            matrix_errors.append(
                {
                    "field": "requires_external_service",
                    "expected": False,
                    "observed": matrix.get("requires_external_service"),
                }
            )
        if matrix.get("allow_external_targets") is not False:
            matrix_errors.append(
                {
                    "field": "allow_external_targets",
                    "expected": False,
                    "observed": matrix.get("allow_external_targets"),
                }
            )
        if observed_frameworks != required_frameworks:
            matrix_errors.append(
                {
                    "field": "frameworks",
                    "expected": required_frameworks,
                    "observed": observed_frameworks,
                }
            )
        expected_count = len(required_frameworks)
        expected_summary_counts = {
            "contract_count": expected_count,
            "local_executable_fixture_count": expected_count,
            "requires_external_service_count": 0,
            "external_target_count": 0,
            "trace_runtime_count": expected_count,
        }
        for field, expected in expected_summary_counts.items():
            observed = matrix_summary.get(field)
            if observed != expected:
                matrix_errors.append(
                    {"field": f"summary.{field}", "expected": expected, "observed": observed}
                )
        if observed_modalities != required_modalities:
            matrix_errors.append(
                {
                    "field": "summary.modalities",
                    "expected": sorted(required_modalities),
                    "observed": sorted(observed_modalities),
                }
            )
        if observed_transports != required_transports:
            matrix_errors.append(
                {
                    "field": "summary.transports",
                    "expected": sorted(required_transports),
                    "observed": sorted(observed_transports),
                }
            )
        if observed_target_schemes != required_target_schemes:
            matrix_errors.append(
                {
                    "field": "summary.target_schemes",
                    "expected": sorted(required_target_schemes),
                    "observed": sorted(observed_target_schemes),
                }
            )

        contracts = list(matrix.get("contracts") or [])
        observed_contract_frameworks: set[str] = set()
        for contract in contracts:
            if not isinstance(contract, Mapping):
                contract_errors.append(
                    {
                        "framework": "<unknown>",
                        "field": "contract",
                        "error": f"contract is {type(contract).__name__}, expected mapping",
                    }
                )
                continue
            framework = str(contract.get("framework") or "")
            observed_contract_frameworks.add(framework)
            capabilities = set(contract.get("capabilities") or [])
            evidence = set(contract.get("evidence_requirements") or [])
            lifecycle_hooks = set(contract.get("lifecycle_hooks") or [])
            schemas = contract.get("schemas")
            target = str(contract.get("target") or "")
            target_scheme = str(contract.get("target_scheme") or "")
            contract_expectations = {
                "kind": (
                    contract.get("kind"),
                    "agent-learning.framework-adapter-contract.v1",
                ),
                "requires_external_service": (
                    contract.get("requires_external_service"),
                    False,
                ),
                "local_executable_fixture": (
                    contract.get("local_executable_fixture"),
                    True,
                ),
                "trace_runtime": (contract.get("trace_runtime"), True),
            }
            for field, (observed, expected) in contract_expectations.items():
                if observed != expected:
                    contract_errors.append(
                        {
                            "framework": framework,
                            "field": field,
                            "expected": expected,
                            "observed": observed,
                        }
                    )
            if framework not in required_framework_set:
                contract_errors.append(
                    {
                        "framework": framework,
                        "field": "framework",
                        "expected": sorted(required_framework_set),
                        "observed": framework,
                    }
                )
            if contract.get("modality") not in required_modalities:
                contract_errors.append(
                    {
                        "framework": framework,
                        "field": "modality",
                        "expected": sorted(required_modalities),
                        "observed": contract.get("modality"),
                    }
                )
            if contract.get("transport") not in required_transports:
                contract_errors.append(
                    {
                        "framework": framework,
                        "field": "transport",
                        "expected": sorted(required_transports),
                        "observed": contract.get("transport"),
                    }
                )
            if not target:
                contract_errors.append(
                    {
                        "framework": framework,
                        "field": "target",
                        "expected": "non-empty local fixture target",
                        "observed": target,
                    }
                )
            if target_scheme not in required_target_schemes:
                contract_errors.append(
                    {
                        "framework": framework,
                        "field": "target_scheme",
                        "expected": sorted(required_target_schemes),
                        "observed": target_scheme,
                    }
                )
            missing_capabilities = sorted(required_capabilities - capabilities)
            if missing_capabilities:
                contract_errors.append(
                    {
                        "framework": framework,
                        "field": "capabilities",
                        "missing": missing_capabilities,
                    }
                )
            missing_evidence = sorted(required_evidence - evidence)
            if missing_evidence:
                contract_errors.append(
                    {
                        "framework": framework,
                        "field": "evidence_requirements",
                        "missing": missing_evidence,
                    }
                )
            if not {"setup", "teardown"} <= lifecycle_hooks:
                contract_errors.append(
                    {
                        "framework": framework,
                        "field": "lifecycle_hooks",
                        "required": ["setup", "teardown"],
                        "observed": sorted(lifecycle_hooks),
                    }
                )
            if not isinstance(schemas, Mapping) or not {"input", "output"} <= set(schemas):
                contract_errors.append(
                    {
                        "framework": framework,
                        "field": "schemas",
                        "required": ["input", "output"],
                        "observed": sorted(schemas) if isinstance(schemas, Mapping) else [],
                    }
                )
        missing_contract_frameworks = sorted(
            required_framework_set - observed_contract_frameworks
        )
        if missing_contract_frameworks:
            contract_errors.append(
                {
                    "framework": "<matrix>",
                    "field": "contracts",
                    "missing_frameworks": missing_contract_frameworks,
                }
            )

    for spec in V1_FRAMEWORK_PROVIDER_MANIFEST_CONTRACTS:
        relative_path = str(spec["path"])
        path = root / relative_path
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append({"path": relative_path, "error": str(exc)})
            continue
        if not isinstance(payload, Mapping):
            errors.append(
                {
                    "path": relative_path,
                    "error": f"manifest is {type(payload).__name__}, expected mapping",
                }
            )
            continue

        external_value_findings.extend(
            _release_external_value_findings(relative_path, payload)
        )
        agent = payload.get("agent") if isinstance(payload.get("agent"), Mapping) else {}
        simulation = (
            payload.get("simulation")
            if isinstance(payload.get("simulation"), Mapping)
            else {}
        )
        environments = [
            env
            for env in simulation.get("environments") or []
            if isinstance(env, Mapping)
        ]
        environment_types = [
            str(env.get("type") or "") for env in environments if env.get("type")
        ]
        framework_values = set()
        if agent.get("framework"):
            framework_values.add(str(agent.get("framework")))
        environment_frameworks: dict[str, str] = {}
        for env in environments:
            env_type = str(env.get("type") or "")
            data = env.get("data") if isinstance(env.get("data"), Mapping) else {}
            framework = str(data.get("framework") or "")
            if framework:
                framework_values.add(framework)
                environment_frameworks[env_type] = framework

        required_environment_types = [
            str(item) for item in spec["required_environment_types"]
        ]
        missing_environment_types = sorted(
            set(required_environment_types) - set(environment_types)
        )
        observed_kind = (
            payload.get("version")
            or payload.get("kind")
            or payload.get("schema_version")
        )
        observed_agent_type = agent.get("type")
        observed_modality = str(simulation.get("modality") or "text")
        expected_framework = str(spec["framework"])
        agent_target = str(agent.get("target") or "")
        manifest_contracts.append(
            {
                "path": relative_path,
                "kind": observed_kind,
                "agent_type": observed_agent_type,
                "frameworks": sorted(framework_values),
                "modality": observed_modality,
                "environment_types": environment_types,
                "required_environment_types": required_environment_types,
                "missing_environment_types": missing_environment_types,
                "required_env": list(payload.get("required_env") or []),
                "agent_target": agent_target,
            }
        )
        manifest_expectations = {
            "kind": (observed_kind, spec["kind"]),
            "agent.type": (observed_agent_type, spec["agent_type"]),
            "simulation.modality": (observed_modality, spec["modality"]),
        }
        for field, (observed, expected) in manifest_expectations.items():
            if observed != expected:
                manifest_errors.append(
                    {
                        "path": relative_path,
                        "field": field,
                        "expected": expected,
                        "observed": observed,
                    }
                )
        if expected_framework not in framework_values:
            manifest_errors.append(
                {
                    "path": relative_path,
                    "field": "framework",
                    "expected": expected_framework,
                    "observed": sorted(framework_values),
                }
            )
        if missing_environment_types:
            manifest_errors.append(
                {
                    "path": relative_path,
                    "field": "simulation.environments",
                    "required": required_environment_types,
                    "observed": environment_types,
                    "missing": missing_environment_types,
                }
            )
        if not payload.get("required_env"):
            manifest_errors.append(
                {
                    "path": relative_path,
                    "field": "required_env",
                    "expected": "at least one env-key name for real-key execution",
                    "observed": [],
                }
            )
        for env_type in required_environment_types:
            framework = environment_frameworks.get(env_type)
            if framework != expected_framework:
                manifest_errors.append(
                    {
                        "path": relative_path,
                        "field": f"environment.{env_type}.framework",
                        "expected": expected_framework,
                        "observed": framework,
                    }
                )
        if spec["agent_type"] == "framework" and not agent_target:
            manifest_errors.append(
                {
                    "path": relative_path,
                    "field": "agent.target",
                    "expected": "local framework shim target",
                    "observed": agent_target,
                }
            )

    return {
        "required_frameworks": required_frameworks,
        "required_modalities": list(V1_FRAMEWORK_PROVIDER_REQUIRED_MODALITIES),
        "required_transports": list(V1_FRAMEWORK_PROVIDER_REQUIRED_TRANSPORTS),
        "required_target_schemes": list(V1_FRAMEWORK_PROVIDER_REQUIRED_TARGET_SCHEMES),
        "required_manifest_contracts": copy.deepcopy(
            V1_FRAMEWORK_PROVIDER_MANIFEST_CONTRACTS
        ),
        "matrix_kind": matrix.get("kind"),
        "matrix_status": matrix.get("status"),
        "matrix_summary": dict(matrix_summary),
        "matrix_quality_gate": dict(matrix.get("contract_quality_gate") or {}),
        "observed_frameworks": list(matrix.get("frameworks") or []),
        "observed_modalities": list(matrix_summary.get("modalities") or []),
        "observed_transports": list(matrix_summary.get("transports") or []),
        "observed_target_schemes": list(matrix_summary.get("target_schemes") or []),
        "manifest_contracts": manifest_contracts,
        "missing_files": missing_files,
        "matrix_errors": matrix_errors,
        "contract_errors": contract_errors,
        "manifest_errors": manifest_errors,
        "external_value_findings": external_value_findings,
        "errors": errors,
    }


def _release_external_value_findings(
    relative_path: str,
    value: Any,
    *,
    breadcrumb: str = "$",
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    external_prefixes = ("http://", "https://", "ws://", "wss://")
    if isinstance(value, str):
        if value.strip().lower().startswith(external_prefixes):
            findings.append(
                {
                    "path": relative_path,
                    "field": breadcrumb,
                    "value": value,
                }
            )
        return findings
    if isinstance(value, Mapping):
        for key, item in value.items():
            findings.extend(
                _release_external_value_findings(
                    relative_path,
                    item,
                    breadcrumb=f"{breadcrumb}.{key}",
                )
            )
        return findings
    if isinstance(value, list | tuple | set):
        for index, item in enumerate(value):
            findings.extend(
                _release_external_value_findings(
                    relative_path,
                    item,
                    breadcrumb=f"{breadcrumb}[{index}]",
                )
            )
    return findings


def _release_secret_marker_findings(
    relative_path: str,
    payloads: Mapping[str, Any],
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for surface, payload in payloads.items():
        try:
            text = json.dumps(payload, sort_keys=True, default=str)
        except Exception:
            text = str(payload)
        lowered = text.lower()
        for marker in V1_UI_FORBIDDEN_SECRET_MARKERS:
            if marker.lower() in lowered:
                findings.append(
                    {
                        "path": relative_path,
                        "surface": str(surface),
                        "marker": marker,
                    }
                )
    return findings


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
    "LEGACY_TYPESCRIPT_PACKAGES",
    "PUBLIC_MODULES",
    "PUBLIC_CONSOLE_SCRIPTS",
    "REJECTED_LEGACY_CONSOLE_SCRIPTS",
    "RESEARCH_SOURCES",
    "TYPESCRIPT_PUBLIC_PACKAGE",
    "V1_REQUIRED_CLI_COMMANDS",
    "V1_REQUIRED_DOCS",
    "V1_REQUIRED_EVIDENCE_COMPONENTS",
    "V1_REQUIRED_EXAMPLES",
    "V1_REQUIRED_SCHEMA_KINDS",
    "V1_RELEASE_PROOF_REQUIRED_CHECKS",
    "V1_TYPESCRIPT_SDK_REQUIRED_FILES",
    "V1_HARNESS_DIAGNOSIS_REQUIRED_ACTIONS",
    "V1_HARNESS_DIAGNOSIS_REQUIRED_LAYERS",
    "V1_HARNESS_DIAGNOSIS_REQUIRED_RESEARCH_SOURCES",
    "V1_HARNESS_DIAGNOSIS_SOURCE",
    "V1_FRAMEWORK_PROVIDER_EXAMPLES",
    "V1_FRAMEWORK_PROVIDER_FRAMEWORKS",
    "V1_FRAMEWORK_PROVIDER_MANIFEST_CONTRACTS",
    "V1_FRAMEWORK_PROVIDER_REQUIRED_MODALITIES",
    "V1_FRAMEWORK_PROVIDER_REQUIRED_TARGET_SCHEMES",
    "V1_FRAMEWORK_PROVIDER_REQUIRED_TRANSPORTS",
    "V1_LOCAL_SIM_EVAL_EXAMPLES",
    "V1_REDTEAM_EXAMPLES",
    "V1_REDTEAM_CORPUS_EXECUTION_CHANNELS",
    "V1_REDTEAM_CORPUS_EXECUTION_FILE",
    "V1_REDTEAM_CORPUS_EXECUTION_FRAMEWORKS",
    "V1_REDTEAM_CORPUS_EXECUTION_PROVIDERS",
    "V1_REDTEAM_RESEARCH_ATTACK_TYPES",
    "V1_REDTEAM_RESEARCH_CORPUS_FILE",
    "V1_REDTEAM_RESEARCH_FILES",
    "V1_REDTEAM_RESEARCH_SOURCE_URLS",
    "V1_REDTEAM_RESEARCH_SURFACES",
    "V1_UI_ACTION_REPORT_ARTIFACTS",
    "V1_UI_FORBIDDEN_SECRET_MARKERS",
    "assert_release_ready",
    "assert_trinity_ready",
    "consolidation_metadata",
    "module_status",
    "release_proof_status",
    "release_status",
    "trinity_status",
]
