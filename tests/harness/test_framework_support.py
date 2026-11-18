"""The supported-frameworks list: every claim backed by evidence that exists."""

from __future__ import annotations

import re
import subprocess
import sys
from functools import cache
from pathlib import Path

import pytest

from fi.alk.harness import framework_support, scan_document
from fi.alk.harness.framework_support import (
    CHECKS,
    EVIDENCE_KINDS,
    LAYERS,
    RESULTS,
    STATUSES,
)

REPO = Path(__file__).resolve().parents[2]
ENTRIES = framework_support.frameworks()
SMOKE = (REPO / ".github" / "workflows" / "sdk-smoke.yml").read_text(encoding="utf-8")
# The kit commits a real run can prove a layer on: commits in this branch's history, so the
# reference agent and the reading code are the ones here. Any other commit, or none, proves
# nothing (the list fails closed). Add a commit only after checking it is an ancestor.
MERGED_KITS = {"d9b61c0370800a5faa3140012ec50211ba0bfd82"}
# Unmerged kit commits a run was made on, with the PR each is the head of; a pass on one leaves
# its layer pending and its note names the PR.
UNMERGED_KITS = {"5aa31fdb30aa02260d2d006cad121991a3b3828c": "#341"}


def test_the_schema_is_named() -> None:
    assert (
        framework_support.load()["schema_version"] == "futureagi.framework-support.v1"
    )


def test_all_ten_names_have_an_entry() -> None:
    assert [entry["key"] for entry in ENTRIES] == sorted(
        scan_document.FRAMEWORK_NAMES[:10]
    )


def test_every_layer_uses_known_words_and_a_pending_one_says_what_it_waits_on() -> None:
    for entry in ENTRIES:
        assert tuple(entry["layers"]) == LAYERS, entry["key"]
        for layer in LAYERS:
            claim = entry["layers"][layer]
            assert claim["status"] in STATUSES, (entry["key"], layer)
            if claim["status"] == "pending":
                assert claim["note"], (entry["key"], layer)
            for item in claim["evidence"]:
                assert item["kind"] in EVIDENCE_KINDS, (entry["key"], layer, item)
                if item["kind"] == "real_run":
                    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", item["date"]), item
                    # Each real run names the model that did the reading.
                    assert item.get("model"), item
                    assert item.get("result") in ("pass", "miss"), item
                    # `kit` is null when no single commit holds what ran.
                    assert "kit" in item, item
                    assert item["kit"] is None or re.fullmatch(
                        r"[0-9a-f]{40}", item["kit"]
                    ), item
        checks = entry["layers"]["run"]["checks"]
        assert tuple(checks) == CHECKS and set(checks.values()) <= set(RESULTS), entry[
            "key"
        ]


def test_read_and_run_are_proven_only_by_a_real_run() -> None:
    for entry in ENTRIES:
        for layer in ("read", "run"):
            claim = entry["layers"][layer]
            if claim["status"] == "proven":
                kinds = {item["kind"] for item in claim["evidence"]}
                assert kinds == {"real_run"}, (entry["key"], layer)
                # A miss proves nothing, and nor does a pass on a kit not merged here.
                assert any(
                    item["result"] == "pass" and item["kit"] in MERGED_KITS
                    for item in claim["evidence"]
                ), (entry["key"], layer)


def test_a_pass_on_an_unmerged_kit_leaves_its_layer_pending_on_that_pr() -> None:
    for entry in ENTRIES:
        for layer in ("read", "run"):
            claim = entry["layers"][layer]
            passes = [i for i in claim["evidence"] if i.get("result") == "pass"]
            if passes and all(i["kit"] in UNMERGED_KITS for i in passes):
                assert claim["status"] == "pending", (entry["key"], layer)
                for item in passes:
                    assert UNMERGED_KITS[item["kit"]] in claim["note"], (
                        entry["key"],
                        layer,
                    )


def test_a_real_run_on_an_outside_agent_is_pending_until_the_kit_holds_a_reference() -> (
    None
):
    for entry in ENTRIES:
        if entry["reference"] is not None:
            continue
        for layer in ("read", "run"):
            claim = entry["layers"][layer]
            assert claim["status"] != "proven", (entry["key"], layer)
            if claim["status"] == "pending":
                assert "a reference agent in the kit" in claim["note"], (
                    entry["key"],
                    layer,
                )


def test_found_is_proven_by_tests() -> None:
    for entry in ENTRIES:
        found = entry["layers"]["found"]
        assert found["status"] == "proven", entry["key"]
        assert found["evidence"], entry["key"]


def test_run_is_proven_only_when_all_five_checks_passed() -> None:
    for entry in ENTRIES:
        run = entry["layers"]["run"]
        if run["status"] == "proven":
            assert set(run["checks"].values()) == {"passed"}, entry["key"]


