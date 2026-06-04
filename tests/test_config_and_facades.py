from __future__ import annotations

import json
import os

from agent_learning import configure, current_config, get_api_key
from agent_learning.cli import main


def test_configure_sets_unified_key_environment(monkeypatch):
    for key in (
        "AGENT_LEARNING_API_KEY",
        "FUTURE_AGI_API_KEY",
        "FI_API_KEY",
        "AGENT_LEARNING_PROJECT_ID",
        "FUTURE_AGI_PROJECT_ID",
    ):
        monkeypatch.delenv(key, raising=False)

    config = configure(
        api_key="real-local-agent-learning-key",
        project_id="project_123",
    )

    assert config.api_key == "real-local-agent-learning-key"
    assert current_config().project_id == "project_123"
    assert get_api_key(required=True) == "real-local-agent-learning-key"
    assert os.environ["AGENT_LEARNING_API_KEY"] == "real-local-agent-learning-key"
    assert os.environ["FUTURE_AGI_API_KEY"] == "real-local-agent-learning-key"
    assert os.environ["FI_API_KEY"] == "real-local-agent-learning-key"


def test_facades_expose_unified_agent_learning_modules():
    from agent_learning import evals, optimize, simulate

    assert simulate.run_eval_suite_file is not None
    assert optimize.OptimizationTarget is not None
    assert optimize.optimize_eval_suite_file is not None
    assert evals.evaluate is not None


def test_agent_learn_eval_delegates_to_simulate_cli(tmp_path):
    suite_path = tmp_path / "suite.json"
    output_path = tmp_path / "result.json"
    suite_path.write_text(
        json.dumps(
            {
                "version": "agent-simulate.eval.v1",
                "name": "agent-learning-kit-eval",
                "providers": [{"id": "echo", "type": "echo"}],
                "prompts": [{"id": "support", "template": "{{question}}"}],
                "tests": [
                    {
                        "id": "policy",
                        "vars": {"question": "Where is the policy?"},
                        "assert": [{"type": "contains", "value": "policy"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["eval", str(suite_path), "--output", str(output_path)])

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["status"] == "passed"
    assert payload["kind"] == "agent-simulate.eval.v1"


def test_agent_learn_optimize_eval_runs_unified_command_and_writes_artifacts(tmp_path):
    suite_path = tmp_path / "suite.json"
    output_path = tmp_path / "optimization.json"
    junit_path = tmp_path / "optimization.junit.xml"
    sarif_path = tmp_path / "optimization.sarif.json"
    markdown_path = tmp_path / "optimization.md"
    failing_response = "Private credentials only."
    passing_response = "Policy answer with policy and no leaked secrets."
    suite_path.write_text(
        json.dumps(
            {
                "version": "agent-simulate.eval.v1",
                "name": "agent-learning-kit-eval-optimization",
                "providers": [
                    {"id": "scripted", "type": "scripted", "response": failing_response}
                ],
                "prompts": [{"id": "support", "template": "{{question}}"}],
                "tests": [
                    {
                        "id": "policy",
                        "vars": {"question": "Where is the policy?"},
                        "assert": [
                            {"type": "contains", "value": "policy"},
                            {
                                "type": "not_contains",
                                "value": "private credentials",
                            },
                        ],
                    }
                ],
                "optimization": {
                    "threshold": 1.0,
                    "target": {
                        "name": "agent-learning-provider-response",
                        "layers": ["prompt", "evaluator"],
                        "base_config": {
                            "providers": [{"response": failing_response}]
                        },
                        "search_space": {
                            "providers.0.response": [
                                failing_response,
                                passing_response,
                            ]
                        },
                    },
                    "optimizer": {
                        "max_candidates": 2,
                        "include_seed": True,
                        "auto_diagnose": False,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    exit_code = main([
        "optimize-eval",
        str(suite_path),
        "--output",
        str(output_path),
        "--junit",
        str(junit_path),
        "--sarif",
        str(sarif_path),
        "--markdown",
        str(markdown_path),
    ])

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["kind"] == "agent-learning.eval-optimization.v1"
    assert payload["status"] == "passed"
    assert payload["optimization"]["best_config"]["providers"][0]["response"] == passing_response
    assert "failures=\"0\"" in junit_path.read_text(encoding="utf-8")
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    assert sarif["version"] == "2.1.0"
    assert all(result["level"] != "error" for result in sarif["runs"][0]["results"])
    assert "## Optimization" in markdown_path.read_text(encoding="utf-8")


def test_agent_learn_doctor_reports_module_availability(capsys):
    exit_code = main(["doctor"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["modules"]["simulate"]["available"] is True
    assert payload["modules"]["evaluation"]["available"] is True
    assert payload["modules"]["optimize"]["available"] is True
