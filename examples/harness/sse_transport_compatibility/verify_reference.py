#!/usr/bin/env python3
"""Verify the pinned AG-UI CrewAI SSE reference and an optional source tree."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REFERENCE = ROOT / "ag_ui_crewai" / "reference.json"


def verify(source_root: Path | None = None) -> dict[str, object]:
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    source = reference["source"]
    qualification = reference["qualification"]
    assert reference["schema_version"] == "futureagi.sse-transport-reference.v1"
    assert re.fullmatch(r"[0-9a-f]{40}", source["commit"])
    assert source["repository"].startswith("https://github.com/")
    assert reference["runtime"]["endpoint"].startswith("/conversational_flows/")
    assert reference["runtime"]["environment"] == {
        "MODEL": "vertex_ai/gemini-3.7-flash",
        "VERTEXAI_LOCATION": "global",
    }
    assert reference["runtime"]["environment_names"] == [
        "GOOGLE_APPLICATION_CREDENTIALS",
        "VERTEXAI_PROJECT",
    ]
    assert reference["runtime"]["supplemental_dependencies"] == ["google-auth"]
    assert (
        reference["runtime"]["authentication"]
        == "google_application_credentials_file"
    )
    assert reference["request"]["runId"] == "{{execution_id}}-{{turn_index}}"
    assert reference["request"]["messages"] == [
        {
            "id": "user-{{execution_id}}-{{turn_index}}",
            "role": "user",
            "content": "{{new_message_content}}",
        }
    ]
    assert reference["sse"]["completion"]["values"] == ["RUN_FINISHED"]
    assert reference["sse"]["failure"]["values"] == ["RUN_ERROR"]
    assert reference["sse"]["text"] == [
        {
            "mode": "cumulative",
            "path": "data.messages.-1.content",
            "when": {
                "path": "data.messages.-1.role",
                "values": ["assistant"],
            },
        }
    ]
    assert qualification["requires_credentials"] is True
    assert qualification["tool_event_status"] == "pending"
    assert len(qualification["turns"]) == 2
    assert (ROOT / "qualify_reference.py").is_file()
    if source_root is not None:
        assert source_root.is_dir(), source_root
        for check in reference["source_checks"]:
            path = source_root / check["path"]
            assert path.is_file(), path
            assert check["contains"] in path.read_text(encoding="utf-8"), check
        provenance = json.loads(
            (source_root / ".futureagi-source.json").read_text(encoding="utf-8")
        )
        assert provenance["commit"] == source["commit"]
    return reference


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    reference = verify(args.source)
    suffix = " and source tree" if args.source else ""
    print(f"{reference['id']} reference{suffix} verified")


if __name__ == "__main__":
    main()
