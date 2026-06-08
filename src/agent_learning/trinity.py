from __future__ import annotations

import copy
import importlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

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
    "examples/custom_framework_optimization.json",
    "examples/social_memory_framework_optimization.json",
    "examples/multi_agent_framework_handoff_optimization.json",
    "examples/sdk_world_hooks_optimization.py",
    "examples/sdk_optimizer_portfolio_optimization.py",
    "examples/sdk_framework_certification_optimization.py",
    "examples/sdk_redteam_society_optimization.py",
    "examples/sdk_redteam_causal_attribution_optimization.py",
    "examples/sdk_trinity_stack_probe_optimization.py",
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
    "examples/framework_openenv_manifest.json",
    "examples/voice_streaming_realtime_manifest.json",
    "examples/voice_streaming_realtime_optimization.json",
    "examples/agent_integration_optimization.json",
    "examples/world_framework_memory_optimization.json",
    "examples/custom_framework_optimization.json",
    "examples/social_memory_framework_optimization.json",
    "examples/multi_agent_framework_handoff_optimization.json",
    "examples/sdk_framework_adapter_mcp_tool_session.py",
    "examples/sdk_framework_adapter_a2a_protocol_trace.py",
    "examples/sdk_framework_adapter_realtime_trace.py",
    "examples/sdk_framework_adapter_browser_cua_trace.py",
    "examples/sdk_framework_adapter_memory_trace.py",
    "examples/sdk_framework_adapter_workflow_trace.py",
    "examples/sdk_framework_adapter_orchestration_trace.py",
    "examples/sdk_framework_adapter_lifecycle_trace.py",
    "examples/sdk_multi_framework_simulation.py",
    "examples/sdk_framework_certification_optimization.py",
    "examples/sdk_framework_certification_simulation.py",
    "examples/sdk_framework_adapter_openenv_trace.py",
    "examples/sdk_openenv_environment_optimization.py",
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
    "browser_use",
    "openenv",
    "gymnasium",
    "mcp",
    "a2a",
]

V1_FRAMEWORK_PROVIDER_REQUIRED_MODALITIES = ["text", "voice", "cua"]

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
        "path": "examples/framework_openenv_manifest.json",
        "kind": "agent-learning.run.v1",
        "framework": "openenv",
        "modality": "text",
        "agent_type": "framework",
        "required_environment_types": [],
        "required_evaluation_config_keys": [
            "framework_runtime_contract",
            "required_openenv",
            "openenv_quality",
        ],
        "required_metric_weights": ["openenv_coverage", "openenv_quality"],
        "required_framework_runtime_signals": ["openenv"],
        "required_state_keys": ["openenv"],
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

V1_TRINITY_STACK_PROBE_FILES = [
    "examples/sdk_trinity_stack_probe_optimization.py",
    "examples/sdk_orchestration_stack_probe_optimization.py",
    "examples/sdk_evaluation_hook_probe_optimization.py",
    "internal-docs/trinity-stack-probe-research.md",
    "internal-docs/orchestration-stack-probe-research.md",
    "internal-docs/evaluation-hook-probe-research.md",
]

V1_TRINITY_STACK_PROBE_REQUIRED_ENVIRONMENT_TYPES = [
    "world_contract",
    "framework_trace",
    "retrieval_memory",
    "agent_memory_lineage",
    "multi_agent_room",
]

V1_TRINITY_STACK_PROBE_PROOF_KIND = (
    "agent-learning.optimization.trinity-stack-probe-proof.v1"
)

V1_OPENENV_OPTIMIZER_FILES = [
    "examples/sdk_openenv_environment_optimization.py",
    "internal-docs/openenv-environment-adapter-research.md",
]

V1_OPENENV_OPTIMIZER_REQUIRED_PROFILES = [
    "weak_openenv_reset_step_only",
    "partial_openenv_no_failure_injection",
    "verified_openenv_replay",
]

V1_OPENENV_OPTIMIZER_REQUIRED_METRICS = [
    "openenv_coverage",
    "openenv_quality",
]

V1_FRAMEWORK_OPTIMIZER_FILES = [
    "examples/custom_framework_optimization.json",
    "examples/social_memory_framework_optimization.json",
    "examples/world_framework_memory_optimization.json",
    "examples/multi_agent_framework_handoff_optimization.json",
    "examples/framework_certification_optimization.json",
    "examples/framework_import_repair_optimization.json",
    "internal-docs/framework-optimizer-readiness-research.md",
]

V1_FRAMEWORK_OPTIMIZER_CONTRACTS = [
    {
        "surface": "custom_framework_adapter",
        "path": "examples/custom_framework_optimization.json",
        "required_env": ["AGENT_LEARNING_CUSTOM_FRAMEWORK_OPT_EXAMPLE_KEY"],
        "required_layers": ["framework", "harness", "evaluator"],
        "required_search_paths": ["agent"],
        "required_best_patch_keys": ["agent"],
        "expected_best_agent": {
            "type": "framework",
            "framework": "custom_refund_orchestrator",
            "method": "execute_task",
            "input_mode": "dict",
        },
        "required_optimizer": "AgentOptimizer",
        "min_optimization_score": 0.95,
        "min_evaluation_score": 1.0,
        "min_history_count": 2,
        "min_candidate_lineage_count": 2,
        "required_metrics": {
            "framework_runtime_contract": 1.0,
            "framework_runtime_coverage": 1.0,
            "framework_trace_coverage": 1.0,
            "tool_selection_accuracy": 1.0,
        },
        "required_proofs": ["framework_runtime_proof"],
    },
    {
        "surface": "social_memory_framework",
        "path": "examples/social_memory_framework_optimization.json",
        "required_env": ["AGENT_LEARNING_SOCIAL_MEMORY_OPT_EXAMPLE_KEY"],
        "required_layers": ["framework", "orchestration", "memory", "evaluator"],
        "required_search_paths": ["agent", "simulation.environments"],
        "required_best_patch_keys": ["agent", "simulation.environments"],
        "expected_best_agent": {
            "type": "framework",
            "framework": "custom_refund_orchestrator",
            "method": "execute_task",
            "input_mode": "dict",
        },
        "required_best_environment_types": ["framework_trace"],
        "required_optimizer": "AgentSocialMemoryOptimizer",
        "min_optimization_score": 0.95,
        "min_evaluation_score": 1.0,
        "min_history_count": 4,
        "min_candidate_lineage_count": 4,
        "required_metrics": {
            "framework_runtime_contract": 1.0,
            "framework_runtime_coverage": 1.0,
            "framework_trace_coverage": 1.0,
            "tool_selection_accuracy": 1.0,
        },
        "required_proofs": ["framework_runtime_proof"],
    },
    {
        "surface": "world_framework_memory",
        "path": "examples/world_framework_memory_optimization.json",
        "required_env": ["AGENT_LEARNING_WORLD_FRAMEWORK_OPT_EXAMPLE_KEY"],
        "required_layers": [
            "harness",
            "framework",
            "memory",
            "multi_agent",
            "evaluator",
        ],
        "required_search_paths": ["simulation.environments"],
        "required_best_patch_keys": ["simulation.environments"],
        "required_best_environment_types": [
            "world_orchestration_replay",
            "framework_trace",
            "retrieval_memory",
            "agent_memory_lineage",
            "multi_agent_room",
        ],
        "required_optimizer": "AgentOptimizer",
        "min_optimization_score": 0.9,
        "min_evaluation_score": 1.0,
        "min_history_count": 2,
        "min_candidate_lineage_count": 2,
        "required_metrics": {
            "framework_trace_coverage": 1.0,
            "orchestration_flow_quality": 1.0,
            "world_contract_quality": 1.0,
            "retrieval_context_quality": 1.0,
            "agent_memory_lineage_quality": 1.0,
            "retrieval_memory_attribution": 1.0,
            "multi_agent_coordination_quality": 1.0,
        },
    },
    {
        "surface": "multi_agent_framework_handoff",
        "path": "examples/multi_agent_framework_handoff_optimization.json",
        "required_env": [
            "AGENT_LEARNING_MULTI_AGENT_FRAMEWORK_HANDOFF_OPT_EXAMPLE_KEY"
        ],
        "required_layers": ["framework", "multi_agent", "orchestration", "memory"],
        "required_search_paths": ["simulation.environments"],
        "required_best_patch_keys": ["simulation.environments"],
        "required_best_environment_types": [
            "framework_trace",
            "framework_trace",
            "framework_trace",
            "framework_trace",
            "multi_agent_room",
        ],
        "required_optimizer": "AgentEvolutionOptimizer",
        "min_optimization_score": 0.99,
        "min_evaluation_score": 1.0,
        "min_history_count": 3,
        "min_candidate_lineage_count": 3,
        "required_metrics": {
            "framework_trace_coverage": 1.0,
            "framework_transcript_quality": 1.0,
            "multi_agent_coordination_quality": 1.0,
            "tool_selection_accuracy": 1.0,
        },
        "required_proofs": ["multi_agent_coordination_proof"],
    },
    {
        "surface": "framework_certification",
        "path": "examples/framework_certification_optimization.json",
        "required_env": ["AGENT_LEARNING_FRAMEWORK_CERT_OPT_EXAMPLE_KEY"],
        "required_layers": ["framework", "integration", "harness", "evaluator"],
        "required_search_paths": ["simulation.environments"],
        "required_best_patch_keys": ["simulation.environments"],
        "required_best_environment_types": [
            "framework_lifecycle",
            "framework_capability",
            "framework_probe",
            "framework_portability",
        ],
        "required_optimizer": "AgentOptimizer",
        "min_optimization_score": 0.98,
        "min_evaluation_score": 1.0,
        "min_history_count": 2,
        "min_candidate_lineage_count": 2,
        "required_metrics": {
            "framework_lifecycle_quality": 1.0,
            "framework_capability_coverage": 1.0,
            "framework_probe_quality": 1.0,
            "framework_portability_quality": 1.0,
            "framework_trace_coverage": 1.0,
            "tool_selection_accuracy": 1.0,
        },
        "required_proofs": ["framework_certification_proof"],
    },
    {
        "surface": "framework_import_repair",
        "path": "examples/framework_import_repair_optimization.json",
        "required_env": [
            "AGENT_LEARNING_FRAMEWORK_IMPORT_REPAIR_OPT_EXAMPLE_KEY"
        ],
        "required_layers": ["framework", "integration", "evaluator"],
        "required_search_paths": ["simulation.environments"],
        "required_best_patch_keys": ["simulation.environments"],
        "required_best_environment_types": ["framework_import"],
        "required_optimizer": "AgentOptimizer",
        "min_optimization_score": 1.0,
        "min_evaluation_score": 1.0,
        "min_history_count": 3,
        "min_candidate_lineage_count": 3,
        "required_metrics": {
            "framework_import_coverage": 1.0,
            "framework_import_quality": 1.0,
            "tool_selection_accuracy": 1.0,
        },
    },
]

