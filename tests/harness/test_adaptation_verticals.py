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
    tmp_path: Path,
    vertical: str,
    profile: dict,
    mapping: dict,
    *,
    trajectories: dict | None = None,
    **drafts,
) -> dict:
    release = Path(RELEASES) / vertical
    profile_path, drafts_path = fixtures.write_drafts(
        tmp_path / "inputs", profile, mapping, **drafts
    )
    if trajectories:
        body = json.loads(drafts_path.read_text(encoding="utf-8"))
        drafts_path.write_text(
            json.dumps({**body, "trajectories": trajectories}), encoding="utf-8"
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


# Internal Knowledge's gap_noticed_flag_declined reads the Acceptable Use Policy whole through
# Drive's `read_file_content`; fetching its two passages with the environment's own `fetch_passage`
# is a valid route to the same outcome (`test_internal_knowledge_validation`, ALTERNATES).
ROUTED = "gap-noticed-flag-declined"


def _mine(tool: str, **arguments: object) -> dict:
    return {
        "tool": f"my_{tool}",
        "arguments": {f"my_{name}": value for name, value in arguments.items()},
    }


def _run_manifest(tmp_path: Path, binding: dict, keys: list[str]) -> Path:
    from fi.alk.harness.releases import RunManifestV1, object_digest

    release = Path(RELEASES) / "internal_knowledge"
    environment = json.loads(
        (release / "environment" / "release.json").read_text(encoding="utf-8")
    )
    suite = json.loads((release / "suite" / "suite.json").read_text(encoding="utf-8"))
    body = {
        "schema_version": "futureagi.run-manifest.v1",
        "digest": "sha256:" + "0" * 64,
        "environment": [{"digest": environment["digest"], "name": environment["name"]}],
        "suite": {"digest": suite["digest"]},
        "binding": {"digest": binding["digest"]},
        "agent": binding["agent"],
        "selection": {"scenario_keys": keys, "trials": 1},
        "simulator": {"llm": {"provider": "openai", "model": "gpt-test"}},
        "scorer": {"alk_version": "test"},
        "runtime": {
            "provider": "test",
            "image_name": "alk",
            "image_digest": "sha256:" + "1" * 64,
        },
    }
    manifest = RunManifestV1.model_validate(body)
    manifest = manifest.model_copy(update={"digest": object_digest(manifest)})
    path = tmp_path / f"manifest-{len(keys)}-{keys[0]}.json"
    path.write_text(json.dumps(manifest.model_dump(mode="json")), encoding="utf-8")
    return path


@needs_releases
def test_an_agent_without_a_tool_is_proved_on_another_route_and_runs_only_its_subset(
    tmp_path,
):
    """An Internal Knowledge agent without Drive's `read_file_content`: gap_noticed_flag_declined's
    reference needs it, but the builder's trajectory through `fetch_passage` passes the same
    outcome and adversarial controls, so the scenario is proved, not unscorable. The scenarios no
    route and no trajectory reaches are unscorable, and compose refuses to run them and leaves
    them out of the bundle."""
    import shutil

    from fi.alk.harness.job import HarnessJob
    from fi.alk.harness.releases import (
        ReleaseError,
        compose_bundle,
        environment_directory,
    )

    release = Path(RELEASES) / "internal_knowledge"
    profile, mapping = fixtures.renamed_agent(release / "environment")
    profile["tools"] = [
        tool for tool in profile["tools"] if tool["name"] != "my_read_file_content"
    ]
    mapping["tools"] = [
        entry
        for entry in mapping["tools"]
        if entry["agent_tool"] != "my_read_file_content"
    ]
    result = _adapt(
        tmp_path,
        "internal_knowledge",
        profile,
        mapping,
        trajectories={
            ROUTED: [
                _mine("get_user_profile", employee_id="E13"),
                _mine(
                    "get_effective_version", employee_id="E13", family="acceptable_use"
                ),
                _mine("fetch_passage", employee_id="E13", passage_id="P-AUPV2-01"),
                _mine("fetch_passage", employee_id="E13", passage_id="P-AUPV2-02"),
                _mine(
                    "search_documents",
                    employee_id="E13",
                    query="client data system",
                    include_superseded=False,
                    collection_id="C_IT",
                ),
                _mine("get_document_owner", employee_id="E13", drive_file_id="aup_v2"),
            ]
        },
    )
    out = tmp_path / "out"
    proof = json.loads((out / "binding-proof.json").read_text(encoding="utf-8"))
    assert proof["scenarios"][ROUTED]["status"] == "proved", proof["scenarios"][ROUTED]
    unscorable = {one["scenario_key"] for one in result["alignment"]["unscorable"]}
    assert unscorable and ROUTED not in unscorable
    assert unscorable == {
        key for key, one in proof["scenarios"].items() if one["status"] == "unscorable"
    }

    releases = tmp_path / "releases"
    environment = json.loads(
        (release / "environment" / "release.json").read_text(encoding="utf-8")
    )
    shutil.copytree(
        release / "environment", environment_directory(releases, environment["digest"])
    )
    shutil.copytree(release / "suite", releases / "suite")
    binding = json.loads((out / "binding.json").read_text(encoding="utf-8"))
    job = tmp_path / "job.json"
    job.write_text(
        HarnessJob(
            job_id="subset",
            run_id="subset-run",
            execution="local",
            source={"kind": "local_repository", "local_path": str(tmp_path)},
            agent={
                "connector": "mcp_client",
                "config": {
                    key: value for key, value in profile.items() if key != "provider"
                },
            },
            scenario_count=1,
        ).model_dump_json(),
        encoding="utf-8",
    )
    common = {
        "binding_path": out / "binding.json",
        "job_path": job,
        "binding_proof_path": out / "binding-proof.json",
    }
    picked = min(unscorable)
    with pytest.raises(ReleaseError) as caught:
        compose_bundle(
            releases,
            manifest_path=_run_manifest(tmp_path, binding, [ROUTED, picked]),
            output=tmp_path / "refused",
            **common,
        )
    assert caught.value.code == "scenario_unscorable" and picked in caught.value.detail
    with pytest.raises(ReleaseError) as caught:
        compose_bundle(
            releases,
            manifest_path=_run_manifest(tmp_path, binding, [ROUTED]),
            output=tmp_path / "unproved",
            **{**common, "binding_proof_path": None},
        )
    assert caught.value.code == "binding_unproved"

    bundle = compose_bundle(
        releases,
        manifest_path=_run_manifest(tmp_path, binding, [ROUTED]),
        output=tmp_path / "bundle",
        **common,
    )
    proved = sorted(
        key for key, one in proof["scenarios"].items() if one["status"] == "proved"
    )
    assert bundle.metadata["release"]["scenarios"] == proved
    carried = {
        json.loads((folder / "scenario.json").read_text(encoding="utf-8"))[
            "scenario_key"
        ]
        for folder in (tmp_path / "bundle" / "scenarios").iterdir()
        if (folder / "scenario.json").is_file()
    }
    assert carried == set(proved)
