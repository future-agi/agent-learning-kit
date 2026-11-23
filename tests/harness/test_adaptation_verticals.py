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
needs_release_files = pytest.mark.skipif(
    not RELEASES, reason="set ALK_ADAPTATION_RELEASES"
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


@needs_release_files
def test_an_explicit_agent_id_is_the_one_compose_finds(tmp_path):
    """Job -> binding -> compose with a customer id that is not the name's slug: the binding is
    sealed for `bank-support-42`, and compose accepts the run's own declaration as that agent."""
    from fi.alk.harness.adaptation.declared import MCP_CLIENT
    from fi.alk.harness.adaptation.profile import profile_from_job, seal_binding
    from fi.alk.harness.binding_proof import BindingProofV1
    from fi.alk.harness.job import HarnessJob
    from fi.alk.harness.releases import (
        RELEASES_SUITE,
        BindingMode,
        compose_bundle,
        environment_directory,
        load_environment_release,
        load_suite_release,
        seal_run_manifest,
    )

    release = Path(RELEASES) / "banking"
    profile, mapping = fixtures.declared_agent(
        release / "environment",
        name="CardGuard",
        instructions=fixtures.CARDGUARD_INSTRUCTIONS,
        renames=fixtures.CARDGUARD_RENAMES,
        unsupported=fixtures.CARDGUARD_UNSUPPORTED,
        agent_id="bank-support-42",
    )
    config = {key: value for key, value in profile.items() if key != "provider"}
    digest = "sha256:" + "ab" * 32
    job = HarnessJob(
        job_id="job-cardguard",
        run_id="run-cardguard",
        execution="hosted",
        runtime={"isolation": "dedicated_vm"},
        source={"kind": "archive", "archive_artifact_id": "source-id"},
        agent={"connector": MCP_CLIENT, "config": config},
        metadata={
            "adaptation": {
                "agent": {
                    "agent_definition_id": "cardguard",
                    "agent_version_id": "cardguard-v3",
                    "configuration_digest": digest,
                    "agent_config_digest": digest,
                    "connector": MCP_CLIENT,
                }
            }
        },
    )
    adapted = profile_from_job(job, credentials={})
    binding = seal_binding(
        adapted,
        BindingMode.TOOL_ROUTED,
        mapping,
        environment_root=release / "environment",
    )
    assert binding.agent.provider_target_id == "bank-support-42"

    releases = tmp_path / "releases"
    environment = load_environment_release(release / "environment")
    environment_directory(releases, environment.digest).parent.mkdir(parents=True)
    environment_directory(releases, environment.digest).symlink_to(
        release / "environment"
    )
    (releases / RELEASES_SUITE).symlink_to(release / "suite")
    suite = load_suite_release(release / "suite")
    manifest = seal_run_manifest(
        {
            "environment": [{"digest": environment.digest, "name": environment.name}],
            "suite": {"digest": suite.digest},
            "binding": {"digest": binding.digest},
            "agent": binding.agent.model_dump(mode="json"),
            "selection": {"scenario_keys": [suite.scenarios[0].key], "trials": 1},
            "simulator": {"llm": {"provider": "openai", "model": "gpt-4.1-mini"}},
            "scorer": {"alk_version": "test"},
            "runtime": {"provider": "e2b", "image_name": "alk", "image_digest": digest},
        }
    )
    # A routed binding runs only on what its proof proved; this test is about identity, so the
    # proof covers the one scenario selected.
    proof = BindingProofV1(
        binding={"digest": binding.digest},
        suite={"digest": suite.digest},
        environment={"digest": environment.digest},
        scenarios={suite.scenarios[0].key: {"status": "proved", "reason": ""}},
        passed=True,
    )
    paths = {}
    for name, body in (
        ("binding", binding.model_dump(mode="json", by_alias=True)),
        ("manifest", manifest.model_dump(mode="json", by_alias=True)),
        ("job", job.model_dump(mode="json")),
        ("proof", proof.model_dump(mode="json")),
    ):
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(body), encoding="utf-8")
    bundle = compose_bundle(
        releases,
        binding_path=paths["binding"],
        manifest_path=paths["manifest"],
        job_path=paths["job"],
        output=tmp_path / "bundle",
        binding_proof_path=paths["proof"],
    )
    assert bundle.metadata["release"]["binding"] == binding.digest


