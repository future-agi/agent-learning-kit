from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from agent_learning import configure, optimize


REQUIRED_ENV = "AGENT_LEARNING_SDK_REDTEAM_READINESS_CERTIFICATION_KEY"
EXAMPLE_DIR = Path(__file__).resolve().parent


def _targets() -> list[dict[str, Any]]:
    return [
        {
            "id": "langgraph_factory",
            "framework": "langgraph",
            "module": "framework_shims",
            "attribute": "build_langgraph_agent",
            "callable": True,
            "invoke": True,
            "signals": ["factory", "workspace", "shim"],
        },
        {
            "id": "pipecat_factory",
            "framework": "pipecat",
            "module": "framework_shims",
            "attribute": "build_pipecat_pipeline",
            "callable": True,
            "invoke": True,
            "signals": ["factory", "voice", "workspace", "shim"],
        },
    ]


def build_manifest() -> dict[str, Any]:
    if str(EXAMPLE_DIR) not in sys.path:
        sys.path.insert(0, str(EXAMPLE_DIR))

    return optimize.build_redteam_readiness_certification_optimization_manifest(
        name="sdk-redteam-readiness-certification-optimization",
        workspace_path=EXAMPLE_DIR,
        required_env=[REQUIRED_ENV],
        repository_url="https://github.com/future-agi/agent-learning-kit",
        commit_sha="local-example-worktree",
        framework="langgraph",
        targets=_targets(),
        target={
            "name": "local-redteam-readiness-agent",
            "provider": "futureagi",
            "repository": "examples/framework_shims.py",
            "modalities": ["chat", "voice", "tool", "memory"],
        },
        adapter={
            "name": "redteam-readiness-certification-adapter",
            "version": "2026-06",
            "runtime": "python",
        },
        required_frameworks=["langgraph", "pipecat"],
        required_export_types=["probe_suite"],
        required_signals=[
            "framework_import",
            "runtime_import",
            "python_import",
            "module_import",
            "callable",
            "runtime_call",
            "target",
            "adapter",
            "observability",
            "artifact",
        ],
        attack_types=["prompt_injection", "credential_exfiltration"],
        surfaces=["tool", "memory"],
        channels=["chat"],
        providers=["local_cli"],
        target_metadata={"cookbook": "sdk-redteam-readiness-certification"},
    )


def run(output_path: str | Path | None = None) -> dict[str, Any]:
    api_key = os.environ.get(REQUIRED_ENV)
    if not api_key:
        raise RuntimeError(f"Set {REQUIRED_ENV} before running this example.")
    configure(api_key=api_key)

    manifest = build_manifest()
    manifest_path = (
        Path(output_path).expanduser().with_suffix(".manifest.json")
        if output_path is not None
        else Path(__file__).with_suffix(".json")
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    result = optimize.optimize_manifest(
        manifest,
        manifest_path=manifest_path,
    )
    if output_path is not None:
        path = Path(output_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(result, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
    return result


if __name__ == "__main__":
    destination = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    payload = run(destination)
    if destination is None:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
