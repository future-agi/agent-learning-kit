"""The Wave A vertical packages: each is named after its vertical, carries its workbook cases
under their IDs, documents them in its README's coverage map, and still seals."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fi.alk import environments
from fi.alk.environments import designed

ROOT = Path(environments.__file__).parent
# Package -> phase-1 workbook entry whose five acceptance cases it must carry.
VERTICALS = {
    "banking": "v01",
    "hr": "v13",
    "retail": "v15",
    "saas_subscriptions": "v16",
    "internal_it": "v20",
}


@pytest.mark.parametrize("package", sorted(VERTICALS))
def test_vertical_package_is_documented(package: str) -> None:
    here = ROOT / package
    spec = json.loads((here / "environment.json").read_text(encoding="utf-8"))
    assert spec["name"] == package
    assert (here / "sample_agent.md").read_text(encoding="utf-8").strip()
    readme = (here / "README.md").read_text(encoding="utf-8")
    entry = VERTICALS[package]
    for case in range(1, 6):
        prefix = f"{entry}_s{case:02d}_"
        folders = [
            path.name
            for path in (here / "suite" / "scenarios").iterdir()
            if path.name.startswith(prefix)
        ]
        assert len(folders) == 1, (package, prefix, folders)
        scenario = json.loads(
            (here / "suite" / "scenarios" / folders[0] / "scenario.json").read_text(
                encoding="utf-8"
            )
        )
        assert scenario["name"] == folders[0]
        assert scenario["scenario_key"] == folders[0].replace("_", "-")
        assert f"`{folders[0]}`" in readme, (package, folders[0])


def test_inventory_lists_every_package() -> None:
    inventory = (ROOT / "README.md").read_text(encoding="utf-8")
    packages = sorted(
        path.name for path in ROOT.iterdir() if (path / "environment.json").is_file()
    )
    assert packages, "no designed packages"
    assert "notion_action_tracker" not in packages
    for package in packages + ["appointment_booking"]:
        assert f"`{package}`" in inventory, package


def test_renamed_scenarios_seal(tmp_path: Path) -> None:
    here = ROOT / "saas_subscriptions"
    environment = designed.build_environment(here, tmp_path / "environment")
    suite = designed.build_suite(here, tmp_path / "suite", tmp_path / "environment")
    assert environment.startswith("sha256:") and suite.startswith("sha256:")
    sealed = json.loads((tmp_path / "suite" / "suite.json").read_text(encoding="utf-8"))
    keys = {entry["key"] for entry in sealed["scenarios"]}
    assert {f"v16-s{case:02d}" for case in range(1, 6)} <= {key[:7] for key in keys}