V1_PROTOCOL_ADAPTER_FILES = [
    "examples/sdk_framework_adapter_mcp_tool_session.py",
    "examples/sdk_framework_adapter_a2a_protocol_trace.py",
    "internal-docs/mcp-tool-session-adapter-research.md",
    "internal-docs/a2a-protocol-adapter-research.md",
]

V1_PROTOCOL_ADAPTER_CONTRACTS = [
    {
        "protocol": "mcp",
        "path": "examples/sdk_framework_adapter_mcp_tool_session.py",
        "manifest_key": "framework_adapter_mcp_tool_session_manifest",
        "framework": "mcp",
        "method": "execute_task",
        "input_mode": "dict",
        "state_key": "mcp_tool_session",
        "coverage_metric": "mcp_tool_session_coverage",
        "quality_metric": "mcp_tool_session_quality",
        "required_events": [
            "mcp_server",
            "mcp_tool_schema",
            "mcp_resource",
            "mcp_tool_call",
            "mcp_tool_result",
            "mcp_tool_session",
        ],
        "required_artifact_kinds": ["mcp_tool_session", "framework_runtime"],
        "summary_minimums": {
            "server_count": 1,
            "schema_count": 2,
            "resource_count": 1,
            "call_count": 2,
            "result_count": 2,
            "tool_count": 2,
            "tool_response_count": 2,
        },
        "summary_maximums": {"error_count": 0},
        "summary_contains": {
            "server_names": ["refund-tools"],
            "tool_names": ["refund_policy_lookup", "refund_status"],
        },
    },
    {
        "protocol": "a2a",
        "path": "examples/sdk_framework_adapter_a2a_protocol_trace.py",
        "manifest_key": "framework_adapter_a2a_protocol_trace_manifest",
        "framework": "a2a",
        "method": "send_message",
        "input_mode": "dict",
        "state_key": "a2a_protocol_trace",
        "coverage_metric": "a2a_protocol_coverage",
        "quality_metric": "a2a_protocol_quality",
        "required_events": [
            "a2a_agent_card",
            "a2a_message_send",
            "a2a_task_status",
            "a2a_task_artifact",
            "a2a_artifact",
            "a2a_protocol_trace",
        ],
        "required_artifact_kinds": [
            "a2a_protocol_trace",
            "a2a_artifact",
            "framework_runtime",
        ],
        "summary_minimums": {
            "agent_card_count": 1,
            "message_count": 3,
            "task_count": 1,
            "artifact_count": 1,
            "protocol_event_count": 5,
            "status_update_count": 3,
            "artifact_update_count": 1,
            "terminal_task_count": 1,
        },
        "summary_maximums": {"error_count": 0},
        "summary_contains": {
            "agent_names": ["refund-review-agent"],
            "skill_names": ["refund_review"],
            "roles": ["agent", "user"],
            "states": ["completed"],
        },
    },
]

V1_BROWSER_REALTIME_ADAPTER_FILES = [
    "examples/sdk_framework_adapter_realtime_trace.py",
    "examples/sdk_framework_adapter_browser_cua_trace.py",
    "internal-docs/realtime-stack-probe-research.md",
    "internal-docs/browser-cua-probe-research.md",
]

V1_BROWSER_REALTIME_ADAPTER_CONTRACTS = [
    {
        "surface": "realtime_trace",
        "path": "examples/sdk_framework_adapter_realtime_trace.py",
        "manifest_key": "framework_adapter_realtime_trace_manifest",
        "framework": "livekit",
        "method": "run_session",
        "input_mode": "dict",
        "state_key": "realtime_trace",
        "coverage_metric": "realtime_trace_coverage",
        "quality_metrics": ["realtime_trace_quality"],
        "required_tools": ["lookup_refund_policy"],
        "required_events": [
            "realtime_frame",
            "realtime_audio_frame",
            "realtime_tool_call",
            "realtime_tool_response",
            "realtime_transcript",
            "realtime_lifecycle",
            "realtime_completion",
        ],
        "required_artifact_kinds": [
            "framework_runtime",
            "framework_trace",
            "realtime_trace",
        ],
        "state_minimums": {
            "frame_count": 5,
            "event_count": 5,
            "tool_call_count": 2,
            "tool_response_count": 2,
            "transcript_count": 2,
            "audio_frame_count": 1,
            "lifecycle_event_count": 1,
            "completion_count": 2,
        },
        "state_maximums": {"error_count": 0},
        "state_contains": {
            "tool_names": ["lookup_refund_policy"],
            "directions": ["inbound", "outbound"],
            "frame_types": [
                "AudioRawFrame",
                "FunctionCallFrame",
                "FunctionCallResultFrame",
                "TranscriptionFrame",
            ],
            "event_types": [
                "agent_state_changed",
                "session_closed",
                "tool_execution_completed",
                "tool_execution_started",
                "transcript_final",
            ],
            "categories": ["control", "data", "event"],
            "modalities": ["voice"],
        },
    },
    {
        "surface": "browser_cua",
        "path": "examples/sdk_framework_adapter_browser_cua_trace.py",
        "manifest_key": "framework_adapter_browser_cua_trace_manifest",
        "framework": "browser_use",
        "method": "execute_task",
        "input_mode": "dict",
        "state_key": "browser_cua",
        "coverage_metric": "browser_trace_coverage",
        "quality_metrics": [
            "browser_action_safety",
            "browser_action_outcome",
            "browser_grounding_quality",
            "browser_mutation_resilience",
        ],
        "required_tools": ["browser_click"],
        "required_events": [
            "browser_snapshot",
            "browser_action",
            "browser_trace",
            "browser_network",
            "browser_runtime",
            "browser_storage",
            "browser_mutation_pack",
            "environment_injection",
        ],
        "required_artifact_kinds": [
            "browser_screenshot",
            "browser_trace",
            "framework_runtime",
            "framework_trace",
        ],
        "state_minimums": {
            "snapshot_count": 2,
            "action_count": 1,
            "successful_action_count": 1,
            "matched_action_count": 1,
            "screenshot_count": 2,
            "region_count": 1,
            "network_request_count": 1,
            "runtime_event_count": 1,
            "performance_entry_count": 1,
            "prompt_injection_surface_count": 1,
            "screenshot_diff_count": 1,
            "mutation_count": 1,
        },
        "state_maximums": {
            "blocked_action_count": 0,
            "prompt_injection_touched_count": 0,
        },
        "state_contains": {
            "action_types": ["click"],
            "tool_names": ["browser_click"],
        },
        "state_equals": {
            "layout_shift_present": True,
            "storage_present": True,
        },
    },
]

V1_STATEFUL_FRAMEWORK_ADAPTER_FILES = [
    "examples/sdk_framework_adapter_memory_trace.py",
    "examples/sdk_framework_adapter_workflow_trace.py",
    "examples/sdk_framework_adapter_orchestration_trace.py",
    "examples/sdk_framework_adapter_lifecycle_trace.py",
    "internal-docs/memory-layer-probe-research.md",
    "internal-docs/workflow-graph-probe-research.md",
    "internal-docs/orchestration-trace-adapter-research.md",
    "internal-docs/framework-lifecycle-adapter-research.md",
]