def test_a_check_has_a_result_only_from_a_real_run() -> None:
    for entry in ENTRIES:
        run = entry["layers"]["run"]
        if any(result != "not_run" for result in run["checks"].values()):
            assert any(item["kind"] == "real_run" for item in run["evidence"]), entry[
                "key"
            ]


def _evidence() -> list[tuple[str, str, dict]]:
    return [
        (entry["key"], layer, item)
        for entry in ENTRIES
        for layer in LAYERS
        for item in entry["layers"][layer]["evidence"]
    ]


@cache
def _collected(path: str) -> frozenset[str]:
    """The test ids pytest collects from one file, so a cited case must exist as a case."""
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            path,
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    return frozenset(line.strip() for line in done.stdout.splitlines() if "::" in line)


def _section(path: Path, heading: str) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(
        n for n, line in enumerate(lines) if line.lstrip("#").strip() == heading
    )
    end = next(
        (n for n in range(start + 1, len(lines)) if lines[n].startswith("#")),
        len(lines),
    )
    return [line.lstrip("- ").strip() for line in lines[start + 1 : end]]


@pytest.mark.parametrize(
    "key, layer, item", _evidence(), ids=lambda value: str(value)[:80]
)
def test_every_piece_of_evidence_names_something_that_exists(
    key: str, layer: str, item: dict
) -> None:
    ref = item["ref"]
    if item["kind"] == "test":
        path, _, test = ref.partition("::")
        source = (REPO / path).read_text(encoding="utf-8")
        function, _, case = test.partition("[")
        assert re.search(rf"^(async )?def {function}\(", source, re.M), ref
        if case:
            assert ref in _collected(path), ref
    else:
        path, _, heading = ref.partition("#")
        record = REPO / path
        headings = [
            line.lstrip("#").strip()
            for line in record.read_text(encoding="utf-8").splitlines()
            if line.startswith("#")
        ]
        assert heading in headings, ref
        # The record states the result and the kit commit the entry claims.
        if item["kit"] is not None:
            kit_lines = [
                line
                for line in record.read_text(encoding="utf-8").splitlines()
                if line.startswith("- Kit:")
            ]
            assert len(kit_lines) == 1 and f"`{item['kit']}`" in kit_lines[0], ref
        word = {"read": "Reading", "run": "Run"}[layer]
        section = _section(record, heading)
        assert any(
            line == item["result"] or line.startswith(f"{word}: {item['result']}")
            for line in section
        ), ref


def test_every_tested_version_is_a_pin_of_its_reference_or_a_ci_row() -> None:
    for entry in ENTRIES:
        pins = set()
        if entry["reference"]:
            requirements = REPO / entry["reference"] / "requirements.txt"
            for line in requirements.read_text(encoding="utf-8").splitlines():
                match = re.match(
                    r"([A-Za-z0-9_.-]+)(?:\[[^]]*\])?==(\S+)", line.strip()
                )
                if match:
                    pins.add(f"{match.group(1)}=={match.group(2)}")
        for version in entry["tested_versions"]:
            package, _, number = version.partition("==")
            assert package == entry["package"], (entry["key"], version)
            in_ci = version in SMOKE or (
                f"{package}==" in SMOKE and f'"{number}"' in SMOKE
            )
            assert version in pins or in_ci, (entry["key"], version)


def test_the_reference_exists_and_its_python_is_the_one_stated() -> None:
    for entry in ENTRIES:
        if entry["reference"] is None:
            assert entry["python"] is None and not entry["tested_versions"], entry[
                "key"
            ]
            continue
        pinned = (REPO / entry["reference"] / ".python-version").read_text().strip()
        assert entry["python"] == pinned, entry["key"]


def test_the_docs_page_is_generated_from_the_file() -> None:
    page = (REPO / "docs" / "frameworks" / "supported.md").read_text(encoding="utf-8")
    assert page == framework_support.markdown_page()


def _pins(reference: str) -> dict[str, str]:
    pins = {}
    requirements = REPO / reference / "requirements.txt"
    for line in requirements.read_text(encoding="utf-8").splitlines():
        match = re.match(r"([A-Za-z0-9_.-]+)(?:\[[^]]*\])?==(\S+)", line.strip())
        if match:
            pins[match.group(1)] = match.group(2)
    return pins


def test_pinned_versions_are_the_references_pin_of_the_entrys_own_package() -> None:
    for entry in ENTRIES:
        package = entry["package"]
        if entry["reference"] is None:
            assert entry["pinned_versions"] == [], entry["key"]
            continue
        pins = _pins(entry["reference"])
        assert package in pins, (entry["key"], package)
        assert entry["pinned_versions"] == [f"{package}=={pins[package]}"], entry["key"]
