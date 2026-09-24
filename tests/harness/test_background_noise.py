import json

import pytest

from fi.alk.harness.background_noise import _BUILTIN_BY_ENVIRONMENT, source_for

CLIPS = [
    {"environment": "street", "url": "https://clips.example/traffic.wav"},
    {"environment": "vehicle", "url": "https://clips.example/car.wav"},
    {"environment": "transit", "url": "https://clips.example/airport.wav"},
    {"environment": "transit", "url": "https://clips.example/station.wav"},
]


@pytest.fixture
def catalogue(monkeypatch):
    monkeypatch.setenv("ALK_BACKGROUND_NOISE_CATALOG", json.dumps(CLIPS))


def _heard(environment, seeds=range(40)):
    return {source_for(environment, seed=f"scenario-{seed}") for seed in seeds}


def test_a_named_place_draws_from_its_clips_and_its_builtin(catalogue):
    assert _heard("street") == {"https://clips.example/traffic.wav", "CITY_AMBIENCE"}
    assert _heard("transit") == {
        "https://clips.example/airport.wav",
        "https://clips.example/station.wav",
        "CITY_AMBIENCE",
    }


def test_an_unnamed_place_draws_from_every_bed(catalogue):
    heard = _heard("", seeds=range(200))
    assert {clip["url"] for clip in CLIPS} <= heard
    assert set(_BUILTIN_BY_ENVIRONMENT.values()) <= heard


def test_the_same_scenario_hears_the_same_bed(catalogue):
    assert source_for("transit", seed="late-night-pickup") == source_for("transit", seed="late-night-pickup")


def test_a_quiet_place_stays_silent(catalogue):
    assert source_for("quiet_line", seed="x") == ""


def test_without_a_catalogue_places_map_to_builtins(monkeypatch):
    monkeypatch.delenv("ALK_BACKGROUND_NOISE_CATALOG", raising=False)
    assert _heard("office") == {"OFFICE_AMBIENCE"}
    assert _heard("") <= set(_BUILTIN_BY_ENVIRONMENT.values())


def test_a_catalogue_file_path_is_read(tmp_path, monkeypatch):
    path = tmp_path / "clips.json"
    path.write_text(json.dumps(CLIPS[:1]), encoding="utf-8")
    monkeypatch.setenv("ALK_BACKGROUND_NOISE_CATALOG", str(path))
    assert "https://clips.example/traffic.wav" in _heard("street")


@pytest.mark.parametrize("raw", ["[not json", '{"environment": "street"}', "/no/such/file.json"])
def test_an_unreadable_catalogue_falls_back_to_builtins(monkeypatch, raw):
    monkeypatch.setenv("ALK_BACKGROUND_NOISE_CATALOG", raw)
    assert _heard("street") == {"CITY_AMBIENCE"}


def test_every_bed_is_levelled_to_the_office_clip():
    import asyncio

    from livekit.agents.voice.background_audio import BuiltinAudioClip

    from fi.simulate.simulation.engines.livekit import _bed_gain

    beds = [BuiltinAudioClip[name] for name in set(_BUILTIN_BY_ENVIRONMENT.values())]

    async def gains():
        return {clip.name: await _bed_gain(clip) for clip in beds}

    gains = asyncio.run(gains())
    assert gains.pop("OFFICE_AMBIENCE") == pytest.approx(1.0)
    assert all(0 < gain < 1 for gain in gains.values())


def test_an_unreadable_bed_keeps_its_volume(tmp_path):
    import asyncio

    from fi.simulate.simulation.engines.livekit import _bed_gain

    broken = tmp_path / "broken.wav"
    broken.write_bytes(b"not audio")
    assert asyncio.run(_bed_gain(str(broken))) == 1.0


def test_places_come_from_the_catalogue_and_the_builtins(catalogue):
    from fi.alk.harness.background_noise import places

    assert places() == {"crowd": 1, "office": 1, "outdoors": 1, "street": 2, "transit": 3, "vehicle": 2}


def test_without_a_catalogue_places_are_the_builtin_beds(monkeypatch):
    from fi.alk.harness.background_noise import places

    monkeypatch.delenv("ALK_BACKGROUND_NOISE_CATALOG", raising=False)
    assert set(places()) == {"crowd", "office", "outdoors", "street"}


def test_a_writer_brief_deals_the_places_it_can_play(catalogue):
    from fi.alk.harness.scenarios import callers_for

    brief = callers_for(0, 6)
    assert "transit (3)" in brief and "vehicle (2)" in brief
    assert "believable person" in brief and "celebrity" in brief


def test_a_voice_scenario_with_noise_on_is_given_a_place(catalogue, tmp_path):
    from fi.alk.harness.background_noise import place_for, places
    from fi.alk.harness.scenario import Scenario

    def derived(value, fixture=None):
        one = Scenario(name="caller", instruction="x", sub_goals=["a"], background_noise=value, fixture=fixture or {})
        return place_for(one.name, one.fixture) if one.background_noise is True else one.background_noise

    assert derived(True) in places()
    assert derived("street") == "street"
    assert derived(False) is False
    assert derived(True, {"environment": "vehicle", "origin": "seed"}) == "vehicle"


def test_a_brief_for_a_chat_agent_says_nothing_about_noise(catalogue):
    from fi.alk.harness.scenarios import callers_for

    assert "background_noise" in callers_for(0, 6)
    typed = callers_for(0, 6, spoken=False)
    assert "background_noise" not in typed and "accent" not in typed and "believable person" in typed


def test_a_caller_who_speaks_no_english_has_no_english_accent():
    from fi.alk.harness.scenario import Persona

    assert Persona(name="Paloma Reyes", languages=["Spanish"], accent="Canadian").accent == "Neutral"
    assert Persona(name="Rahul Varma", languages=["English", "Hindi"], accent="Indian").accent == "Indian"


def test_a_caller_speaks_one_language():
    from fi.alk.harness.scenario import Persona

    one = Persona(name="Rahul Varma", languages=["English", "Hindi"], accent="Indian", multilingual=True)
    assert one.languages == ["English"] and one.multilingual is False


def test_a_place_the_situation_describes_wins_over_a_pick_by_name(catalogue):
    from fi.alk.harness.background_noise import place_for

    assert place_for("any", None, "You are rushing through the airport to your gate.") == "transit"
    assert place_for("any", None, "You call from a busy street corner.") == "street"
    assert place_for("any", {"environment": "vehicle"}, "at the airport") == "vehicle"


def test_a_suite_keeps_quiet_lines_to_a_small_share() -> None:
    from fi.alk.harness.scenario import Scenario
    from fi.alk.harness.scenario_tools import _over_its_share

    grid = {"interface": ["quiet_line", "noisy_office", "noisy_street", "accented"]}
    quiet = [Scenario(name=f"s{i}", use_case="u", coverage={"interface": "quiet_line"}) for i in range(15)]
    assert "quiet line" in _over_its_share({"interface": "quiet_line"}, grid, quiet, 100)
    assert _over_its_share({"interface": "quiet_line"}, grid, quiet[:10], 100) == ""
    assert _over_its_share({"interface": "noisy_street"}, grid, quiet, 100) == ""

