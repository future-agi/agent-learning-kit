"""The supported-frameworks list (ADR-006): every claim backed by evidence that exists."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from fi.alk.harness import framework_support, scan_document
from fi.alk.harness.framework_support import CHECKS, LEVELS, RESULTS

REPO = Path(__file__).resolve().parents[2]
ROWS = framework_support.load()
SMOKE = (REPO / ".github" / "workflows" / "sdk-smoke.yml").read_text(encoding="utf-8")


def test_every_one_of_the_ten_names_has_a_row() -> None:
    assert sorted(row.name for row in ROWS) == sorted(
        scan_document.FRAMEWORK_NAMES[:10]
    )


def test_levels_and_checks_use_known_words() -> None:
    for row in ROWS:
        assert row.level in LEVELS, row.name
        assert tuple(row.checks) == CHECKS, row.name
        assert set(row.checks.values()) <= set(RESULTS), row.name


def test_no_row_claims_runs_with_a_check_not_passed() -> None:
    for row in ROWS:
        if LEVELS.index(row.level) >= LEVELS.index("runs"):
            assert set(row.checks.values()) == {"passed"}, row.name


def test_a_check_has_a_result_only_from_a_run_on_record() -> None:
    for row in ROWS:
        ran = any(result != "not_run" for result in row.checks.values())
        assert ran == (row.evidence["runs"] is not None), row.name


def test_each_level_reached_points_at_its_evidence() -> None:
    for row in ROWS:
        assert row.evidence["recognised"], row.name
        for level in LEVELS[1 : LEVELS.index(row.level) + 1]:
            claim = row.evidence[level]
            assert claim and claim["date"] and claim["place"], (row.name, level)


def _places() -> list[tuple[str, str]]:
    places = [(row.name, item) for row in ROWS for item in row.evidence["recognised"]]
    for row in ROWS:
        for level in LEVELS[1:]:
            if row.evidence[level]:
                places.append((row.name, row.evidence[level]["place"]))
    return places


@pytest.mark.parametrize("name, item", _places(), ids=lambda value: str(value)[:80])
def test_every_piece_of_evidence_names_something_that_exists(
    name: str, item: str
) -> None:
    kind, _, target = item.partition(":")
    if kind == "test":
        path, _, test = target.partition("::")
        source = (REPO / path).read_text(encoding="utf-8")
        function, _, case = test.partition("[")
        assert re.search(rf"^(async )?def {function}\(", source, re.M), item
        if case:
            assert case.rstrip("]") in source, item
    elif kind == "record":
        path, _, heading = target.partition("#")
        headings = [
            line.lstrip("#").strip()
            for line in (REPO / path).read_text(encoding="utf-8").splitlines()
            if line.startswith("#")
        ]
        assert heading in headings, item
    else:
        pytest.fail(f"unknown evidence kind: {item}")


def test_every_tested_version_is_a_pin_of_its_reference_or_a_ci_row() -> None:
    for row in ROWS:
        pins = set()
        if row.reference:
            for line in (
                (REPO / row.reference / "requirements.txt").read_text().splitlines()
            ):
                match = re.match(
                    r"([A-Za-z0-9_.-]+)(?:\[[^]]*\])?==(\S+)", line.strip()
                )
                if match:
                    pins.add(f"{match.group(1)}=={match.group(2)}")
        for version in row.tested_versions:
            package, _, number = version.partition("==")
            in_ci = version in SMOKE or (
                f"{package}==" in SMOKE and f'"{number}"' in SMOKE
            )
            assert version in pins or in_ci, (row.name, version)


def test_the_reference_exists_and_its_python_is_the_one_stated() -> None:
    for row in ROWS:
        if row.reference is None:
            assert row.python is None and not row.tested_versions, row.name
            continue
        pinned = (REPO / row.reference / ".python-version").read_text().strip()
        assert row.python == pinned, row.name


def test_the_docs_page_is_generated_from_the_list() -> None:
    page = (REPO / "docs" / "frameworks" / "supported.md").read_text(encoding="utf-8")
    assert page == framework_support.markdown_page()
