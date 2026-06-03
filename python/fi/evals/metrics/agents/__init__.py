"""
Agent Evaluation Metrics.

Trajectory-based evaluation of AI agent performance.
Provides multi-step analysis beyond single-response evaluation.

Based on:
- AgentBench methodology (ICLR 2024)
- Multi-turn agent evaluation frameworks
"""

from .types import (
    AgentTrajectoryInput,
    AgentStep,
    ToolCall,
    TaskDefinition,
    TrajectoryAnalysis,
)
from .metrics import (
    TaskCompletion,
    StepEfficiency,
    ToolSelectionAccuracy,
    TrajectoryScore,
    GoalProgress,
    ActionSafety,
    ReasoningQuality,
)
from .report import (
    AgentReportEvalConfig,
    AgentReportMetricResult,
    AgentReportCaseResult,
    AgentReportEvaluation,
    AgentReportEvaluator,
    evaluate_agent_report,
    diff_domain_package_registries,
    normalize_agent_report,
    replay_domain_package_registry,
    validate_domain_package_registry,
)

__all__ = [
    # Types
    "AgentTrajectoryInput",
    "AgentStep",
    "ToolCall",
    "TaskDefinition",
    "TrajectoryAnalysis",
    "AgentReportEvalConfig",
    "AgentReportMetricResult",
    "AgentReportCaseResult",
    "AgentReportEvaluation",
    # Metrics
    "TaskCompletion",
    "StepEfficiency",
    "ToolSelectionAccuracy",
    "TrajectoryScore",
    "GoalProgress",
    "ActionSafety",
    "ReasoningQuality",
    "AgentReportEvaluator",
    "evaluate_agent_report",
    "diff_domain_package_registries",
    "normalize_agent_report",
    "replay_domain_package_registry",
    "validate_domain_package_registry",
]
