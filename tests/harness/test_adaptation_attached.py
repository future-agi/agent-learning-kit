"""A customer agent adapted onto tools a release serves from genuinely attached MCP servers.

The clinic release (`fi.alk.environments.appointment_booking`) attaches our read-only `sql` server
over its reference tables and the pinned filesystem server over its patient guides. The customer
calls them by its own names, with its own argument names; the binding maps them onto
`sql__query` and `docs__read_text_file`, the harness derives the trajectories, and the proof runs
every call through the real servers.

Needs a PostgreSQL server that may create databases and roles, and the filesystem server installed
from `mcp-servers/filesystem` (`npm ci` there, or under ALK_MCP_SERVERS_ROOT):

    ALK_RELEASE_POSTGRES_DSN=postgresql://... ALK_MCP_SERVERS_ROOT=<dir with filesystem/> \\
        pytest tests/harness/test_adaptation_attached.py
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
from fi.alk.harness.mcp_servers import REGISTRY
from fi.alk.harness.releases import load_agent_binding, load_tools
from fi.alk.harness.world_tools import WorldTools

DSN = os.environ.get("ALK_RELEASE_POSTGRES_DSN", "")
pytestmark = pytest.mark.skipif(
    not DSN or not Path(REGISTRY["filesystem"].argv[1]).is_file(),
    reason="set ALK_RELEASE_POSTGRES_DSN and install the filesystem MCP server",
)
# The customer's names for the two attached tools, and for their arguments.
AGENT = {
    "provider": "declared",
    "name": "Riverside concierge",
    "instructions": "Answer from the clinic's schedule and guides.",
    "tools": [
        {
            "name": "look_up_schedule",
            "description": "Run one read-only query over the clinic's schedule.",
            "parameters": {
                "type": "object",
                "properties": {"statement": {"type": "string"}},
                "required": ["statement"],
            },
        },
        {
            "name": "read_guide",
            "description": "Read one of the clinic's patient guides.",
            "parameters": {
                "type": "object",
                "properties": {"file": {"type": "string"}},
                "required": ["file"],
            },
        },
    ],
}
MAPPING = {
    "tools": [
        {
            "agent_tool": "look_up_schedule",
            "steps": [
                {
                    "id": "query",
                    "tool": "sql__query",
                    "arguments": {"sql": {"arg": "statement"}},
                }
            ],
        },
        {
            "agent_tool": "read_guide",
            "steps": [
                {
                    "id": "read",
                    "tool": "docs__read_text_file",
                    "arguments": {"path": {"arg": "file"}},
                }
            ],
        },
    ]
}


@pytest.fixture(scope="module")
def release(tmp_path_factory) -> Path:
    from fi.alk.environments.appointment_booking import build

    root = tmp_path_factory.mktemp("clinic")
    build.build_environment(root / "environment")
    build.build_suite(root / "suite", root / "environment", postgres_dsn=DSN)
    return root


def test_a_renamed_agent_is_proved_over_the_attached_servers(release, tmp_path):
    profile, drafts = fixtures.write_drafts(tmp_path / "inputs", AGENT, MAPPING)
    code = main(
        [
            "run",
            "--environment",
            str(release / "environment"),
            "--suite",
            str(release / "suite"),
            "--profile",
            str(profile),
            "--drafts",
            str(drafts),
            "--output",
            str(tmp_path / "out"),
            "--postgres-dsn",
            DSN,
            "--approve",
        ]
    )
    out = tmp_path / "out"
    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert code == 0, result.get("failure")
    assert result["binding"]["mode"] == "tool_routed"
    mapped = {one["agent_tool"]: one["steps"] for one in result["alignment"]["mapped"]}
    assert mapped == {
        "look_up_schedule": ["sql__query"],
        "read_guide": ["docs__read_text_file"],
    }
    proof = json.loads((out / "binding-proof.json").read_text(encoding="utf-8"))
    proved = {
        key for key, one in proof["scenarios"].items() if one["status"] == "proved"
    }
    assert {"patel-clinic-days", "first-visit-preparation"} <= proved, proof
    # The harness derived the agent's own calls from the reference solutions.
    trajectories = json.loads((out / "trajectories.json").read_text(encoding="utf-8"))
    assert trajectories["first-visit-preparation"] == [
        {"tool": "read_guide", "arguments": {"file": "new-patient-guide.md"}}
    ]
    assert trajectories["patel-clinic-days"][0]["tool"] == "look_up_schedule"
    # A run serves the customer's names, and nothing of the environment's.
    world = WorldTools(
        {},
        load_tools(release / "environment"),
        "postgresql://unused/world",
        binding=load_agent_binding(out / "binding.json"),
    )
    assert [tool.name for tool in world.tools()] == ["look_up_schedule", "read_guide"]

    # The composed run's tool server gets the address of the reader login the harness makes.
    import shutil

    from fi.alk.harness.job import HarnessJob
    from fi.alk.harness.process_preflight import _verify_placeholder_vocabulary
    from fi.alk.harness.releases import (
        compose_bundle,
        environment_directory,
        seal_run_manifest,
    )
    from fi.alk.harness.world_roles import has_world_reader

    releases = tmp_path / "releases"
    sealed = json.loads((release / "environment" / "release.json").read_text())
    shutil.copytree(
        release / "environment", environment_directory(releases, sealed["digest"])
    )
    shutil.copytree(release / "suite", releases / "suite")
    job = tmp_path / "job.json"
    job.write_text(
        HarnessJob(
            job_id="attached",
            run_id="attached-run",
            execution="local",
            source={"kind": "local_repository", "local_path": str(tmp_path)},
            agent={
                "connector": "mcp_client",
                "config": {
                    key: value
                    for key, value in json.loads(profile.read_text()).items()
                    if key != "provider"
                },
            },
            scenario_count=1,
        ).model_dump_json(),
        encoding="utf-8",
    )
    binding = json.loads((out / "binding.json").read_text())
    manifest = seal_run_manifest(
        {
            "environment": [{"digest": sealed["digest"], "name": sealed["name"]}],
            "suite": {
                "digest": json.loads((release / "suite" / "suite.json").read_text())["digest"]
            },
            "binding": {"digest": binding["digest"]},
            "binding_proof": result["binding_proof"],
            "agent": binding["agent"],
            "selection": {"scenario_keys": ["first-visit-preparation"], "trials": 1},
            "simulator": {"llm": {"provider": "openai", "model": "gpt-test"}},
            "scorer": {"alk_version": "test"},
            "runtime": {
                "provider": "test",
                "image_name": "alk",
                "image_digest": "sha256:" + "1" * 64,
            },
        }
    )
    (tmp_path / "manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    bundle = compose_bundle(
        releases,
        binding_path=out / "binding.json",
        binding_proof_path=out / "binding-proof.json",
        manifest_path=tmp_path / "manifest.json",
        job_path=job,
        output=tmp_path / "bundle",
    )
    assert has_world_reader(bundle)
    _verify_placeholder_vocabulary(bundle)
