"""The supported-frameworks list: each claim is backed by evidence that exists, in one place."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from fi.alk import framework_support
from fi.alk.harness import scan_document
from fi.alk.framework_support import FRAMEWORKS, LAYERS, STATUSES

REPO = Path(__file__).resolve().parents[2]


def test_the_list_covers_exactly_the_frameworks_the_reader_names() -> None:
    named = scan_document.FRAMEWORK_NAMES[:10]
    assert [entry.key for entry in FRAMEWORKS] == sorted(named)


def test_every_layer_has_a_known_status_and_a_proven_one_has_evidence() -> None:
    for entry in FRAMEWORKS:
        for layer in LAYERS:
            claim = getattr(entry, layer)
            assert claim.status in STATUSES, (entry.key, layer)
            if claim.status in ("proven", "partial"):
                assert claim.evidence, (entry.key, layer, "a claim with no evidence")
            if claim.status != "proven":
                assert claim.note, (entry.key, layer, "say what is missing")


def _evidence() -> list[tuple[str, str, str]]:
    return [
        (entry.key, layer, item)
        for entry in FRAMEWORKS
        for layer in LAYERS
        for item in getattr(entry, layer).evidence
    ]


@pytest.mark.parametrize(
    "key, layer, item", _evidence(), ids=lambda value: str(value)[:80]
)
def test_every_piece_of_evidence_names_something_that_exists(
    key: str, layer: str, item: str
) -> None:
    kind, _, target = item.partition(":")
    if kind == "test":
        path, _, name = target.partition("::")
        source = (REPO / path).read_text(encoding="utf-8")
        function, _, case = name.partition("[")
        assert re.search(rf"^(async )?def {function}\(", source, re.M), item
        if case:
            # A case is named by its id, which the test file spells out.
            assert case.rstrip("]") in source, item
    elif kind == "record":
        path, _, heading = target.partition("#")
        text = (REPO / path).read_text(encoding="utf-8")
        assert any(
            line.lstrip("#").strip() == heading
            for line in text.splitlines()
            if line.startswith("#")
        ), item
    elif kind == "pr":
        assert re.fullmatch(r"future-agi/agent-learning-kit#\d+ \S+::\S+", target), item
    else:
        pytest.fail(f"unknown evidence kind: {item}")


def test_pending_names_the_change_it_waits_for() -> None:
    for entry in FRAMEWORKS:
        for layer in LAYERS:
            claim = getattr(entry, layer)
            if claim.status == "pending":
                assert any(item.startswith("pr:") for item in claim.evidence), (
                    entry.key,
                    layer,
                )


def test_an_example_pin_is_the_one_its_requirements_name() -> None:
    for entry in FRAMEWORKS:
        if entry.example is None:
            assert entry.example_pin is None, entry.key
            continue
        requirements = (REPO / entry.example / "requirements.txt").read_text(
            encoding="utf-8"
        )
        pins = dict(
            re.match(r"([A-Za-z0-9_.-]+)(?:\[[^]]*\])?==(\S+)", line).groups()
            for line in requirements.splitlines()
            if "==" in line
        )
        assert pins.get(entry.package) == entry.example_pin, entry.key


def test_a_tested_version_says_where_it_ran() -> None:
    for entry in FRAMEWORKS:
        if entry.tested_version is None:
            assert entry.tested_in == "", entry.key
        else:
            assert entry.tested_in, entry.key


def test_the_docs_table_is_the_list() -> None:
    page = (REPO / "docs" / "frameworks" / "supported.md").read_text(encoding="utf-8")
    assert framework_support.markdown_table() in page


def test_capabilities_carry_the_list() -> None:
    from fi.alk.capabilities import capability_catalog

    listed = capability_catalog()["framework_support"]
    assert listed == framework_support.as_records()
    assert {row["key"] for row in listed} == set(scan_document.FRAMEWORK_NAMES[:10])
    assert set(listed[0]) == {
        "key",
        "name",
        "package",
        "tested_version",
        "tested_in",
        "example",
        "example_pin",
        "notes",
        *LAYERS,
    }
