"""Every Wave A vertical adapts a customer agent that shares none of its names, and Banking adapts
CardGuard: proved on PostgreSQL through `python -m fi.alk.harness.adaptation run`, with every
trajectory derived from the reference solutions.

Needs sealed releases and a PostgreSQL server, so it runs only when both are named:

    ALK_ADAPTATION_RELEASES=<dir with <vertical>/environment and <vertical>/suite> \\
    ALK_RELEASE_POSTGRES_DSN=postgresql://... pytest tests/harness/test_adaptation_verticals.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adaptation_fixtures as fixtures

from fi.alk.harness.adaptation.__main__ import main

VERTICALS = (
    "banking",
    "retail",
    "hr",
    "internal_it",
    "saas_subscriptions",
    "clinic_scheduling",
    "service_scheduling",
    "sales_intake",
    "internal_knowledge",
)
RELEASES = os.environ.get("ALK_ADAPTATION_RELEASES", "")
DSN = os.environ.get("ALK_RELEASE_POSTGRES_DSN", "")
needs_releases = pytest.mark.skipif(
    not (RELEASES and DSN),
    reason="set ALK_ADAPTATION_RELEASES and ALK_RELEASE_POSTGRES_DSN",
)


def _adapt(
    tmp_path: Path, vertical: str, profile: dict, mapping: dict, **drafts
) -> dict:
    release = Path(RELEASES) / vertical
    profile_path, drafts_path = fixtures.write_drafts(
        tmp_path / "inputs", profile, mapping, **drafts
    )
    code = main(
        [
            "run",
            "--environment",
            str(release / "environment"),
            "--suite",
            str(release / "suite"),
            "--profile",
            str(profile_path),
            "--drafts",
            str(drafts_path),
            "--output",
            str(tmp_path / "out"),
            "--postgres-dsn",
            DSN,
            "--approve",
        ]
    )
    result = json.loads((tmp_path / "out" / "result.json").read_text(encoding="utf-8"))
    assert code == 0, result.get("failure")
    return result


def _counts(tmp_path: Path) -> dict[str, int]:
    proof = json.loads(
        (tmp_path / "out" / "binding-proof.json").read_text(encoding="utf-8")
    )
    counts: dict[str, int] = {}
    for scenario in proof["scenarios"].values():
        counts[scenario["status"]] = counts.get(scenario["status"], 0) + 1
    assert proof["passed"], counts
    return counts


@needs_releases
@pytest.mark.parametrize("vertical", VERTICALS)
def test_a_renamed_agent_is_proved_on_every_scenario(tmp_path, vertical):
    """An agent with none of the environment's names reaches all of it, so every scenario is
    proved: the harness derived each trajectory, and every adversarial variant failed."""
    profile, mapping = fixtures.renamed_agent(Path(RELEASES) / vertical / "environment")
    result = _adapt(tmp_path, vertical, profile, mapping)
    assert result["status"] == "completed"
    assert result["binding"]["mode"] == "tool_routed"
    assert result["suite"]["derived"] is False
    counts = _counts(tmp_path)
    assert counts == {"proved": sum(counts.values())}, counts
    assert result["alignment"]["unsupported"] == []
    assert result["alignment"]["unscorable"] == []


@needs_releases
def test_cardguard_is_adapted_to_banking(tmp_path):
    """Banking's customer: own names for the bank's tools, the official Gmail and Drive tools
    mapped onto themselves, one tool the world cannot serve, two instructions against the rules."""
    profile, mapping = fixtures.cardguard(Path(RELEASES) / "banking" / "environment")
    result = _adapt(
        tmp_path,
        "banking",
        profile,
        mapping,
        conflicts=fixtures.CARDGUARD_CONFLICTS,
        extension_candidates=[
            {
                "agent_tool": "send_sms_alert",
                "reason": fixtures.CARDGUARD_UNSUPPORTED["send_sms_alert"][1],
            }
        ],
    )
    assert result["status"] == "completed"
    alignment = result["alignment"]
    assert alignment["mode"] == "tool_routed"
    assert {one["agent_tool"] for one in alignment["unsupported"]} == {"send_sms_alert"}
    assert {one["agent_tool"] for one in alignment["extension_candidates"]} == {
        "send_sms_alert"
    }
    assert {one["rule"] for one in alignment["conflicts"]} == {
        "confirm_before_lock",
        "explain_pending_policy",
    }
    mapped = {one["agent_tool"]: one["steps"] for one in alignment["mapped"]}
    assert mapped["block_card"] == ["lock_card"] and mapped["get_thread"] == [
        "get_thread"
    ]
    counts = _counts(tmp_path)
    assert counts == {"proved": sum(counts.values())}, counts
    trajectories = json.loads(
        (tmp_path / "out" / "trajectories.json").read_text(encoding="utf-8")
    )
    assert len(trajectories) == counts["proved"]
    # The agent's calls carry its own names and argument names, never the environment's.
    names = {call["tool"] for calls in trajectories.values() for call in calls}
    assert "lock_card" not in names and "block_card" in names
    arguments = {
        key
        for calls in trajectories.values()
        for call in calls
        for key in call["arguments"]
    }
    assert "customer_id" not in arguments and "customer" in arguments
