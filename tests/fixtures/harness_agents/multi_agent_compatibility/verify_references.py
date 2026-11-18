#!/usr/bin/env python3
"""Verify pinned multi-agent references and optional materialized source trees."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent
EXPECTED_FRAMEWORKS = {"google_adk", "langgraph", "crewai"}
REQUIRED_EVIDENCE = {
    "stable parent and child IDs under one run ID",
    "delegation and return events for every handoff",
    "every tool call attributed to its worker",
    "separate tool permissions per worker",
    "independent results for each exercised worker and the whole task",
    "missing child evidence reported as unmeasured",
}
GOOGLE_TOP_LEVEL_WORKERS = {
    "inspiration_agent",
    "planning_agent",
    "booking_agent",
    "pre_trip_agent",
    "in_trip_agent",
    "post_trip_agent",
}
GOOGLE_ALL_WORKERS = GOOGLE_TOP_LEVEL_WORKERS | {
    "place_agent",
    "poi_agent",
    "flight_search_agent",
    "flight_seat_selection_agent",
    "hotel_search_agent",
    "hotel_room_selection_agent",
    "itinerary_agent",
    "create_reservation",
    "payment_choice",
    "process_payment",
    "what_to_pack_agent",
    "google_search_grounding",
    "trip_monitor_agent",
    "day_of_agent",
}


def load_references() -> list[dict[str, object]]:
    references = []
    for path in sorted(ROOT.glob("*/reference.json")):
        reference = json.loads(path.read_text(encoding="utf-8"))
        reference["_directory"] = path.parent.name
        references.append(reference)
    return references


def verify_reference(reference: dict[str, object]) -> None:
    source = reference["source"]
    framework = reference["framework"]
    topology = reference["topology"]
    coordinator = topology["coordinator"]
    workers = topology["workers"]
    worker_ids = {worker["id"] for worker in workers}

    assert reference["schema_version"] == "futureagi.multi-agent-reference.v1"
    assert re.fullmatch(r"[0-9a-f]{40}", source["commit"])
    assert source["repository"].startswith("https://github.com/")
    assert re.fullmatch(r"\d+\.\d+\.\d+", framework["version"])
    assert reference["runtime"]["command"]
    assert set(reference["required_evidence"]) == REQUIRED_EVIDENCE
    assert workers
    assert len(worker_ids) == len(workers)
    assert coordinator not in worker_ids

    adaptation = reference.get("adaptation")
    if adaptation:
        patch = ROOT / str(reference["_directory"]) / adaptation["patch"]
        assert hashlib.sha256(patch.read_bytes()).hexdigest() == adaptation["sha256"]

    valid_parents = worker_ids | {coordinator}
    assert all(worker["parent"] in valid_parents for worker in workers)
    assert all(worker["parent"] != worker["id"] for worker in workers)

    assert reference["scenarios"]
    for scenario in reference["scenarios"]:
        assert scenario["instruction"]
        assert scenario["expected_effects"]
        assert set(scenario["expected_workers"]) <= valid_parents


def verify_google_topology(reference: dict[str, object]) -> None:
    topology = reference["topology"]
    workers = topology["workers"]
    worker_ids = {worker["id"] for worker in workers}
    top_level = {
        worker["id"]
        for worker in workers
        if worker["parent"] == topology["coordinator"]
    }
    assert topology["coordinator"] == "root_agent"
    assert top_level == GOOGLE_TOP_LEVEL_WORKERS
    assert worker_ids == GOOGLE_ALL_WORKERS


def verify_source(reference: dict[str, object], source: Path) -> None:
    assert source.is_dir(), source
    for check in reference["source_checks"]:
        path = source / check["path"]
        assert path.is_file(), path
        assert check["contains"] in path.read_text(encoding="utf-8"), check


def verify(sources: dict[str, Path] | None = None) -> list[dict[str, object]]:
    references = load_references()
    by_framework = {
        reference["framework"]["name"]: reference for reference in references
    }
    assert set(by_framework) == EXPECTED_FRAMEWORKS

    for reference in references:
        verify_reference(reference)
        directory = ROOT / str(reference["_directory"])
        expected_files = {"reference.json"}
        if adaptation := reference.get("adaptation"):
            expected_files.add(adaptation["patch"])
        assert {path.name for path in directory.iterdir()} == expected_files
    verify_google_topology(by_framework["google_adk"])

    for reference_id, path in (sources or {}).items():
        by_id = {reference["id"]: reference for reference in references}
        assert reference_id in by_id, reference_id
        verify_source(by_id[reference_id], path)
    return references


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        metavar="REFERENCE_ID=PATH",
    )
    args = parser.parse_args()
    sources = {}
    for item in args.source:
        reference_id, separator, raw_path = item.partition("=")
        assert separator and reference_id and raw_path, item
        sources[reference_id] = Path(raw_path)

    references = verify(sources)
    suffix = f" and {len(sources)} source trees" if sources else ""
    print(f"{len(references)} multi-agent references{suffix} verified")


if __name__ == "__main__":
    main()