@needs_releases
def test_a_call_the_agents_schema_refuses_proves_nothing(tmp_path):
    """CardGuard's policy tool takes its topics in capitals and the binding lowers them for the
    bank. A trajectory that sends the bank's own spelling is not a call CardGuard can make: it is
    not derived, and the proof does not pass the scenario on it; the capitalized call proves it."""
    from fi.alk.harness.adaptation.profile import profile_from_file, seal_binding
    from fi.alk.harness.binding_proof import prove_binding
    from fi.alk.harness.binding_trajectories import derive_trajectories
    from fi.alk.harness.folder import read_folder
    from fi.alk.harness.releases import BindingMode, load_suite_release, load_tools

    release = Path(RELEASES) / "banking"
    environment = release / "environment"
    suite = load_suite_release(release / "suite")
    scenarios = [
        (entry.key, read_folder(release / "suite", Path(entry.path).name).solution)
        for entry in suite.scenarios
    ]

    def bound(capitals: bool):
        profile, mapping = fixtures.declared_agent(
            environment,
            name="CardGuard",
            instructions=fixtures.CARDGUARD_INSTRUCTIONS,
            renames=fixtures.CARDGUARD_RENAMES,
        )
        if capitals:
            tool = next(one for one in profile["tools"] if one["name"] == "policy")
            topic = tool["parameters"]["properties"]["topic"]
            topic["enum"] = [value.upper() for value in topic["enum"]]
            entry = next(
                one for one in mapping["tools"] if one["agent_tool"] == "policy"
            )
            entry["steps"][0]["arguments"]["policy_type"]["convert"] = "lower"
        return seal_binding(
            profile_from_file(profile),
            BindingMode.TOOL_ROUTED,
            mapping,
            environment_root=environment,
        )

    binding = bound(capitals=True)
    derived = derive_trajectories(binding, scenarios, load_tools(environment))
    key = next(
        key for key, why in sorted(derived.undecided.items()) if "schema refuses" in why
    )
    # The bank's spelling, as a derivation that ignored the agent's schema wrote it.
    lowered = derive_trajectories(
        bound(capitals=False), scenarios, load_tools(environment)
    ).trajectories[key]
    capitalized = [
        {
            **call,
            "arguments": {
                name: value.upper() if name == "topic" else value
                for name, value in call["arguments"].items()
            },
        }
        for call in lowered
    ]
    assert any(call["tool"] == "policy" for call in lowered)
    for calls, status in ((lowered, "failed"), (capitalized, "proved")):
        proof = prove_binding(
            environment, release / "suite", binding, {key: calls}, postgres_dsn=DSN
        )
        assert proof.scenarios[key].status == status, proof.scenarios[key]
        if status == "failed":
            assert "cannot make" in proof.scenarios[key].reason


@needs_releases
def test_an_agent_with_some_of_the_banks_own_tools_is_served_only_those(tmp_path):
    """An agent that calls the bank's tools by their own names but lacks three of them is not
    canonical: the harness binds it onto its own tools, the world serves only those, and the
    proof holds every scenario it can reach."""
    from fi.alk.harness.adaptation.profile import profile_from_file
    from fi.alk.harness.adaptation.workbench import Workbench
    from fi.alk.harness.releases import BindingMode, load_tools
    from fi.alk.harness.world_tools import WorldTools

    release = Path(RELEASES) / "banking"
    environment = release / "environment"
    lacking = {"update_contact_details", "withdraw_dispute", "create_handoff"}
    own = {
        tool.name: (tool.name, {})
        for tool in load_tools(environment)
        if tool.name not in lacking
    }
    profile, _mapping = fixtures.declared_agent(
        environment, name="Teller", instructions="Help.", renames=own
    )
    workbench = Workbench(
        environments=[environment],
        suite_root=release / "suite",
        output=tmp_path / "out",
        postgres_dsn=DSN,
        profile=profile_from_file(profile),
    )
    assert workbench.mode is BindingMode.TOOL_ROUTED
    assert workbench.draft is not None
    served = WorldTools({}, load_tools(environment), "postgresql://unused/world")
    served.binding = workbench.binding
    assert {tool.name for tool in served.tools()} == set(own)
    passed, said = workbench.run_proof()
    assert passed, said
    statuses = {verdict.status for verdict in workbench.verdicts.values()}
    assert statuses <= {"proved", "unscorable"} and "proved" in statuses
