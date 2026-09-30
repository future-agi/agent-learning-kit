"""The skills only deal what the scenario gates can accept."""

from pathlib import Path

from fi.alk.harness import scenario
from fi.alk.harness.persona_guides import vocabulary

SKILLS = Path(scenario.__file__).parent / "skills"


def test_a_disfluent_level_is_dealt_only_when_an_offered_style_can_carry_it():
    styles = vocabulary().get("communication_style") or []
    deliverable = any(scenario._DISFLUENT_STYLE.search(style) for style in styles)
    table = (SKILLS / "kinds" / "voice.md").read_text()

    offered = any(f"| `{level}` |" in table for level in scenario._DISFLUENT_INTERFACE)

    assert offered == deliverable
