"""The supported-frameworks list: every claim backed by evidence that exists."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from functools import cache
from pathlib import Path

import pytest

from fi.alk import trinity
from fi.alk.harness import framework_detection, framework_support, scan_document
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
UNMERGED_KITS: dict[str, str] = {}
# The models a real run's reading is recorded with. Add a model only as a run's own logs name it.
KNOWN_MODELS = {"claude-sonnet (laptop gateway)"}
# The reader's words that name no framework.
NOT_A_FRAMEWORK = {"other", "none", "unknown"}
EVIDENCE = "src/fi/alk/harness/data/framework_evidence/"


def test_the_schema_is_named() -> None:
    assert (
        framework_support.load()["schema_version"] == "futureagi.framework-support.v1"
    )


def test_every_framework_the_reader_names_has_an_entry() -> None:
    named = set(scan_document.FRAMEWORK_NAMES) - NOT_A_FRAMEWORK
    assert [entry["key"] for entry in ENTRIES] == sorted(named)
    assert named == framework_detection._NAMED_FRAMEWORKS


def test_every_entrys_package_is_one_its_detection_row_names() -> None:
    for entry in ENTRIES:
        row = framework_detection._ROW_BY_FRAMEWORK[entry["key"]]
        assert entry["package"] in row.packages, (entry["key"], row.packages)


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
                    # Each real run names the model that did the reading, as its logs name it.
                    assert item.get("model") in KNOWN_MODELS, item
                    # A record is named by the date it ran, and its evidence carries that date.
                    path = item["ref"].partition("#")[0]
                    assert path.startswith(EVIDENCE), item
                    assert Path(path).name.startswith(f"{item['date']}-"), item
                    assert item.get("result") in ("pass", "miss"), item
                    # `kit` is null when no single commit holds what ran.
                    assert "kit" in item, item
                    assert item["kit"] is None or re.fullmatch(
                        r"[0-9a-f]{40}", item["kit"]
                    ), item
                    sha = item.get("reference_sha256")
                    assert sha is None or re.fullmatch(r"[0-9a-f]{64}", sha), item
        checks = entry["layers"]["run"]["checks"]
        assert tuple(checks) == CHECKS and set(checks.values()) <= set(RESULTS), entry[
            "key"
        ]


def test_a_proven_layer_has_evidence() -> None:
    for entry in ENTRIES:
        for layer in LAYERS:
            claim = entry["layers"][layer]
            if claim["status"] == "proven":
                assert claim["evidence"], (entry["key"], layer)


def _proves(claim: dict, folder_hash: str | None) -> bool:
    """A pass on a merged kit, of the reference folder as it is in this checkout."""
    return {item["kind"] for item in claim["evidence"]} == {"real_run"} and any(
        item["result"] == "pass"
        and item["kit"] in MERGED_KITS
        and folder_hash is not None
        and item.get("reference_sha256") == folder_hash
        for item in claim["evidence"]
    )


def test_read_run_and_platform_are_proven_only_by_a_real_run_of_the_folder_as_it_is() -> (
    None
):
    for entry in ENTRIES:
        for layer in ("read", "run", "platform"):
            claim = entry["layers"][layer]
            if claim["status"] != "proven":
                continue
            # An absent reference folder fails here; it does not skip.
            assert entry["reference"], (entry["key"], layer)
            today = framework_support.reference_sha256(REPO / entry["reference"])
            # A miss proves nothing, nor does a pass on a kit not merged here, nor one of a
            # folder that has changed since: that needs a new run, or the layer goes pending.
            assert _proves(claim, today), (entry["key"], layer, today)


def test_a_proof_with_no_hash_or_another_hash_or_a_miss_is_refused() -> None:
    kit = next(iter(MERGED_KITS))
    good = {
        "kind": "real_run",
        "result": "pass",
        "kit": kit,
        "reference_sha256": "a" * 64,
    }
    assert _proves({"evidence": [good]}, "a" * 64)
    assert not _proves({"evidence": [good]}, "b" * 64)
    assert not _proves({"evidence": [{**good, "reference_sha256": None}]}, "a" * 64)
    no_field = {k: v for k, v in good.items() if k != "reference_sha256"}
    assert not _proves({"evidence": [no_field]}, "a" * 64)
    assert not _proves({"evidence": [{**good, "result": "miss"}]}, "a" * 64)
    assert not _proves({"evidence": [{**good, "kit": "c" * 40}]}, "a" * 64)
    assert not _proves({"evidence": [{**good, "kit": None}]}, "a" * 64)


def _git(folder: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=folder, check=True, capture_output=True)


def _tracked_copy(tmp_path: Path, reference: str) -> Path:
    folder = tmp_path / "reference"
    shutil.copytree(REPO / reference, folder)
    _git(folder, "init", "-q")
    _git(folder, "add", "-A")
    return folder


def test_the_folder_hash_counts_every_tracked_file_and_nothing_else(
    tmp_path: Path,
) -> None:
    folder = _tracked_copy(tmp_path, "examples/harness/crewai_support")
    before = framework_support.reference_sha256(folder)
    assert re.fullmatch(r"[0-9a-f]{64}", before)

    # Leftovers from importing or running the agent are not the folder.
    (folder / "__pycache__").mkdir()
    (folder / "__pycache__" / "agent.cpython-312.pyc").write_bytes(b"\0\1")
    (folder / ".DS_Store").write_bytes(b"x")
    assert framework_support.reference_sha256(folder) == before

    # One changed byte in a tracked file (Dileep's repro: a line appended to agent.py).
    agent = folder / "agent.py"
    original = agent.read_bytes()
    agent.write_bytes(original + b"\n# one more line\n")
    assert framework_support.reference_sha256(folder) != before
    agent.write_bytes(original)
    assert framework_support.reference_sha256(folder) == before

    # A tracked dotfile counts: removing .python-version changes the hash.
    _git(folder, "rm", "-q", "-f", ".python-version")
    assert framework_support.reference_sha256(folder) != before


def test_the_folder_hash_frames_each_path_and_its_bytes(tmp_path: Path) -> None:
    # The same bytes split differently between names must not give the same hash.
    one, two = tmp_path / "one", tmp_path / "two"
    for folder, files in ((one, {"a": b"bc", "d": b""}), (two, {"a": b"b", "cd": b""})):
        folder.mkdir()
        for name, data in files.items():
            (folder / name).write_bytes(data)
        _git(folder, "init", "-q")
        _git(folder, "add", "-A")
    assert framework_support.reference_sha256(
        one
    ) != framework_support.reference_sha256(two)


def test_an_absent_reference_folder_fails(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        framework_support.reference_sha256(tmp_path / "missing")


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


def test_a_check_has_a_result_only_from_a_real_run_on_a_merged_kit() -> None:
    # A run on no single commit can't prove a layer, so it can't fill the check columns either.
    for entry in ENTRIES:
        run = entry["layers"]["run"]
        if any(result != "not_run" for result in run["checks"].values()):
            assert any(
                item["kind"] == "real_run" and item["kit"] in MERGED_KITS
                for item in run["evidence"]
            ), entry["key"]


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
        # The record states the folder's hash on its own line, and the list carries the same one.
        hash_lines = [
            line
            for line in record.read_text(encoding="utf-8").splitlines()
            if line.startswith("- Reference sha256:")
        ]
        if item.get("reference_sha256") is None:
            assert hash_lines == [], ref
        else:
            assert len(hash_lines) == 1, ref
            assert hash_lines[0].startswith(
                f"- Reference sha256: `{item['reference_sha256']}`"
            ), ref
        word = {"read": "Reading", "run": "Run"}[layer]
        section = _section(record, heading)
        assert any(
            line == item["result"] or line.startswith(f"{word}: {item['result']}")
            for line in section
        ), ref


def test_a_version_is_tested_only_when_a_real_run_or_a_ci_row_used_it() -> None:
    for entry in ENTRIES:
        pins = _pins(entry["reference"]) if entry["reference"] else {}
        # A run on no single commit can't say which versions it used, so only a merged one counts.
        ran = any(
            item["kind"] == "real_run" and item["kit"] in MERGED_KITS
            for item in entry["layers"]["run"]["evidence"]
        )
        for version in entry["tested_versions"]:
            package, _, number = version.partition("==")
            assert package == entry["package"], (entry["key"], version)
            in_ci = version in SMOKE or (
                f"{package}==" in SMOKE and f'"{number}"' in SMOKE
            )
            # A pin is only what the reference asks for; it is tested once a run used it.
            assert in_ci or (ran and pins.get(package) == number), (
                entry["key"],
                version,
            )


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


def test_the_release_docs_gate_finds_nothing_wrong_with_the_list_s_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The gate runs each page's backing example; only its reading of the pages is under test here.
    monkeypatch.setattr(trinity, "_execute_docs_backing_fresh", lambda *_: None)
    status = trinity._release_docs_executability_status(REPO, [])
    ours = ("docs/frameworks/supported.md", "docs/frameworks/evidence/")
    for kind in ("metadata_errors", "claims_errors"):
        found = [e for e in status[kind] if str(e.get("path", "")).startswith(ours)]
        assert found == [], (kind, found)
    # The page is in the docs index, regenerated.
    assert status["index_errors"] == []
    assert "docs/frameworks/supported.md" in {page["path"] for page in status["pages"]}


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
