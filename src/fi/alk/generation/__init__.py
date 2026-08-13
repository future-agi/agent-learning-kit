"""Local-first scenario generation: point at an agent, get reviewed, checkable test scenarios."""

from .contract import AgentContract, ToolSpec, extract_contract, validate_contract
from .emit import smoke_manifest, to_alk_scenario, write_outputs
from .llm import (
    AuthFailed,
    BudgetExceeded,
    FakeLLMClient,
    LiteLLMClient,
    LLMClient,
    Usage,
)
from .pipeline import GenerationConfig, GenerationResult, generate
from .sources import (
    AgentEvidence,
    AgentSource,
    RepoFolderSource,
    register_source,
    resolve_source,
    source_registry,
)
from .validators import banned_tokens, repair_hint, validate_scenario

__all__ = [
    "AgentContract",
    "AgentEvidence",
    "AgentSource",
    "AuthFailed",
    "BudgetExceeded",
    "FakeLLMClient",
    "GenerationConfig",
    "GenerationResult",
    "LLMClient",
    "LiteLLMClient",
    "RepoFolderSource",
    "ToolSpec",
    "Usage",
    "banned_tokens",
    "extract_contract",
    "generate",
    "register_source",
    "repair_hint",
    "resolve_source",
    "smoke_manifest",
    "source_registry",
    "to_alk_scenario",
    "validate_contract",
    "validate_scenario",
    "write_outputs",
]
