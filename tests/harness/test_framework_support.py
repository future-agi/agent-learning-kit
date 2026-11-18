"""The supported-frameworks list (ADR-006): every claim backed by evidence that exists."""

from __future__ import annotations

import re
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
            assert case.rstrip("]") in source, ref
    else:
        path, _, heading = ref.partition("#")
        headings = [
            line.lstrip("#").strip()
            for line in (REPO / path).read_text(encoding="utf-8").splitlines()
            if line.startswith("#")
        ]
        assert heading in headings, ref


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


def test_capabilities_carry_the_file_as_framework_support() -> None:
    from fi.alk.capabilities import capability_catalog

    catalog = capability_catalog()
    assert catalog["framework_support"] == framework_support.load()
    # The simulate SDK's adapter names stay where they were, unchanged.
    assert "frameworks" in catalog["capabilities"]
