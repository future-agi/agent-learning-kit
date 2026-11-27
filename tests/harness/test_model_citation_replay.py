"""Every reading below was checked against the model citation rules before they were last
changed, and each must get the same answer now: saved with the same name and setting, or
refused. A reading whose answer changed on purpose carries the answer it had before and why it
changed; nothing else may change. A change to the rules that moves another reading fails here
before it reaches anyone's save.

Each reading is one or more source files and the model a reader cited in them. A key-shaped
value is written in the fixture as {KEY_SHAPED} and built here, so no file holds one."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fi.alk.harness.contract import AgentContract
from fi.alk.harness.model_citation import model_problems
from fi.alk.harness.runtime_selection import path_lookup_budget

READINGS = json.loads(
    (Path(__file__).parent / "fixtures" / "model_citation_replay.json").read_text(
        encoding="utf-8"
    )
)
KEY_SHAPED = "AKIA" + "Q" * 16


def _filled(value):
    if isinstance(value, str):
        return value.replace("{KEY_SHAPED}", KEY_SHAPED)
    if isinstance(value, dict):
        return {key: _filled(one) for key, one in value.items()}
    return value


def _answer(tmp_path: Path, reading: dict) -> dict:
    root = tmp_path / "src"
    root.mkdir()
    for name, text in _filled(reading["files"]).items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    contract = AgentContract.model_validate(
        {
            "agent": "pair",
            "modality": "chat",
            "real_use_cases": ["order"],
            "tools": [{"name": "add", "args": ["item_id"]}],
            "model": _filled(reading["model"]),
        }
    )
    with path_lookup_budget():
        problems = model_problems(contract, root)
    if problems:
        return {"refused": True}
    return {
        "refused": False,
        "name": contract.model.name,
        "setting": contract.model.setting or "",
    }


@pytest.mark.parametrize(
    "reading", READINGS, ids=[reading["id"] for reading in READINGS]
)
def test_a_reading_gets_the_answer_it_got_before(tmp_path, reading):
    assert _answer(tmp_path, reading) == reading["expected"], reading.get("why", "")


def test_every_changed_answer_says_what_it_was_and_why():
    changed = [
        reading
        for reading in READINGS
        if reading["expected"] != reading.get("earlier", reading["expected"])
    ]
    assert all(reading.get("why") for reading in changed)
    assert all("earlier" in reading for reading in READINGS if "why" in reading)
    # Only these readings changed on purpose; a new one needs its own entry and reason.
    assert len(changed) == 29
    assert len(READINGS) == 155
