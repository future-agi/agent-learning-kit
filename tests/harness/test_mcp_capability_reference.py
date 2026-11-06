"""The MCP capabilities (Slack, Gmail, Calendar, Drive) match the official servers' published
interfaces, pinned per package in `mcp_reference.json` (Wave A plan §3.2, workbook C11): every
attached tool exists with the official argument names, types and required set, plus only the
local extensions the reference declares; and each reference solution's successful calls answer
with the official result fields, plus only the declared local ones."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from fi.alk import environments
from fi.alk.environments import designed
from fi.alk.harness.folder import read_folder
from fi.alk.harness.prove import _run
from fi.alk.harness.releases import (
    WORLD,
    PolicyProfile,
    apply_policy_profile,
    load_policy,
)

ROOT = Path(environments.__file__).parent
PACKAGES = sorted(
    path.name for path in ROOT.iterdir() if (path / "mcp_reference.json").is_file()
)


def _reference(package: str) -> dict:
    return json.loads(
        (ROOT / package / "mcp_reference.json").read_text(encoding="utf-8")
    )


def _tools(package: str) -> dict[str, dict]:
    tools = json.loads((ROOT / package / "tools.json").read_text(encoding="utf-8"))
    return {tool["name"]: tool for tool in tools}


@pytest.mark.parametrize("package", PACKAGES)
def test_tool_definitions_match_the_official_interface(package: str) -> None:
    reference, tools = _reference(package), _tools(package)
    local = reference.get("local", {})
    official = reference["tools"]
    assert set(official) <= set(tools), (
        f"{package}: official tools missing from tools.json"
    )
    extra = set(tools) - set(official) - set(local.get("tools", {}))
    assert not extra, (
        f"{package}: tools the official server lacks and the reference does not declare: {sorted(extra)}"
    )
    for name, spec in official.items():
        ours = tools[name]["parameters"]
        allowed = set(spec["input"]["properties"]) | set(
            local.get("parameters", {}).get(name, {})
        )
        unknown = set(ours.get("properties", {})) - allowed
        assert not unknown, (
            f"{package}.{name}: arguments the official server lacks: {sorted(unknown)}"
        )
        missing_required = set(spec["input"]["required"]) - set(
            ours.get("required", [])
        )
        assert not missing_required, (
            f"{package}.{name}: official required arguments not required here: {sorted(missing_required)}"
        )
        over_required = (
            set(ours.get("required", []))
            - set(spec["input"]["required"])
            - set(local.get("parameters", {}).get(name, {}))
        )
        assert not over_required, (
            f"{package}.{name}: arguments required here but optional officially: {sorted(over_required)}"
        )
        for argument, schema in spec["input"]["properties"].items():
            if argument in ours.get("properties", {}):
                assert ours["properties"][argument]["type"] == schema["type"], (
                    f"{package}.{name}.{argument}: type"
                )


@pytest.mark.parametrize("package", PACKAGES)
def test_reference_solutions_answer_with_the_official_result_fields(
    package: str, tmp_path: Path
) -> None:
    reference = _reference(package)
    local_fields = reference.get("local", {}).get("result_fields", {})
    here = ROOT / package
    designed.build_environment(here, tmp_path / "environment")
    world_root = tmp_path / WORLD
    shutil.copytree(tmp_path / "environment" / WORLD, world_root)
    apply_policy_profile(
        world_root, load_policy(tmp_path / "environment"), PolicyProfile()
    )
    suite = here / "suite"
    seen: set[str] = set()
    for folder in sorted(
        path for path in (suite / "scenarios").iterdir() if path.is_dir()
    ):
        scenario = read_folder(suite, folder.name)
        assert scenario is not None
        world, calls, _refused, _ = _run(scenario, world_root, with_solution=True)
        try:
            for call in calls:
                spec = reference["tools"].get(call.name)
                if spec is None or not call.ok or not isinstance(call.result, dict):
                    continue
                seen.add(call.name)
                keys = set(call.result)
                missing = set(spec["output"]["required"]) - keys
                assert not missing, (
                    f"{package}.{call.name} ({folder.name}): official result fields missing: {sorted(missing)}"
                )
                allowed = set(spec["output"]["properties"]) | {
                    field.split("[")[0] for field in local_fields.get(call.name, {})
                }
                extra = keys - allowed
                assert not extra, (
                    f"{package}.{call.name} ({folder.name}): result fields the official server lacks: {sorted(extra)}"
                )
        finally:
            world.close()
    assert seen, f"{package}: no reference solution exercised an official tool"
