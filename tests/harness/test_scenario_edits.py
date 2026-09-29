"""A run plays the platform's current edits over the sealed scenario document."""

from fi.alk.harness.call_runner import _with_scenario_edits

SEALED = {
    "scenario_key": "book-ride",
    "instruction": "book a ride",
    "background_noise": "office",
    "max_turns": 10,
    "persona": {"name": "Asha", "accent": "Indian", "personality": "calm"},
}


def test_edits_replace_only_editable_fields():
    edits = {
        "scenario_edits": {
            "book-ride": {
                "background_noise": "street",
                "max_turns": 6,
                "instruction": "ignored",
                "persona": {"accent": "British", "name": "ignored"},
            }
        }
    }

    merged = _with_scenario_edits(SEALED, edits, "book-ride")

    assert merged["background_noise"] == "street"
    assert merged["max_turns"] == 6
    assert merged["instruction"] == "book a ride"
    assert merged["persona"] == {"name": "Asha", "accent": "British", "personality": "calm"}
    assert SEALED["background_noise"] == "office"


def test_no_edits_leave_the_sealed_document():
    for metadata in (None, {}, {"scenario_edits": {}}, {"scenario_edits": {"other": {}}}):
        assert _with_scenario_edits(SEALED, metadata, "book-ride") == SEALED
