from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REFERENCES = ROOT / "examples" / "harness" / "multi_agent_compatibility"


def run_verifier(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REFERENCES / "verify_references.py"), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def test_multi_agent_reference_contracts_do_not_drift() -> None:
    result = run_verifier()
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "3 multi-agent references verified"


def test_google_reference_keeps_the_full_travel_concierge_topology() -> None:
    reference = json.loads(
        (REFERENCES / "google_adk" / "reference.json").read_text(encoding="utf-8")
    )
    topology = reference["topology"]
    workers = topology["workers"]
    top_level = {
        worker["id"]
        for worker in workers
        if worker["parent"] == topology["coordinator"]
    }
    assert top_level == {
        "inspiration_agent",
        "planning_agent",
        "booking_agent",
        "pre_trip_agent",
        "in_trip_agent",
        "post_trip_agent",
    }
    assert len(workers) == 20


def test_crewai_reference_keeps_the_native_self_evaluation_workers() -> None:
    reference = json.loads(
        (REFERENCES / "crewai" / "reference.json").read_text(encoding="utf-8")
    )
    assert reference["topology"] == {
        "coordinator": "ShakespeareXPostFlow",
        "workers": [
            {"id": "shakespearean_bard", "parent": "ShakespeareXPostFlow"},
            {"id": "x_post_verifier", "parent": "ShakespeareXPostFlow"},
        ],
    }


def test_langgraph_reference_keeps_both_async_workers() -> None:
    reference = json.loads(
        (REFERENCES / "langgraph" / "reference.json").read_text(encoding="utf-8")
    )
    assert reference["topology"] == {
        "coordinator": "agent",
        "workers": [
            {"id": "researcher", "parent": "agent"},
            {"id": "coder", "parent": "agent"},
        ],
    }


def test_verifier_checks_materialized_orchestration(tmp_path: Path) -> None:
    source = tmp_path / "travel-concierge"
    agent = source / "travel_concierge" / "agent.py"
    agent.parent.mkdir(parents=True)
    agent.write_text(
        "\n".join(
            [
                "sub_agents=[",
                "inspiration_agent,",
                "planning_agent,",
                "booking_agent,",
                "pre_trip_agent,",
                "in_trip_agent,",
                "post_trip_agent,",
            ]
        ),
        encoding="utf-8",
    )
    result = run_verifier(f"--source=google-adk-travel-concierge={source}")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (
        "3 multi-agent references and 1 source trees verified"
    )
