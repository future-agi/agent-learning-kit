from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_learning.cli import main


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = PROJECT_ROOT / "examples"


@pytest.mark.parametrize(
    ("command", "example", "kind", "required_env"),
    [
        (
            "run",
            "run_manifest.json",
            "agent-learning.run.v1",
            "AGENT_LEARNING_RUN_EXAMPLE_KEY",
        ),
        ("eval", "eval_suite.json", "agent-learning.eval.v1", None),
        (
            "redteam",
            "redteam_manifest.json",
            "agent-learning.redteam.v1",
            "AGENT_LEARNING_REDTEAM_EXAMPLE_KEY",
        ),
        (
            "optimize",
            "optimization_manifest.json",
            "agent-learning.optimization.v1",
            "AGENT_LEARNING_OPTIMIZE_EXAMPLE_KEY",
        ),
        (
            "optimize-eval",
            "eval_suite_optimization.json",
            "agent-learning.eval-optimization.v1",
            None,
        ),
    ],
)
def test_shipped_examples_execute_through_unified_cli(
    command: str,
    example: str,
    kind: str,
    required_env: str | None,
    tmp_path,
    monkeypatch,
):
    if required_env:
        monkeypatch.setenv(required_env, f"real-local-{required_env.lower()}")

    output_path = tmp_path / f"{command}.json"
    junit_path = tmp_path / f"{command}.junit.xml"
    sarif_path = tmp_path / f"{command}.sarif.json"
    markdown_path = tmp_path / f"{command}.md"

    args = [
        command,
        str(EXAMPLES / example),
        "--output",
        str(output_path),
        "--junit",
        str(junit_path),
        "--sarif",
        str(sarif_path),
        "--markdown",
        str(markdown_path),
    ]
    if command == "run":
        args.append("--no-eval")

    exit_code = main(args)

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["kind"] == kind
    assert payload["status"] == "passed"
    assert payload["exit_code"] == 0
    assert "failures=\"0\"" in junit_path.read_text(encoding="utf-8")
    assert json.loads(sarif_path.read_text(encoding="utf-8"))["version"] == "2.1.0"
    assert markdown_path.read_text(encoding="utf-8").strip()

    if command == "optimize":
        assert payload["summary"]["optimization_score"] >= 0.9
        assert payload["optimization"]["best_config"]
    if command == "optimize-eval":
        assert payload["summary"]["optimization_score"] == pytest.approx(1.0)
        assert payload["optimization"]["best_config"]
    if command in {"run", "eval", "redteam"}:
        assert payload["summary"]["case_count"] >= 1
