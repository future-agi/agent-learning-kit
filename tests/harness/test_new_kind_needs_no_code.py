"""Adding support for a new kind of agent must be adding one file, not editing code."""

from __future__ import annotations

from pathlib import Path


def _kind(root: Path, name: str, modality: str, body: str) -> None:
    (root / "kinds").mkdir(parents=True, exist_ok=True)
    (root / "kinds" / f"{name}.md").write_text(
        f"---\nname: {name}\napplies_to: modality={modality}\n"
        f"description: what a {name} scenario has to account for.\n---\n\n{body}\n",
        encoding="utf-8",
    )


def test_a_kind_nobody_has_written_yet_reaches_the_writer(tmp_path, monkeypatch):
    from fi.alk.harness import config

    monkeypatch.setattr(config, "SKILLS_ROOT", tmp_path)
    _kind(tmp_path, "browser", "browser", "A browser agent is driven by clicking.")
    _kind(tmp_path, "voice", "voice", "A voice agent is reached by calling.")

    for_browser = config.discovered_skills(modality="browser")
    assert "driven by clicking" in for_browser
    assert "reached by calling" not in for_browser, "a voice file leaked into a browser run"

    for_voice = config.discovered_skills(modality="voice")
    assert "reached by calling" in for_voice
    assert "driven by clicking" not in for_voice


def test_a_kind_can_be_gated_on_a_second_condition_the_way_voicemail_is(tmp_path, monkeypatch):
    from fi.alk.harness import config

    monkeypatch.setattr(config, "SKILLS_ROOT", tmp_path)
    (tmp_path / "kinds").mkdir(parents=True)
    (tmp_path / "kinds" / "browser-upload.md").write_text(
        "---\nname: browser-upload\napplies_to: modality=browser,uploads=on\n"
        "description: files a page accepts.\n---\n\nA page that takes a file upload.\n",
        encoding="utf-8",
    )

    assert "takes a file upload" in config.discovered_skills(modality="browser", uploads="on")
    assert "takes a file upload" not in config.discovered_skills(modality="browser", uploads="off")
    assert "takes a file upload" not in config.discovered_skills(modality="browser")


def test_a_new_kind_file_is_enough_for_the_contract_to_accept_it(tmp_path, monkeypatch):
    from fi.alk.harness import config
    from fi.alk.harness.contract import known_modalities

    monkeypatch.setattr(config, "SKILLS_ROOT", tmp_path)
    _kind(tmp_path, "computer-use", "computer_use", "An agent that drives a desktop.")

    accepted = known_modalities()
    assert "computer_use" in accepted, "a kind file did not widen what a contract may declare"
    for built_in in ("voice", "chat", "browser"):
        assert built_in in accepted


def test_the_built_ins_survive_an_empty_kinds_directory(tmp_path, monkeypatch):
    from fi.alk.harness import config
    from fi.alk.harness.contract import MODALITIES, known_modalities

    monkeypatch.setattr(config, "SKILLS_ROOT", tmp_path)
    (tmp_path / "kinds").mkdir()

    assert set(known_modalities()) == set(MODALITIES)