V1_STATEFUL_FRAMEWORK_ADAPTER_CONTRACTS = [
    {
        "surface": "memory_trace",
        "path": "examples/sdk_framework_adapter_memory_trace.py",
        "manifest_key": "framework_adapter_memory_trace_manifest",
        "framework": "langgraph",
        "method": "ainvoke",
        "input_mode": "dict",
        "state_key": "framework_memory",
        "required_state_keys": [
            "agent_memory_lineage",
            "framework_memory",
            "retrieval_memory",
        ],
        "coverage_metric": "agent_memory_lineage_coverage",
        "quality_metrics": [
            "agent_memory_lineage_quality",
            "retrieval_memory_attribution",
        ],
        "required_events": [
            "framework_memory_operation",
            "framework_memory_checkpoint",
            "framework_memory_retrieval",
            "framework_memory_record",
        ],
        "required_artifact_kinds": [
            "framework_memory",
            "framework_runtime",
            "framework_trace",
        ],
        "state_minimums": {
            "operation_count": 4,
            "checkpoint_count": 1,
            "memory_count": 1,
            "retrieval_count": 1,
            "store_count": 1,
            "policy_count": 6,
        },
        "state_contains": {
            "operation_types": ["read", "recall", "update", "write"],
            "source_ids": ["refund_policy_doc"],
            "namespaces": ["tenant_refunds"],
            "policy_keys": [
                "audit",
                "canary",
                "deletion",
                "redaction",
                "retention",
                "tenant_isolation",
            ],
        },
    },
    {
        "surface": "workflow_trace",
        "path": "examples/sdk_framework_adapter_workflow_trace.py",
        "manifest_key": "framework_adapter_workflow_trace_manifest",
        "framework": "langgraph",
        "method": "execute_task",
        "input_mode": "dict",
        "state_key": "workflow_trace",
        "coverage_metric": "workflow_trace_coverage",
        "quality_metrics": ["workflow_graph_quality"],
        "required_tools": ["policy_lookup"],
        "required_events": [
            "workflow_step",
            "workflow_route",
            "workflow_checkpoint",
            "workflow_interrupt",
            "workflow_replay",
            "workflow_trace",
        ],
        "required_artifact_kinds": [
            "framework_runtime",
            "framework_trace",
            "workflow_trace",
        ],
        "state_minimums": {
            "node_count": 4,
            "edge_count": 3,
            "step_count": 4,
            "checkpoint_count": 2,
            "route_decision_count": 1,
            "interrupt_count": 1,
            "replay_count": 1,
            "write_count": 1,
            "tool_call_count": 1,
        },
        "state_contains": {
            "tool_names": ["policy_lookup"],
            "final_state_keys": ["approval", "decision", "policy_result"],
            "topology.entry_nodes": ["intake"],
            "topology.terminal_nodes": ["finalize"],
        },
        "state_equals": {
            "has_replay": True,
            "has_interrupts": True,
            "has_routes": True,
        },
    },
    {
        "surface": "orchestration_trace",
        "path": "examples/sdk_framework_adapter_orchestration_trace.py",
        "manifest_key": "framework_adapter_orchestration_trace_manifest",
        "framework": "langgraph",
        "method": "execute_task",
        "input_mode": "dict",
        "state_key": "orchestration_trace",
        "state_summary_key": "summary",
        "coverage_metric": "orchestration_trace_coverage",
        "quality_metrics": ["orchestration_flow_quality"],
        "required_tools": ["policy_lookup"],
        "required_events": [
            "orchestration_step",
            "orchestration_trace",
        ],
        "required_artifact_kinds": [
            "framework_runtime",
            "framework_trace",
            "orchestration_trace",
        ],
        "state_minimums": {
            "node_count": 4,
            "edge_count": 3,
            "step_count": 6,
            "agent_count": 4,
            "spawn_count": 1,
            "delegation_count": 2,
            "communication_count": 2,
            "aggregation_count": 2,
            "stop_count": 1,
            "failure_count": 1,
            "retry_count": 1,
            "recovered_failures": 1,
        },
        "state_contains": {
            "signals": ["delegate", "handoff", "recovered", "stop", "tool"],
        },
        "state_equals": {"terminal_status": "success"},
    },
    {
        "surface": "lifecycle_trace",
        "path": "examples/sdk_framework_adapter_lifecycle_trace.py",
        "manifest_key": "framework_adapter_lifecycle_trace_manifest",
        "framework": "livekit",
        "method": "execute_task",
        "input_mode": "dict",
        "state_key": "framework_lifecycle_trace",
        "state_summary_key": "summary",
        "coverage_metric": "framework_lifecycle_coverage",
        "quality_metrics": ["framework_lifecycle_quality"],
        "required_tools": ["framework_lifecycle_status"],
        "required_events": [
            "framework_lifecycle_phase",
            "framework_lifecycle_trace",
        ],
        "required_artifact_kinds": [
            "framework_lifecycle_trace",
            "framework_runtime",
            "framework_trace",
        ],
        "state_minimums": {
            "phase_count": 10,
            "session_count": 1,
            "retry_count": 1,
            "error_count": 1,
            "recovered_error_count": 1,
            "cancellation_count": 1,
            "resume_count": 1,
            "cleanup_count": 1,
            "checkpoint_count": 2,
        },
        "state_contains": {
            "signals": [
                "checkpoint",
                "recovery",
                "resume",
                "retry",
                "state_persistence",
                "tool_registration",
            ],
        },
        "state_equals": {
            "state_persistence": True,
            "cleanup_complete": True,
            "terminal_status": "completed",
        },
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
    "openenv",
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
    openenv_optimizer = _release_openenv_optimizer_status(root)
    _append_release_check(
        checks,
        check_id="openenv_optimizer_readiness",
        passed=(
            not openenv_optimizer["missing_files"]
            and not openenv_optimizer["manifest_errors"]
            and not openenv_optimizer["optimization_errors"]
            and not openenv_optimizer["metric_errors"]
            and not openenv_optimizer["errors"]
        ),
        milestone="M6",
        evidence=openenv_optimizer,
    )
    framework_optimizer = _release_framework_optimizer_status(root)
    _append_release_check(
        checks,
        check_id="framework_optimizer_readiness",
        passed=(
            not framework_optimizer["missing_files"]
            and not framework_optimizer["manifest_errors"]
            and not framework_optimizer["optimization_errors"]
            and not framework_optimizer["metric_errors"]
            and not framework_optimizer["proof_errors"]
            and not framework_optimizer["errors"]
        ),
        milestone="M6",
        evidence=framework_optimizer,
    )
    protocol_adapter = _release_protocol_adapter_status(root)
    _append_release_check(
        checks,
        check_id="protocol_adapter_readiness",
        passed=(
            not protocol_adapter["missing_files"]
            and not protocol_adapter["adapter_errors"]
            and not protocol_adapter["event_errors"]
            and not protocol_adapter["artifact_errors"]
            and not protocol_adapter["metric_errors"]
            and not protocol_adapter["summary_errors"]
            and not protocol_adapter["errors"]
        ),
        milestone="M6",
        evidence=protocol_adapter,
    )
    browser_realtime_adapter = _release_browser_realtime_adapter_status(root)
    _append_release_check(
        checks,
        check_id="browser_realtime_adapter_readiness",
        passed=(
            not browser_realtime_adapter["missing_files"]
            and not browser_realtime_adapter["adapter_errors"]
            and not browser_realtime_adapter["event_errors"]
            and not browser_realtime_adapter["artifact_errors"]
            and not browser_realtime_adapter["metric_errors"]
            and not browser_realtime_adapter["state_errors"]
            and not browser_realtime_adapter["errors"]
        ),
        milestone="M6",
        evidence=browser_realtime_adapter,
    )
    stateful_framework_adapter = _release_stateful_framework_adapter_status(root)
    _append_release_check(
        checks,
        check_id="stateful_framework_adapter_readiness",
        passed=(
            not stateful_framework_adapter["missing_files"]
            and not stateful_framework_adapter["adapter_errors"]
            and not stateful_framework_adapter["event_errors"]
            and not stateful_framework_adapter["artifact_errors"]
            and not stateful_framework_adapter["metric_errors"]
            and not stateful_framework_adapter["state_errors"]
            and not stateful_framework_adapter["errors"]
        ),
        milestone="M6",
        evidence=stateful_framework_adapter,
    )
    trinity_stack_probe = _release_trinity_stack_probe_status(root)
    _append_release_check(
        checks,
        check_id="trinity_stack_probe_readiness",
        passed=(
            not trinity_stack_probe["missing_files"]
            and not trinity_stack_probe["optimization_errors"]
            and not trinity_stack_probe["proof_errors"]
            and not trinity_stack_probe["manifest_errors"]
            and not trinity_stack_probe["errors"]
        ),
        milestone="M6",
        evidence=trinity_stack_probe,
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
        "required_openenv_optimizer_files": list(V1_OPENENV_OPTIMIZER_FILES),
        "required_framework_optimizer_files": list(V1_FRAMEWORK_OPTIMIZER_FILES),
        "required_framework_optimizer_contracts": copy.deepcopy(
            V1_FRAMEWORK_OPTIMIZER_CONTRACTS
        ),
        "required_protocol_adapter_files": list(V1_PROTOCOL_ADAPTER_FILES),
        "required_protocol_adapter_contracts": copy.deepcopy(
            V1_PROTOCOL_ADAPTER_CONTRACTS
        ),
        "required_browser_realtime_adapter_files": list(
            V1_BROWSER_REALTIME_ADAPTER_FILES
        ),
        "required_browser_realtime_adapter_contracts": copy.deepcopy(
            V1_BROWSER_REALTIME_ADAPTER_CONTRACTS
        ),
        "required_stateful_framework_adapter_files": list(
            V1_STATEFUL_FRAMEWORK_ADAPTER_FILES
        ),
        "required_stateful_framework_adapter_contracts": copy.deepcopy(
            V1_STATEFUL_FRAMEWORK_ADAPTER_CONTRACTS
        ),
        "required_trinity_stack_probe_files": list(V1_TRINITY_STACK_PROBE_FILES),
        "required_trinity_stack_probe_environment_types": list(
            V1_TRINITY_STACK_PROBE_REQUIRED_ENVIRONMENT_TYPES
        ),
        "required_trinity_stack_probe_proof_kind": V1_TRINITY_STACK_PROBE_PROOF_KIND,
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
    required_openenv_capabilities = {
        "environment_replay",
        "reset_step_trace",
        "runtime_trace",
        "state",
        "artifacts",
    }
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
            expected_capabilities = (
                required_openenv_capabilities
                if framework in {"openenv", "gymnasium", "gymnasium_env"}
                else required_capabilities
            )
            missing_capabilities = sorted(expected_capabilities - capabilities)
            if missing_capabilities:
                contract_errors.append(
                    {
                        "framework": framework,
                        "field": "capabilities",
                        "missing": missing_capabilities,
                    }
                )
            expected_evidence = set(required_evidence)
            if framework in {"openenv", "gymnasium", "gymnasium_env"}:
                expected_evidence.add("openenv")
            missing_evidence = sorted(expected_evidence - evidence)
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
        evaluation = (
            payload.get("evaluation")
            if isinstance(payload.get("evaluation"), Mapping)
            else {}
        )
        agent_report = (
            evaluation.get("agent_report")
            if isinstance(evaluation.get("agent_report"), Mapping)
            else {}
        )
        eval_config = (
            agent_report.get("config")
            if isinstance(agent_report.get("config"), Mapping)
            else {}
        )
        metric_weights = (
            eval_config.get("metric_weights")
            if isinstance(eval_config.get("metric_weights"), Mapping)
            else {}
        )
        framework_runtime_contract = (
            eval_config.get("framework_runtime_contract")
            if isinstance(eval_config.get("framework_runtime_contract"), Mapping)
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
        required_eval_config_keys = [
            str(item) for item in spec.get("required_evaluation_config_keys", [])
        ]
        missing_eval_config_keys = sorted(
            set(required_eval_config_keys) - set(eval_config)
        )
        required_metric_weights = [
            str(item) for item in spec.get("required_metric_weights", [])
        ]
        missing_metric_weights = sorted(
            set(required_metric_weights) - set(metric_weights)
        )
        required_runtime_signals = [
            str(item) for item in spec.get("required_framework_runtime_signals", [])
        ]
        observed_runtime_signals = [
            str(item)
            for item in framework_runtime_contract.get("required_signals", [])
        ]
        missing_runtime_signals = sorted(
            set(required_runtime_signals) - set(observed_runtime_signals)
        )
        required_state_keys = [
            str(item) for item in spec.get("required_state_keys", [])
        ]
        observed_state_keys = [
            str(item)
            for item in framework_runtime_contract.get("required_state_keys", [])
        ]
        missing_state_keys = sorted(set(required_state_keys) - set(observed_state_keys))
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
                "evaluation_config_keys": sorted(str(key) for key in eval_config),
                "required_evaluation_config_keys": required_eval_config_keys,
                "missing_evaluation_config_keys": missing_eval_config_keys,
                "metric_weights": sorted(str(key) for key in metric_weights),
                "required_metric_weights": required_metric_weights,
                "missing_metric_weights": missing_metric_weights,
                "framework_runtime_required_signals": observed_runtime_signals,
                "required_framework_runtime_signals": required_runtime_signals,
                "missing_framework_runtime_signals": missing_runtime_signals,
                "framework_runtime_required_state_keys": observed_state_keys,
                "required_state_keys": required_state_keys,
                "missing_state_keys": missing_state_keys,
                "required_openenv": [
                    str(item) for item in eval_config.get("required_openenv", [])
                ],
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
        if missing_eval_config_keys:
            manifest_errors.append(
                {
                    "path": relative_path,
                    "field": "evaluation.agent_report.config",
                    "required": required_eval_config_keys,
                    "observed": sorted(str(key) for key in eval_config),
                    "missing": missing_eval_config_keys,
                }
            )
        if missing_metric_weights:
            manifest_errors.append(
                {
                    "path": relative_path,
                    "field": "evaluation.agent_report.config.metric_weights",
                    "required": required_metric_weights,
                    "observed": sorted(str(key) for key in metric_weights),
                    "missing": missing_metric_weights,
                }
            )
        if missing_runtime_signals:
            manifest_errors.append(
                {
                    "path": relative_path,
                    "field": (
                        "evaluation.agent_report.config."
                        "framework_runtime_contract.required_signals"
                    ),
                    "required": required_runtime_signals,
                    "observed": observed_runtime_signals,
                    "missing": missing_runtime_signals,
                }
            )
        if missing_state_keys:
            manifest_errors.append(
                {
                    "path": relative_path,
                    "field": (
                        "evaluation.agent_report.config."
                        "framework_runtime_contract.required_state_keys"
                    ),
                    "required": required_state_keys,
                    "observed": observed_state_keys,
                    "missing": missing_state_keys,
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


def _release_openenv_optimizer_status(root: Path) -> dict[str, Any]:
    missing_files = _missing_relative_paths(root, V1_OPENENV_OPTIMIZER_FILES)
    manifest_errors: list[dict[str, Any]] = []
    optimization_errors: list[dict[str, Any]] = []
    metric_errors: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {}

    if not missing_files:
        example_path = root / "examples/sdk_openenv_environment_optimization.py"
        try:
            spec = importlib.util.spec_from_file_location(
                "agent_learning_release_openenv_optimizer",
                example_path,
            )
            if spec is None or spec.loader is None:
                raise RuntimeError(f"Unable to load {example_path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            manifest = module.build_manifest(required_env=())
            result = module.run(required_env=())
        except Exception as exc:
            errors.append({"path": str(example_path.relative_to(root)), "error": str(exc)})
            manifest = {}
            result = {}

        if manifest:
            optimization = _as_mapping(manifest.get("optimization"))
            target = _as_mapping(optimization.get("target"))
            scoring = _as_mapping(optimization.get("scoring"))
            search_space = _as_mapping(target.get("search_space"))
            candidates = _as_list(search_space.get("simulation.environments"))
            profiles: list[str] = []
            environment_types: list[str] = []
            for candidate in candidates:
                environments = [
                    item for item in _as_list(candidate) if isinstance(item, Mapping)
                ]
                if not environments:
                    continue
                environment = _as_mapping(environments[0])
                data = _as_mapping(environment.get("data"))
                metadata = _as_mapping(data.get("metadata"))
                if environment.get("type"):
                    environment_types.append(str(environment.get("type")))
                if metadata.get("candidate_profile"):
                    profiles.append(str(metadata.get("candidate_profile")))
            target_metadata = _as_mapping(target.get("metadata"))
            research_urls = sorted(
                str(source.get("url"))
                for source in _as_list(target_metadata.get("research_sources"))
                if isinstance(source, Mapping) and source.get("url")
            )
            evidence.update(
                {
                    "manifest_version": manifest.get("version"),
                    "manifest_required_env": list(manifest.get("required_env") or []),
                    "manifest_scoring_layers": list(scoring.get("layers") or []),
                    "manifest_candidate_count": len(candidates),
                    "manifest_candidate_environment_types": environment_types,
                    "manifest_candidate_profiles": profiles,
                    "manifest_research_urls": research_urls,
                }
            )
            if manifest.get("version") != "agent-learning.optimization.v1":
                manifest_errors.append(
                    {
                        "field": "version",
                        "expected": "agent-learning.optimization.v1",
                        "observed": manifest.get("version"),
                    }
                )
            if manifest.get("required_env") not in (None, []):
                manifest_errors.append(
                    {
                        "field": "required_env",
                        "expected": [],
                        "observed": manifest.get("required_env"),
                    }
                )
            if scoring.get("layers") != ["openenv"]:
                manifest_errors.append(
                    {
                        "field": "optimization.scoring.layers",
                        "expected": ["openenv"],
                        "observed": scoring.get("layers"),
                    }
                )
            missing_profiles = sorted(
                set(V1_OPENENV_OPTIMIZER_REQUIRED_PROFILES) - set(profiles)
            )
            if missing_profiles:
                manifest_errors.append(
                    {
                        "field": "optimization.target.search_space",
                        "expected": list(V1_OPENENV_OPTIMIZER_REQUIRED_PROFILES),
                        "observed": profiles,
                        "missing": missing_profiles,
                    }
                )
            if "openenv" not in set(environment_types):
                manifest_errors.append(
                    {
                        "field": "optimization.target.search_space.environment.type",
                        "expected": "openenv",
                        "observed": environment_types,
                    }
                )

        if result:
            summary = _as_mapping(result.get("summary"))
            optimization = _as_mapping(result.get("optimization"))
            histories = [
                item for item in _as_list(optimization.get("history"))
                if isinstance(item, Mapping)
            ]
            best_history: Mapping[str, Any] = {}
            best_score = -1.0
            for history in histories:
                score = _float_or_zero(history.get("score"))
                if score > best_score:
                    best_score = score
                    best_history = history
            best_config = _as_mapping(optimization.get("best_config"))
            best_simulation = _as_mapping(best_config.get("simulation"))
            best_environments = [
                item for item in _as_list(best_simulation.get("environments"))
                if isinstance(item, Mapping)
            ]
            best_environment = (
                _as_mapping(best_environments[0]) if best_environments else {}
            )
            best_data = _as_mapping(best_environment.get("data"))
            best_metadata = _as_mapping(best_data.get("metadata"))
            best_metrics = _as_mapping(best_history.get("metrics"))
            best_profile = str(best_metadata.get("candidate_profile") or "")
            evidence.update(
                {
                    "result_kind": result.get("kind"),
                    "result_status": result.get("status"),
                    "optimization_score": summary.get("optimization_score"),
                    "evaluation_score": summary.get("evaluation_score"),
                    "total_iterations": summary.get("total_iterations"),
                    "candidate_lineage_count": summary.get(
                        "candidate_lineage_count"
                    ),
                    "candidate_lineage_selected_score_delta": summary.get(
                        "candidate_lineage_selected_score_delta"
                    ),
                    "best_history_score": best_history.get("score"),
                    "best_candidate_profile": best_profile,
                    "best_environment_type": best_environment.get("type"),
                    "best_metrics": {
                        metric: best_metrics.get(metric)
                        for metric in V1_OPENENV_OPTIMIZER_REQUIRED_METRICS
                    },
                }
            )
            result_expectations = {
                "kind": (result.get("kind"), "agent-learning.optimization.v1"),
                "status": (result.get("status"), "passed"),
            }
            for field, (observed, expected) in result_expectations.items():
                if observed != expected:
                    optimization_errors.append(
                        {
                            "field": field,
                            "expected": expected,
                            "observed": observed,
                        }
                    )
            if _float_or_zero(summary.get("optimization_score")) < 1.0:
                optimization_errors.append(
                    {
                        "field": "summary.optimization_score",
                        "expected": 1.0,
                        "observed": summary.get("optimization_score"),
                    }
                )
            if _float_or_zero(summary.get("evaluation_score")) < 1.0:
                optimization_errors.append(
                    {
                        "field": "summary.evaluation_score",
                        "expected": 1.0,
                        "observed": summary.get("evaluation_score"),
                    }
                )
            if best_environment.get("type") != "openenv":
                optimization_errors.append(
                    {
                        "field": "optimization.best_config.simulation.environments.type",
                        "expected": "openenv",
                        "observed": best_environment.get("type"),
                    }
                )
            if best_profile != "verified_openenv_replay":
                optimization_errors.append(
                    {
                        "field": (
                            "optimization.best_config.simulation.environments."
                            "data.metadata.candidate_profile"
                        ),
                        "expected": "verified_openenv_replay",
                        "observed": best_profile,
                    }
                )
            if _int_or_zero(summary.get("candidate_lineage_count")) < len(
                V1_OPENENV_OPTIMIZER_REQUIRED_PROFILES
            ):
                optimization_errors.append(
                    {
                        "field": "summary.candidate_lineage_count",
                        "expected": f">={len(V1_OPENENV_OPTIMIZER_REQUIRED_PROFILES)}",
                        "observed": summary.get("candidate_lineage_count"),
                    }
                )
            for metric in V1_OPENENV_OPTIMIZER_REQUIRED_METRICS:
                if _float_or_zero(best_metrics.get(metric)) < 1.0:
                    metric_errors.append(
                        {
                            "field": f"optimization.history.best.metrics.{metric}",
                            "expected": 1.0,
                            "observed": best_metrics.get(metric),
                        }
                    )

    return {
        "required_files": list(V1_OPENENV_OPTIMIZER_FILES),
        "required_profiles": list(V1_OPENENV_OPTIMIZER_REQUIRED_PROFILES),
        "required_metrics": list(V1_OPENENV_OPTIMIZER_REQUIRED_METRICS),
        "missing_files": missing_files,
        "manifest_errors": manifest_errors,
        "optimization_errors": optimization_errors,
        "metric_errors": metric_errors,
        "errors": errors,
        "evidence": evidence,
    }


def _release_framework_optimizer_status(root: Path) -> dict[str, Any]:
    missing_files = _missing_relative_paths(root, V1_FRAMEWORK_OPTIMIZER_FILES)
    manifest_errors: list[dict[str, Any]] = []
    optimization_errors: list[dict[str, Any]] = []
    metric_errors: list[dict[str, Any]] = []
    proof_errors: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    optimizations: list[dict[str, Any]] = []

    if not missing_files:
        try:
            from agent_learning import optimize
        except Exception as exc:
            errors.append({"path": "agent_learning.optimize", "error": str(exc)})
            optimize = None  # type: ignore[assignment]

        if optimize is not None:
            for contract in V1_FRAMEWORK_OPTIMIZER_CONTRACTS:
                surface = str(contract["surface"])
                relative_path = str(contract["path"])
                example_path = root / relative_path
                manifest: Mapping[str, Any] = {}
                result: Mapping[str, Any] = {}

                try:
                    manifest = json.loads(example_path.read_text(encoding="utf-8"))
                    result = _release_run_with_local_env(
                        _as_list(contract.get("required_env")),
                        lambda path=example_path: optimize.optimize_manifest_file(path),
                    )
                except Exception as exc:
                    errors.append({"surface": surface, "path": relative_path, "error": str(exc)})

                if manifest:
                    _append_framework_optimizer_manifest_errors(
                        manifest_errors,
                        surface=surface,
                        path=relative_path,
                        manifest=manifest,
                        contract=contract,
                    )

                if result:
                    record = _framework_optimizer_record(result, contract)
                    record["surface"] = surface
                    record["path"] = relative_path
                    optimizations.append(record)
                    _append_framework_optimizer_result_errors(
                        optimization_errors,
                        metric_errors,
                        proof_errors,
                        surface=surface,
                        path=relative_path,
                        result=result,
                        contract=contract,
                        record=record,
                    )

    return {
        "required_files": list(V1_FRAMEWORK_OPTIMIZER_FILES),
        "required_contracts": copy.deepcopy(V1_FRAMEWORK_OPTIMIZER_CONTRACTS),
        "missing_files": missing_files,
        "manifest_errors": manifest_errors,
        "optimization_errors": optimization_errors,
        "metric_errors": metric_errors,
        "proof_errors": proof_errors,
        "errors": errors,
        "optimizations": optimizations,
    }


def _release_run_with_local_env(
    required_env: Sequence[Any],
    callback: Any,
) -> Any:
    env_names = [str(name) for name in required_env if str(name)]
    previous = {name: os.environ.get(name) for name in env_names}
    try:
        for name in env_names:
            os.environ.setdefault(name, f"agent-learning-release-local-{name.lower()}")
        return callback()
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _append_framework_optimizer_manifest_errors(
    errors: list[dict[str, Any]],
    *,
    surface: str,
    path: str,
    manifest: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> None:
    if manifest.get("version") != "agent-learning.optimization.v1":
        errors.append(
            {
                "surface": surface,
                "path": path,
                "field": "version",
                "expected": "agent-learning.optimization.v1",
                "observed": manifest.get("version"),
            }
        )

    required_env = [str(item) for item in _as_list(contract.get("required_env"))]
    observed_env = [str(item) for item in _as_list(manifest.get("required_env"))]
    if sorted(observed_env) != sorted(required_env):
        errors.append(
            {
                "surface": surface,
                "path": path,
                "field": "required_env",
                "expected": required_env,
                "observed": observed_env,
            }
        )

    optimization = _as_mapping(manifest.get("optimization"))
    target = _as_mapping(optimization.get("target"))
    layers = {str(item) for item in _as_list(target.get("layers"))}
    missing_layers = sorted({str(item) for item in _as_list(contract.get("required_layers"))} - layers)
    if missing_layers:
        errors.append(
            {
                "surface": surface,
                "path": path,
                "field": "optimization.target.layers",
                "expected": _as_list(contract.get("required_layers")),
                "observed": sorted(layers),
                "missing": missing_layers,
            }
        )

    search_space = _as_mapping(target.get("search_space"))
    missing_search_paths = sorted(
        {str(item) for item in _as_list(contract.get("required_search_paths"))}
        - set(str(key) for key in search_space)
    )
    if missing_search_paths:
        errors.append(
            {
                "surface": surface,
                "path": path,
                "field": "optimization.target.search_space",
                "expected": _as_list(contract.get("required_search_paths")),
                "observed": sorted(str(key) for key in search_space),
                "missing": missing_search_paths,
            }
        )


def _framework_optimizer_record(
    result: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    summary = _as_mapping(result.get("summary"))
    optimization = _as_mapping(result.get("optimization"))
    histories = [
        item for item in _as_list(optimization.get("history")) if isinstance(item, Mapping)
    ]
    best_history: Mapping[str, Any] = {}
    best_score = -1.0
    for history in histories:
        score = _float_or_zero(history.get("score"))
        if score > best_score:
            best_score = score
            best_history = history

    best_config = _as_mapping(optimization.get("best_config"))
    best_agent = _as_mapping(best_config.get("agent"))
    best_simulation = _as_mapping(best_config.get("simulation"))
    best_environments = [
        item for item in _as_list(best_simulation.get("environments"))
        if isinstance(item, Mapping)
    ]
    best_metrics = _as_mapping(best_history.get("metrics"))
    search_paths = [str(item) for item in _as_list(summary.get("search_paths"))]
    required_metrics = _as_mapping(contract.get("required_metrics"))
    proof_keys = sorted(
        {
            key
            for source in (result, optimization)
            for key in source
            if str(key).endswith("_proof") or str(key).endswith("_trace")
        }
    )

    return {
        "result_kind": result.get("kind"),
        "result_status": result.get("status"),
        "optimization_score": summary.get("optimization_score"),
        "evaluation_score": summary.get("evaluation_score"),
        "history_count": len(histories),
        "candidate_lineage_count": summary.get("candidate_lineage_count"),
        "search_paths": sorted(
            set(search_paths)
            & {str(item) for item in _as_list(contract.get("required_search_paths"))}
        ),
        "search_path_count": len(search_paths),
        "best_history_score": best_history.get("score"),
        "best_patch_keys": sorted(str(key) for key in _as_mapping(best_history.get("patch"))),
        "best_agent": {
            key: best_agent.get(key)
            for key in sorted(_as_mapping(contract.get("expected_best_agent")))
        },
        "best_environment_types": [
            str(environment.get("type")) for environment in best_environments
        ],
        "best_metrics": {
            str(metric): best_metrics.get(metric) for metric in required_metrics
        },
        "optimizer_trace": _as_mapping(optimization.get("optimizer_trace")).get(
            "optimizer"
        ),
        "proof_keys": proof_keys,
    }


def _append_framework_optimizer_result_errors(
    optimization_errors: list[dict[str, Any]],
    metric_errors: list[dict[str, Any]],
    proof_errors: list[dict[str, Any]],
    *,
    surface: str,
    path: str,
    result: Mapping[str, Any],
    contract: Mapping[str, Any],
    record: Mapping[str, Any],
) -> None:
    if result.get("kind") != "agent-learning.optimization.v1":
        optimization_errors.append(
            {
                "surface": surface,
                "path": path,
                "field": "kind",
                "expected": "agent-learning.optimization.v1",
                "observed": result.get("kind"),
            }
        )
    if result.get("status") != "passed":
        optimization_errors.append(
            {
                "surface": surface,
                "path": path,
                "field": "status",
                "expected": "passed",
                "observed": result.get("status"),
            }
        )

    summary = _as_mapping(result.get("summary"))
    optimization = _as_mapping(result.get("optimization"))
    histories = [
        item for item in _as_list(optimization.get("history")) if isinstance(item, Mapping)
    ]
    best_history = max(histories, key=lambda item: _float_or_zero(item.get("score")), default={})
    best_metrics = _as_mapping(best_history.get("metrics"))

    _append_framework_optimizer_minimum_error(
        optimization_errors,
        surface=surface,
        path=path,
        field="summary.optimization_score",
        observed=summary.get("optimization_score"),
        minimum=contract.get("min_optimization_score"),
    )
    _append_framework_optimizer_minimum_error(
        optimization_errors,
        surface=surface,
        path=path,
        field="summary.evaluation_score",
        observed=summary.get("evaluation_score"),
        minimum=contract.get("min_evaluation_score"),
    )
    _append_framework_optimizer_minimum_error(
        optimization_errors,
        surface=surface,
        path=path,
        field="optimization.history",
        observed=len(histories),
        minimum=contract.get("min_history_count"),
    )
    _append_framework_optimizer_minimum_error(
        optimization_errors,
        surface=surface,
        path=path,
        field="summary.candidate_lineage_count",
        observed=summary.get("candidate_lineage_count"),
        minimum=contract.get("min_candidate_lineage_count"),
    )

    search_paths = {str(item) for item in _as_list(summary.get("search_paths"))}
    missing_search_paths = sorted(
        {str(item) for item in _as_list(contract.get("required_search_paths"))}
        - search_paths
    )
    if missing_search_paths:
        optimization_errors.append(
            {
                "surface": surface,
                "path": path,
                "field": "summary.search_paths",
                "expected": _as_list(contract.get("required_search_paths")),
                "observed": sorted(search_paths),
                "missing": missing_search_paths,
            }
        )

    missing_patch_keys = sorted(
        {str(item) for item in _as_list(contract.get("required_best_patch_keys"))}
        - set(str(key) for key in _as_list(record.get("best_patch_keys")))
    )
    if missing_patch_keys:
        optimization_errors.append(
            {
                "surface": surface,
                "path": path,
                "field": "optimization.best_history.patch",
                "expected": _as_list(contract.get("required_best_patch_keys")),
                "observed": record.get("best_patch_keys"),
                "missing": missing_patch_keys,
            }
        )

    expected_agent = _as_mapping(contract.get("expected_best_agent"))
    best_config = _as_mapping(optimization.get("best_config"))
    best_agent = _as_mapping(best_config.get("agent"))
    for field, expected in expected_agent.items():
        if best_agent.get(field) != expected:
            optimization_errors.append(
                {
                    "surface": surface,
                    "path": path,
                    "field": f"optimization.best_config.agent.{field}",
                    "expected": expected,
                    "observed": best_agent.get(field),
                }
            )

    expected_environment_types = [
        str(item) for item in _as_list(contract.get("required_best_environment_types"))
    ]
    if expected_environment_types and record.get("best_environment_types") != expected_environment_types:
        optimization_errors.append(
            {
                "surface": surface,
                "path": path,
                "field": "optimization.best_config.simulation.environments.type",
                "expected": expected_environment_types,
                "observed": record.get("best_environment_types"),
            }
        )

    expected_optimizer = str(contract.get("required_optimizer") or "")
    if expected_optimizer and record.get("optimizer_trace") != expected_optimizer:
        optimization_errors.append(
            {
                "surface": surface,
                "path": path,
                "field": "optimization.optimizer_trace.optimizer",
                "expected": expected_optimizer,
                "observed": record.get("optimizer_trace"),
            }
        )

    for metric, minimum in _as_mapping(contract.get("required_metrics")).items():
        observed = best_metrics.get(metric)
        if _float_or_zero(observed) < float(minimum):
            metric_errors.append(
                {
                    "surface": surface,
                    "path": path,
                    "field": f"optimization.history.best.metrics.{metric}",
                    "expected": f">={minimum}",
                    "observed": observed,
                }
            )

    for proof_key in [str(item) for item in _as_list(contract.get("required_proofs"))]:
        proof = _as_mapping(result.get(proof_key)) or _as_mapping(
            optimization.get(proof_key)
        )
        if not proof:
            proof_errors.append(
                {
                    "surface": surface,
                    "path": path,
                    "field": proof_key,
                    "expected": "present",
                    "observed": None,
                }
            )
            continue
        if proof.get("status") != "passed" or proof.get("passed") is not True:
            proof_errors.append(
                {
                    "surface": surface,
                    "path": path,
                    "field": proof_key,
                    "expected": {"status": "passed", "passed": True},
                    "observed": {
                        "status": proof.get("status"),
                        "passed": proof.get("passed"),
                    },
                }
            )


def _append_framework_optimizer_minimum_error(
    errors: list[dict[str, Any]],
    *,
    surface: str,
    path: str,
    field: str,
    observed: Any,
    minimum: Any,
) -> None:
    if minimum is None:
        return
    if _float_or_zero(observed) >= float(minimum):
        return
    errors.append(
        {
            "surface": surface,
            "path": path,
            "field": field,
            "expected": f">={minimum}",
            "observed": observed,
        }
    )


def _release_protocol_adapter_status(root: Path) -> dict[str, Any]:
    missing_files = _missing_relative_paths(root, V1_PROTOCOL_ADAPTER_FILES)
    adapter_errors: list[dict[str, Any]] = []
    event_errors: list[dict[str, Any]] = []
    artifact_errors: list[dict[str, Any]] = []
    metric_errors: list[dict[str, Any]] = []
    summary_errors: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    adapters: list[dict[str, Any]] = []

    if not missing_files:
        for contract in V1_PROTOCOL_ADAPTER_CONTRACTS:
            protocol = str(contract["protocol"])
            relative_path = str(contract["path"])
            example_path = root / relative_path
            try:
                spec = importlib.util.spec_from_file_location(
                    f"agent_learning_release_protocol_{protocol}",
                    example_path,
                )
                if spec is None or spec.loader is None:
                    raise RuntimeError(f"Unable to load {example_path}")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                with tempfile.TemporaryDirectory(
                    prefix=f"agent-learning-{protocol}-"
                ) as tmpdir:
                    result = module.run(Path(tmpdir) / f"{protocol}.json")
            except Exception as exc:
                errors.append({"path": relative_path, "protocol": protocol, "error": str(exc)})
                continue

            manifest = _as_mapping(result.get(str(contract["manifest_key"])))
            agent = _as_mapping(manifest.get("agent"))
            evaluation = _as_mapping(manifest.get("evaluation"))
            agent_report = _as_mapping(evaluation.get("agent_report"))
            eval_config = _as_mapping(agent_report.get("config"))
            metric_weights = _as_mapping(eval_config.get("metric_weights"))
            runtime_contract = _as_mapping(
                eval_config.get("framework_runtime_contract")
            )
            summary = _as_mapping(result.get("summary"))
            metric_averages = _as_mapping(summary.get("metric_averages"))
            report = _as_mapping(result.get("report"))
            cases = [item for item in _as_list(report.get("results")) if isinstance(item, Mapping)]
            case = _as_mapping(cases[0]) if cases else {}
            metadata = _as_mapping(case.get("metadata"))
            environment_state = _as_mapping(metadata.get("environment_state"))
            state_key = str(contract["state_key"])
            protocol_state = _as_mapping(environment_state.get(state_key))
            protocol_summary = _as_mapping(protocol_state.get("summary"))
            events = [item for item in _as_list(case.get("events")) if isinstance(item, Mapping)]
            event_types = sorted({str(event.get("type") or "") for event in events if event.get("type")})
            artifacts = [
                item for item in _as_list(case.get("artifacts")) if isinstance(item, Mapping)
            ]
            artifact_kinds = sorted(
                {
                    str(_as_mapping(artifact.get("metadata")).get("kind") or "")
                    for artifact in artifacts
                    if _as_mapping(artifact.get("metadata")).get("kind")
                }
            )
            coverage_metric = str(contract["coverage_metric"])
            quality_metric = str(contract["quality_metric"])
            record = {
                "protocol": protocol,
                "path": relative_path,
                "result_kind": result.get("kind"),
                "result_status": result.get("status"),
                "manifest_version": manifest.get("version"),
                "agent_framework": agent.get("framework"),
                "agent_method": agent.get("method"),
                "agent_input_mode": agent.get("input_mode"),
                "trace_runtime": agent.get("trace_runtime"),
                "required_env": list(manifest.get("required_env") or []),
                "runtime_required_state_keys": list(
                    runtime_contract.get("required_state_keys") or []
                ),
                "metric_weights": sorted(str(key) for key in metric_weights),
                "state_keys": sorted(str(key) for key in environment_state),
                "event_types": event_types,
                "artifact_kinds": artifact_kinds,
                "metrics": {
                    coverage_metric: metric_averages.get(coverage_metric),
                    quality_metric: metric_averages.get(quality_metric),
                    "framework_runtime_contract": metric_averages.get(
                        "framework_runtime_contract"
                    ),
                },
                "summary": protocol_summary,
            }
            adapters.append(record)

            expectations = {
                "result.kind": (result.get("kind"), "agent-learning.run.v1"),
                "result.status": (result.get("status"), "passed"),
                "manifest.version": (manifest.get("version"), "agent-learning.run.v1"),
                "agent.framework": (agent.get("framework"), contract["framework"]),
                "agent.method": (agent.get("method"), contract["method"]),
                "agent.input_mode": (agent.get("input_mode"), contract["input_mode"]),
                "agent.trace_runtime": (agent.get("trace_runtime"), True),
            }
            for field, (observed, expected) in expectations.items():
                if observed != expected:
                    adapter_errors.append(
                        {
                            "protocol": protocol,
                            "path": relative_path,
                            "field": field,
                            "expected": expected,
                            "observed": observed,
                        }
                    )
            if manifest.get("required_env") not in (None, []):
                adapter_errors.append(
                    {
                        "protocol": protocol,
                        "path": relative_path,
                        "field": "required_env",
                        "expected": [],
                        "observed": manifest.get("required_env"),
                    }
                )
            if state_key not in environment_state:
                adapter_errors.append(
                    {
                        "protocol": protocol,
                        "path": relative_path,
                        "field": "environment_state",
                        "expected": state_key,
                        "observed": sorted(str(key) for key in environment_state),
                    }
                )
            if state_key not in set(runtime_contract.get("required_state_keys") or []):
                adapter_errors.append(
                    {
                        "protocol": protocol,
                        "path": relative_path,
                        "field": (
                            "evaluation.agent_report.config."
                            "framework_runtime_contract.required_state_keys"
                        ),
                        "expected": state_key,
                        "observed": runtime_contract.get("required_state_keys"),
                    }
                )
            missing_metric_weights = sorted(
                {coverage_metric, quality_metric} - set(str(key) for key in metric_weights)
            )
            if missing_metric_weights:
                adapter_errors.append(
                    {
                        "protocol": protocol,
                        "path": relative_path,
                        "field": "evaluation.agent_report.config.metric_weights",
                        "missing": missing_metric_weights,
                    }
                )

            missing_events = sorted(set(contract["required_events"]) - set(event_types))
            if missing_events:
                event_errors.append(
                    {
                        "protocol": protocol,
                        "path": relative_path,
                        "required": list(contract["required_events"]),
                        "observed": event_types,
                        "missing": missing_events,
                    }
                )
            missing_artifacts = sorted(
                set(contract["required_artifact_kinds"]) - set(artifact_kinds)
            )
            if missing_artifacts:
                artifact_errors.append(
                    {
                        "protocol": protocol,
                        "path": relative_path,
                        "required": list(contract["required_artifact_kinds"]),
                        "observed": artifact_kinds,
                        "missing": missing_artifacts,
                    }
                )
            for metric in (coverage_metric, quality_metric, "framework_runtime_contract"):
                if _float_or_zero(metric_averages.get(metric)) < 1.0:
                    metric_errors.append(
                        {
                            "protocol": protocol,
                            "path": relative_path,
                            "metric": metric,
                            "expected": 1.0,
                            "observed": metric_averages.get(metric),
                        }
                    )
            for field, minimum in _as_mapping(contract.get("summary_minimums")).items():
                if _float_or_zero(protocol_summary.get(field)) < float(minimum):
                    summary_errors.append(
                        {
                            "protocol": protocol,
                            "path": relative_path,
                            "field": f"summary.{field}",
                            "expected": f">={minimum}",
                            "observed": protocol_summary.get(field),
                        }
                    )
            for field, maximum in _as_mapping(contract.get("summary_maximums")).items():
                if _float_or_zero(protocol_summary.get(field)) > float(maximum):
                    summary_errors.append(
                        {
                            "protocol": protocol,
                            "path": relative_path,
                            "field": f"summary.{field}",
                            "expected": f"<={maximum}",
                            "observed": protocol_summary.get(field),
                        }
                    )
            for field, required_values in _as_mapping(contract.get("summary_contains")).items():
                observed_values = {str(item) for item in _as_list(protocol_summary.get(field))}
                missing_values = sorted(
                    {str(item) for item in _as_list(required_values)} - observed_values
                )
                if missing_values:
                    summary_errors.append(
                        {
                            "protocol": protocol,
                            "path": relative_path,
                            "field": f"summary.{field}",
                            "required": list(required_values),
                            "observed": sorted(observed_values),
                            "missing": missing_values,
                        }
                    )

    return {
        "required_files": list(V1_PROTOCOL_ADAPTER_FILES),
        "required_contracts": copy.deepcopy(V1_PROTOCOL_ADAPTER_CONTRACTS),
        "missing_files": missing_files,
        "adapter_errors": adapter_errors,
        "event_errors": event_errors,
        "artifact_errors": artifact_errors,
        "metric_errors": metric_errors,
        "summary_errors": summary_errors,
        "errors": errors,
        "adapters": adapters,
    }


def _release_browser_realtime_adapter_status(root: Path) -> dict[str, Any]:
    return _release_semantic_framework_adapter_status(
        root,
        required_files=V1_BROWSER_REALTIME_ADAPTER_FILES,
        contracts=V1_BROWSER_REALTIME_ADAPTER_CONTRACTS,
    )


def _release_stateful_framework_adapter_status(root: Path) -> dict[str, Any]:
    return _release_semantic_framework_adapter_status(
        root,
        required_files=V1_STATEFUL_FRAMEWORK_ADAPTER_FILES,
        contracts=V1_STATEFUL_FRAMEWORK_ADAPTER_CONTRACTS,
    )


def _release_semantic_framework_adapter_status(
    root: Path,
    *,
    required_files: Sequence[str],
    contracts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    missing_files = _missing_relative_paths(root, required_files)
    adapter_errors: list[dict[str, Any]] = []
    event_errors: list[dict[str, Any]] = []
    artifact_errors: list[dict[str, Any]] = []
    metric_errors: list[dict[str, Any]] = []
    state_errors: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    adapters: list[dict[str, Any]] = []

    if not missing_files:
        for contract in contracts:
            surface = str(contract["surface"])
            relative_path = str(contract["path"])
            example_path = root / relative_path
            try:
                spec = importlib.util.spec_from_file_location(
                    f"agent_learning_release_{surface}_adapter",
                    example_path,
                )
                if spec is None or spec.loader is None:
                    raise RuntimeError(f"Unable to load {example_path}")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                with tempfile.TemporaryDirectory(
                    prefix=f"agent-learning-{surface}-"
                ) as tmpdir:
                    result = module.run(Path(tmpdir) / f"{surface}.json")
            except Exception as exc:
                errors.append({"path": relative_path, "surface": surface, "error": str(exc)})
                continue

            manifest = _as_mapping(result.get(str(contract["manifest_key"])))
            agent = _as_mapping(manifest.get("agent"))
            evaluation = _as_mapping(manifest.get("evaluation"))
            agent_report = _as_mapping(evaluation.get("agent_report"))
            eval_config = _as_mapping(agent_report.get("config"))
            metric_weights = _as_mapping(eval_config.get("metric_weights"))
            runtime_contract = _as_mapping(
                eval_config.get("framework_runtime_contract")
            )
            summary = _as_mapping(result.get("summary"))
            metric_averages = _as_mapping(summary.get("metric_averages"))
            report = _as_mapping(result.get("report"))
            cases = [
                item for item in _as_list(report.get("results")) if isinstance(item, Mapping)
            ]
            case = _as_mapping(cases[0]) if cases else {}
            metadata = _as_mapping(case.get("metadata"))
            environment_state = _as_mapping(metadata.get("environment_state"))
            state_key = str(contract["state_key"])
            required_state_keys = [
                str(item) for item in _as_list(contract.get("required_state_keys"))
            ] or [state_key]
            adapter_state = _as_mapping(environment_state.get(state_key))
            state_summary_key = str(contract.get("state_summary_key") or "")
            state_values = _as_mapping(adapter_state.get(state_summary_key))
            events = [item for item in _as_list(case.get("events")) if isinstance(item, Mapping)]
            event_types = sorted(
                {str(event.get("type") or "") for event in events if event.get("type")}
            )
            artifacts = [
                item for item in _as_list(case.get("artifacts")) if isinstance(item, Mapping)
            ]
            artifact_kinds = sorted(
                {
                    str(_as_mapping(artifact.get("metadata")).get("kind") or "")
                    for artifact in artifacts
                    if _as_mapping(artifact.get("metadata")).get("kind")
                }
            )
            coverage_metric = str(contract["coverage_metric"])
            quality_metrics = [str(metric) for metric in contract["quality_metrics"]]
            required_metrics = [coverage_metric, *quality_metrics]
            state_fields = sorted(
                {
                    *[str(key) for key in _as_mapping(contract.get("state_minimums"))],
                    *[str(key) for key in _as_mapping(contract.get("state_maximums"))],
                    *[str(key) for key in _as_mapping(contract.get("state_contains"))],
                    *[str(key) for key in _as_mapping(contract.get("state_equals"))],
                }
            )
            record = {
                "surface": surface,
                "path": relative_path,
                "result_kind": result.get("kind"),
                "result_status": result.get("status"),
                "manifest_version": manifest.get("version"),
                "agent_framework": agent.get("framework"),
                "agent_method": agent.get("method"),
                "agent_input_mode": agent.get("input_mode"),
                "trace_runtime": agent.get("trace_runtime"),
                "required_env": list(manifest.get("required_env") or []),
                "runtime_required_state_keys": list(
                    runtime_contract.get("required_state_keys") or []
                ),
                "runtime_required_tools": list(
                    runtime_contract.get("required_tools") or []
                ),
                "metric_weights": sorted(str(key) for key in metric_weights),
                "state_keys": sorted(str(key) for key in environment_state),
                "event_types": event_types,
                "artifact_kinds": artifact_kinds,
                "metrics": {
                    **{
                        metric: metric_averages.get(metric)
                        for metric in required_metrics
                    },
                    "framework_runtime_contract": metric_averages.get(
                        "framework_runtime_contract"
                    ),
                },
                "state_summary": {
                    field: _release_adapter_state_value(
                        adapter_state,
                        state_values,
                        field,
                    )
                    for field in state_fields
                },
            }
            adapters.append(record)

            expectations = {
                "result.kind": (result.get("kind"), "agent-learning.run.v1"),
                "result.status": (result.get("status"), "passed"),
                "manifest.version": (manifest.get("version"), "agent-learning.run.v1"),
                "agent.framework": (agent.get("framework"), contract["framework"]),
                "agent.method": (agent.get("method"), contract["method"]),
                "agent.input_mode": (agent.get("input_mode"), contract["input_mode"]),
                "agent.trace_runtime": (agent.get("trace_runtime"), True),
            }
            for field, (observed, expected) in expectations.items():
                if observed != expected:
                    adapter_errors.append(
                        {
                            "surface": surface,
                            "path": relative_path,
                            "field": field,
                            "expected": expected,
                            "observed": observed,
                        }
                    )
            if manifest.get("required_env") not in (None, []):
                adapter_errors.append(
                    {
                        "surface": surface,
                        "path": relative_path,
                        "field": "required_env",
                        "expected": [],
                        "observed": manifest.get("required_env"),
                    }
                )
            missing_environment_state_keys = sorted(
                set(required_state_keys) - set(str(key) for key in environment_state)
            )
            if missing_environment_state_keys:
                adapter_errors.append(
                    {
                        "surface": surface,
                        "path": relative_path,
                        "field": "environment_state",
                        "expected": required_state_keys,
                        "observed": sorted(str(key) for key in environment_state),
                        "missing": missing_environment_state_keys,
                    }
                )
            missing_runtime_state_keys = sorted(
                set(required_state_keys)
                - set(str(key) for key in runtime_contract.get("required_state_keys") or [])
            )
            if missing_runtime_state_keys:
                adapter_errors.append(
                    {
                        "surface": surface,
                        "path": relative_path,
                        "field": (
                            "evaluation.agent_report.config."
                            "framework_runtime_contract.required_state_keys"
                        ),
                        "expected": required_state_keys,
                        "observed": runtime_contract.get("required_state_keys"),
                        "missing": missing_runtime_state_keys,
                    }
                )
            missing_tools = sorted(
                set(str(tool) for tool in _as_list(contract.get("required_tools")))
                - set(str(tool) for tool in runtime_contract.get("required_tools") or [])
            )
            if missing_tools:
                adapter_errors.append(
                    {
                        "surface": surface,
                        "path": relative_path,
                        "field": (
                            "evaluation.agent_report.config."
                            "framework_runtime_contract.required_tools"
                        ),
                        "missing": missing_tools,
                    }
                )
            missing_metric_weights = sorted(
                set(required_metrics) - set(str(key) for key in metric_weights)
            )
            if missing_metric_weights:
                adapter_errors.append(
                    {
                        "surface": surface,
                        "path": relative_path,
                        "field": "evaluation.agent_report.config.metric_weights",
                        "missing": missing_metric_weights,
                    }
                )

            missing_events = sorted(set(contract["required_events"]) - set(event_types))
            if missing_events:
                event_errors.append(
                    {
                        "surface": surface,
                        "path": relative_path,
                        "required": list(contract["required_events"]),
                        "observed": event_types,
                        "missing": missing_events,
                    }
                )
            missing_artifacts = sorted(
                set(contract["required_artifact_kinds"]) - set(artifact_kinds)
            )
            if missing_artifacts:
                artifact_errors.append(
                    {
                        "surface": surface,
                        "path": relative_path,
                        "required": list(contract["required_artifact_kinds"]),
                        "observed": artifact_kinds,
                        "missing": missing_artifacts,
                    }
                )
            for metric in (*required_metrics, "framework_runtime_contract"):
                if _float_or_zero(metric_averages.get(metric)) < 1.0:
                    metric_errors.append(
                        {
                            "surface": surface,
                            "path": relative_path,
                            "metric": metric,
                            "expected": 1.0,
                            "observed": metric_averages.get(metric),
                        }
                    )
            for field, minimum in _as_mapping(contract.get("state_minimums")).items():
                observed = _release_adapter_state_value(adapter_state, state_values, field)
                if _float_or_zero(observed) < float(minimum):
                    state_errors.append(
                        {
                            "surface": surface,
                            "path": relative_path,
                            "field": f"{state_key}.{field}",
                            "expected": f">={minimum}",
                            "observed": observed,
                        }
                    )
            for field, maximum in _as_mapping(contract.get("state_maximums")).items():
                observed = _release_adapter_state_value(adapter_state, state_values, field)
                if _float_or_zero(observed) > float(maximum):
                    state_errors.append(
                        {
                            "surface": surface,
                            "path": relative_path,
                            "field": f"{state_key}.{field}",
                            "expected": f"<={maximum}",
                            "observed": observed,
                        }
                    )
            for field, required_values in _as_mapping(contract.get("state_contains")).items():
                observed = _release_adapter_state_value(adapter_state, state_values, field)
                observed_values = {str(item) for item in _as_list(observed)}
                missing_values = sorted(
                    {str(item) for item in _as_list(required_values)} - observed_values
                )
                if missing_values:
                    state_errors.append(
                        {
                            "surface": surface,
                            "path": relative_path,
                            "field": f"{state_key}.{field}",
                            "required": list(required_values),
                            "observed": sorted(observed_values),
                            "missing": missing_values,
                        }
                    )
            for field, expected in _as_mapping(contract.get("state_equals")).items():
                observed = _release_adapter_state_value(adapter_state, state_values, field)
                if observed != expected:
                    state_errors.append(
                        {
                            "surface": surface,
                            "path": relative_path,
                            "field": f"{state_key}.{field}",
                            "expected": expected,
                            "observed": observed,
                        }
                    )

    return {
        "required_files": list(required_files),
        "required_contracts": copy.deepcopy(list(contracts)),
        "missing_files": missing_files,
        "adapter_errors": adapter_errors,
        "event_errors": event_errors,
        "artifact_errors": artifact_errors,
        "metric_errors": metric_errors,
        "state_errors": state_errors,
        "errors": errors,
        "adapters": adapters,
    }


def _release_adapter_state_value(
    adapter_state: Mapping[str, Any],
    state_values: Mapping[str, Any],
    field: str,
) -> Any:
    for source in (adapter_state, state_values):
        current: Any = source
        for part in str(field).split("."):
            current_mapping = _as_mapping(current)
            if part not in current_mapping:
                current = None
                break
            current = current_mapping.get(part)
        if current is not None:
            return current
    return None


def _release_trinity_stack_probe_status(root: Path) -> dict[str, Any]:
    missing_files = _missing_relative_paths(root, V1_TRINITY_STACK_PROBE_FILES)
    optimization_errors: list[dict[str, Any]] = []
    proof_errors: list[dict[str, Any]] = []
    manifest_errors: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {}

    if not missing_files:
        example_path = root / "examples/sdk_trinity_stack_probe_optimization.py"
        try:
            spec = importlib.util.spec_from_file_location(
                "agent_learning_release_trinity_stack_probe",
                example_path,
            )
            if spec is None or spec.loader is None:
                raise RuntimeError(f"Unable to load {example_path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            from agent_learning import optimize

            with module._local_trinity_evaluation_hook() as endpoint:
                result = module.build_probe_optimization(endpoint)
                manifest = optimize.build_trinity_run_manifest_from_probe_optimization(
                    result,
                    name="release-trinity-stack-probe-readiness",
                    metadata={"release_check": "trinity_stack_probe_readiness"},
                )
        except Exception as exc:
            errors.append({"path": str(example_path.relative_to(root)), "error": str(exc)})
            result = {}
            manifest = {}

        if result:
            summary = _as_mapping(result.get("summary"))
            proof = _as_mapping(result.get("trinity_stack_probe_proof"))
            hook_probe = _as_mapping(result.get("evaluation_hook_probe"))
            hook_summary = _as_mapping(hook_probe.get("summary"))
            orchestration_result = _as_mapping(
                result.get("orchestration_stack_probe_optimization")
            )
            orchestration_proof = _as_mapping(
                orchestration_result.get("orchestration_stack_probe_proof")
            )
            evidence.update(
                {
                    "optimization_kind": result.get("kind"),
                    "optimization_status": result.get("status"),
                    "optimization_score": summary.get("optimization_score"),
                    "trinity_stack_probe_score": summary.get(
                        "trinity_stack_probe_score"
                    ),
                    "promotion_ready": summary.get("promotion_ready"),
                    "same_agent_selected": summary.get("same_agent_selected"),
                    "requires_external_service": summary.get(
                        "requires_external_service"
                    ),
                    "proof_kind": proof.get("kind"),
                    "proof_status": proof.get("status"),
                    "proof_failed_check_ids": proof.get("failed_check_ids") or [],
                    "orchestration_stack_probe_proof_status": orchestration_proof.get(
                        "status"
                    ),
                    "evaluation_hook_probe_status": hook_probe.get("status"),
                    "evaluation_hook_trace_count": hook_summary.get(
                        "hook_trace_count"
                    ),
                    "evaluation_hook_success_trace_count": hook_summary.get(
                        "hook_success_trace_count"
                    ),
                    "evaluation_hook_metric_count": hook_summary.get(
                        "hook_metric_count"
                    ),
                    "evaluation_hook_score": hook_summary.get("hook_score"),
                    "evaluation_hook_auth_redacted": hook_summary.get(
                        "auth_redacted"
                    ),
                    "evaluation_hook_local_executable_fixture": hook_summary.get(
                        "local_executable_fixture"
                    ),
                }
            )
            if result.get("kind") != "agent-learning.optimization.v1":
                optimization_errors.append(
                    {
                        "field": "kind",
                        "expected": "agent-learning.optimization.v1",
                        "observed": result.get("kind"),
                    }
                )
            if result.get("status") != "passed":
                optimization_errors.append(
                    {
                        "field": "status",
                        "expected": "passed",
                        "observed": result.get("status"),
                    }
                )
            if summary.get("promotion_ready") is not True:
                optimization_errors.append(
                    {
                        "field": "summary.promotion_ready",
                        "expected": True,
                        "observed": summary.get("promotion_ready"),
                    }
                )
            if summary.get("same_agent_selected") is not True:
                optimization_errors.append(
                    {
                        "field": "summary.same_agent_selected",
                        "expected": True,
                        "observed": summary.get("same_agent_selected"),
                    }
                )
            if summary.get("requires_external_service") is not False:
                optimization_errors.append(
                    {
                        "field": "summary.requires_external_service",
                        "expected": False,
                        "observed": summary.get("requires_external_service"),
                    }
                )
            if proof.get("kind") != V1_TRINITY_STACK_PROBE_PROOF_KIND:
                proof_errors.append(
                    {
                        "field": "kind",
                        "expected": V1_TRINITY_STACK_PROBE_PROOF_KIND,
                        "observed": proof.get("kind"),
                    }
                )
            if proof.get("status") != "passed" or proof.get("passed") is not True:
                proof_errors.append(
                    {
                        "field": "status",
                        "expected": "passed",
                        "observed": proof.get("status"),
                    }
                )
            if proof.get("failed_check_ids"):
                proof_errors.append(
                    {
                        "field": "failed_check_ids",
                        "expected": [],
                        "observed": proof.get("failed_check_ids"),
                    }
                )
            if orchestration_proof.get("status") != "passed":
                proof_errors.append(
                    {
                        "field": "orchestration_stack_probe_proof.status",
                        "expected": "passed",
                        "observed": orchestration_proof.get("status"),
                    }
                )
            if hook_probe.get("status") != "passed":
                proof_errors.append(
                    {
                        "field": "evaluation_hook_probe.status",
                        "expected": "passed",
                        "observed": hook_probe.get("status"),
                    }
                )
            if _int_or_zero(hook_summary.get("hook_trace_count")) < 1:
                proof_errors.append(
                    {
                        "field": "evaluation_hook_probe.summary.hook_trace_count",
                        "expected": ">=1",
                        "observed": hook_summary.get("hook_trace_count"),
                    }
                )
            if _int_or_zero(hook_summary.get("hook_metric_count")) < 1:
                proof_errors.append(
                    {
                        "field": "evaluation_hook_probe.summary.hook_metric_count",
                        "expected": ">=1",
                        "observed": hook_summary.get("hook_metric_count"),
                    }
                )
            if hook_summary.get("auth_redacted") is not True:
                proof_errors.append(
                    {
                        "field": "evaluation_hook_probe.summary.auth_redacted",
                        "expected": True,
                        "observed": hook_summary.get("auth_redacted"),
                    }
                )

        if manifest:
            env_types = [
                str(item.get("type") or "")
                for item in _as_list(
                    _as_mapping(manifest.get("simulation")).get("environments")
                )
                if isinstance(item, Mapping)
            ]
            metadata = _as_mapping(manifest.get("metadata"))
            eval_config = _as_mapping(
                _as_mapping(manifest.get("evaluation")).get("agent_report")
            )
            hooks = _as_list(_as_mapping(eval_config.get("config")).get("evaluation_hooks"))
            evidence.update(
                {
                    "manifest_version": manifest.get("version"),
                    "manifest_status": manifest.get("status"),
                    "manifest_required_env": manifest.get("required_env") or [],
                    "manifest_environment_types": env_types,
                    "manifest_promoted_from_trinity_stack_probe": metadata.get(
                        "promoted_from_trinity_stack_probe"
                    ),
                    "manifest_trinity_stack_probe_proof_status": metadata.get(
                        "trinity_stack_probe_proof_status"
                    ),
                    "manifest_evaluation_hook_count": len(hooks),
                }
            )
            if manifest.get("version") != "agent-learning.run.v1":
                manifest_errors.append(
                    {
                        "field": "version",
                        "expected": "agent-learning.run.v1",
                        "observed": manifest.get("version"),
                    }
                )
            missing_env_types = sorted(
                set(V1_TRINITY_STACK_PROBE_REQUIRED_ENVIRONMENT_TYPES)
                - set(env_types)
            )
            if missing_env_types:
                manifest_errors.append(
                    {
                        "field": "simulation.environments",
                        "expected": list(
                            V1_TRINITY_STACK_PROBE_REQUIRED_ENVIRONMENT_TYPES
                        ),
                        "missing": missing_env_types,
                    }
                )
            if metadata.get("promoted_from_trinity_stack_probe") is not True:
                manifest_errors.append(
                    {
                        "field": "metadata.promoted_from_trinity_stack_probe",
                        "expected": True,
                        "observed": metadata.get("promoted_from_trinity_stack_probe"),
                    }
                )
            if metadata.get("trinity_stack_probe_proof_status") != "passed":
                manifest_errors.append(
                    {
                        "field": "metadata.trinity_stack_probe_proof_status",
                        "expected": "passed",
                        "observed": metadata.get(
                            "trinity_stack_probe_proof_status"
                        ),
                    }
                )
            if manifest.get("required_env") not in (None, []):
                manifest_errors.append(
                    {
                        "field": "required_env",
                        "expected": [],
                        "observed": manifest.get("required_env"),
                    }
                )
            if not hooks:
                manifest_errors.append(
                    {
                        "field": "evaluation.agent_report.config.evaluation_hooks",
                        "expected": "non-empty",
                        "observed": len(hooks),
                    }
                )

    return {
        "required_files": list(V1_TRINITY_STACK_PROBE_FILES),
        "required_environment_types": list(
            V1_TRINITY_STACK_PROBE_REQUIRED_ENVIRONMENT_TYPES
        ),
        "required_proof_kind": V1_TRINITY_STACK_PROBE_PROOF_KIND,
        "missing_files": missing_files,
        "optimization_errors": optimization_errors,
        "proof_errors": proof_errors,
        "manifest_errors": manifest_errors,
        "errors": errors,
        "evidence": evidence,
    }


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple | set):
        return list(value)
    return [value]


def _int_or_zero(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return 0
    return 0


def _float_or_zero(value: Any) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return 0.0
    return 0.0


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
    "V1_TRINITY_STACK_PROBE_FILES",
    "V1_TRINITY_STACK_PROBE_PROOF_KIND",
    "V1_TRINITY_STACK_PROBE_REQUIRED_ENVIRONMENT_TYPES",
    "V1_FRAMEWORK_PROVIDER_EXAMPLES",
    "V1_FRAMEWORK_PROVIDER_FRAMEWORKS",
    "V1_FRAMEWORK_PROVIDER_MANIFEST_CONTRACTS",
    "V1_FRAMEWORK_PROVIDER_REQUIRED_MODALITIES",
    "V1_FRAMEWORK_PROVIDER_REQUIRED_TARGET_SCHEMES",
    "V1_FRAMEWORK_PROVIDER_REQUIRED_TRANSPORTS",
    "V1_BROWSER_REALTIME_ADAPTER_CONTRACTS",
    "V1_BROWSER_REALTIME_ADAPTER_FILES",
    "V1_FRAMEWORK_OPTIMIZER_CONTRACTS",
    "V1_FRAMEWORK_OPTIMIZER_FILES",
    "V1_STATEFUL_FRAMEWORK_ADAPTER_CONTRACTS",
    "V1_STATEFUL_FRAMEWORK_ADAPTER_FILES",
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
