from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fi.alk.harness.contract import validate_contract
from fi.alk.harness.sources import resolve
from fi.alk.harness.understand import load, open_stage


def _payload() -> dict:
    return {
        "agent": "front-desk",
        "modality": "voice",
        "one_liner": "Books hotel rooms over the phone.",
        "real_use_cases": ["book a room", "cancel a reservation"],
        "hard_constraints": ["Confirm the dates before booking."],
        "tools": [
            {
                "name": "check_availability",
                "args": ["check_in", "check_out"],
                "tables_read": ["rooms", "reservations"],
            },
            {
                "name": "book_room",
                "args": ["room_type", "guest_name"],
                "tables_read": ["rooms"],
                "tables_changed": ["reservations"],
            },
        ],
        "data_schema": {
            "rooms": {"room_id": "string", "nightly_rate": "integer"},
            "reservations": {"confirmation_number": "string", "status": "string"},
        },
        "base_environment": {
            "rooms": [{"room_id": "101", "nightly_rate": 150}],
            "reservations": [{"confirmation_number": "HH-1001", "status": "confirmed"}],
        },
    }


def _submit(stage, destination: Path) -> None:
    server = stage.spec.servers["contract"]
    for _ in range(3):
        asyncio.run(server.tools[0].handler(_payload()))
        if (destination / "contract.json").exists():
            return


def _saved(destination: Path) -> dict:
    return json.loads((destination / "contract.json").read_text())


def test_a_hosted_reading_saves_no_local_world(tmp_path):
    source = resolve(
        "provider",
        name="front-desk",
        profile={"system_prompt": "You book rooms.", "tools": []},
        scratch=str(tmp_path / "scratch"),
    )
    stage, destination = open_stage(source, out=tmp_path / "out")

    _submit(stage, destination)

    saved = _saved(destination)
    assert saved["data_schema"] == {}
    assert saved["base_environment"] == {}
    assert [tool["name"] for tool in saved["tools"]] == [
        "check_availability",
        "book_room",
    ]
    assert all(
        tool["tables_read"] == [] and tool["tables_changed"] == []
        for tool in saved["tools"]
    )
    assert saved["hard_constraints"] == ["Confirm the dates before booking."]
    assert validate_contract(load(destination)) == []


def test_a_repository_reading_keeps_its_data(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "agent.py").write_text("x = 1\n")
    source = resolve("repo", name="front-desk", root=str(root))
    stage, destination = open_stage(source, out=tmp_path / "out")

    _submit(stage, destination)

    saved = _saved(destination)
    assert set(saved["data_schema"]) == {"rooms", "reservations"}
    assert set(saved["base_environment"]) == {"rooms", "reservations"}
    assert saved["tools"][1]["tables_changed"] == ["reservations"]
