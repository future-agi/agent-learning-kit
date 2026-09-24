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
