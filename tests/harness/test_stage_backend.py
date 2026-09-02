"""A stage may name its own backend and model.

Stages are not alike. Reading an unfamiliar codebase and writing five hundred scenarios reward
different models, and a provider counts its rate limit per model, so pinning the expensive stage
to one and the voluminous stage to another is both a quality and a throughput decision. Measured:
one backend wrote every scenario of a 507-suite while the other wrote none, and the only way to
act on that was to switch the whole run.
"""

from __future__ import annotations

from fi.alk.harness.config import stage_backend, stage_model


class TestNamingOneStage:
    def test_a_stage_takes_the_name_given_to_it(self, monkeypatch):
        monkeypatch.setenv("ALK_SCENARIOS_HARNESS", "vertex-gemini")
        monkeypatch.setenv("ALK_SCENARIOS_MODEL", "gemini-3.7-flash")

        assert stage_backend("scenarios/write") == "vertex-gemini"
        assert stage_model("scenarios/write") == "gemini-3.7-flash"

    def test_naming_one_stage_leaves_the_others_alone(self, monkeypatch):
        """The point of the split: understand and build stay where the run put them."""
        monkeypatch.setenv("ALK_SCENARIOS_HARNESS", "vertex-gemini")

        assert stage_backend("understand-agent") is None
        assert stage_backend("build-environment") is None

    def test_nothing_named_means_the_run_decides(self, monkeypatch):
        monkeypatch.delenv("ALK_SCENARIOS_HARNESS", raising=False)
        monkeypatch.delenv("ALK_SCENARIOS_MODEL", raising=False)

        assert stage_backend("scenarios/write") is None
        assert stage_model("scenarios/write") is None

    def test_an_empty_setting_is_the_same_as_unset(self, monkeypatch):
        """An exported-but-blank variable must not resolve to a backend named ''."""
        monkeypatch.setenv("ALK_SCENARIOS_HARNESS", "  ")

        assert stage_backend("scenarios/write") is None

    def test_a_sub_skill_is_scoped_to_its_stage(self, monkeypatch):
        """`scenarios/write` and `scenarios/plan` are one stage and share one setting."""
        monkeypatch.setenv("ALK_SCENARIOS_MODEL", "gemini-3.7-flash")

        assert stage_model("scenarios/plan") == "gemini-3.7-flash"
        assert stage_model("scenarios/write") == "gemini-3.7-flash"


class TestTellingTheParentWhatWorkersItHas:
    """A declared worker is reached through the SDK's sub-agent tool, which asks which kind to
    run. Nothing otherwise names ours, and left to guess the model dispatched the generic kind:
    that runs detached, holds none of the stage's tools, and its work is lost when the parent
    finishes its turn. Measured: eight slices dealt, eight agents dispatched, nothing written.
    """

    def spec(self, with_workers: bool):
        from fi.alk.harness.backends import SessionSpec, WorkerSpec

        return SessionSpec(
            system_prompt="the method",
            workers=(
                {"scenario_writer": WorkerSpec(description="writes a slice", instructions="go")}
                if with_workers
                else {}
            ),
            gated=False,
        )

    def prompt_for(self, spec):
        from fi.alk.harness.backends.claude import ClaudeBackend

        return ClaudeBackend().create(spec)._options.system_prompt

    def test_the_worker_is_named(self):
        said = self.prompt_for(self.spec(with_workers=True))
        assert "scenario_writer" in said
        assert "the method" in said, "the stage's own method must survive"

    def test_the_generic_kind_is_warned_against(self):
        said = self.prompt_for(self.spec(with_workers=True)).lower()
        assert "general-purpose" in said and "detached" in said

    def test_a_stage_with_no_workers_is_told_nothing_extra(self):
        assert self.prompt_for(self.spec(with_workers=False)) == "the method"


class TestABackendIsCheckedAgainstTheModelItWillDrive:
    """A stage that names its own backend names its own model with it. Checking that pair against
    the run's global model instead rejected the exact combination the setting exists to express:
    global claude, scenarios on gemini, refused as "vertex-gemini cannot drive claude-sonnet-4-6".
    """

    def test_a_stage_model_is_what_gets_checked(self, monkeypatch):
        from fi.alk.harness.backends import resolve

        monkeypatch.setenv("ALK_HARNESS_MODEL", "claude-sonnet-4-6")

        assert resolve("vertex-gemini", "gemini-3.7-flash").name == "vertex-gemini"

    def test_a_genuine_mismatch_is_still_refused(self, monkeypatch):
        import pytest

        from fi.alk.harness.backends import resolve

        monkeypatch.delenv("ALK_HARNESS_MODEL", raising=False)
        with pytest.raises(ValueError, match="cannot drive"):
            resolve("vertex-gemini", "claude-sonnet-4-6")

    def test_with_no_model_named_the_run_s_own_still_applies(self, monkeypatch):
        import pytest

        from fi.alk.harness.backends import resolve

        monkeypatch.setenv("ALK_HARNESS_MODEL", "claude-sonnet-4-6")
        with pytest.raises(ValueError, match="cannot drive"):
            resolve("vertex-gemini")


class TestNoiseReachesTheRightPlace:
    """Whoever writes a scenario picks the word for where its caller is, so the map cannot be a
    closed list. A suite measured here used `city`, `airport` and `hotel`, none of them mapped,
    and every one fell through to an office.
    """

    def source(self, environment, monkeypatch):
        from fi.alk.harness.background_noise import scenario_source

        monkeypatch.setenv("ALK_BACKGROUND_NOISE", "1")
        return scenario_source(environment, {}, seed="s")

    def test_the_environments_a_real_suite_used(self, monkeypatch):
        got = {v: self.source(v, monkeypatch) for v in ("city", "airport", "hotel", "street")}
        assert got["city"] == "CITY_AMBIENCE"
        assert got["airport"] == "CROWDED_ROOM"
        assert got["hotel"] == "CROWDED_ROOM"
        assert got["street"] == "CITY_AMBIENCE"

    def test_a_phrase_matches_on_its_words(self, monkeypatch):
        assert self.source("in a moving vehicle", monkeypatch) == "CITY_AMBIENCE"
        assert self.source("busy cafe", monkeypatch) == "CROWDED_ROOM"

    def test_a_scenario_that_asked_for_none_stays_silent(self, monkeypatch):
        assert self.source(False, monkeypatch) == ""

    def test_nothing_plays_when_the_run_did_not_opt_in(self, monkeypatch):
        from fi.alk.harness.background_noise import scenario_source

        monkeypatch.delenv("ALK_BACKGROUND_NOISE", raising=False)
        assert scenario_source("street", {}, seed="s") == ""


def test_a_hosted_run_may_receive_the_per_stage_names():
    """The sandbox only receives names on a closed allow-list. Without these two a hosted job
    silently ignores the split and runs every stage on the run's backend, with no sign it was
    dropped: the setting appears to work locally and does nothing where it matters."""
    from fi.alk.harness.hosted_entrypoint import _SIMULATOR_SECRET_ALIASES

    assert {"ALK_SCENARIOS_HARNESS", "ALK_SCENARIOS_MODEL"} <= _SIMULATOR_SECRET_ALIASES
    assert {"ALK_HARNESS", "ALK_HARNESS_MODEL"} <= _SIMULATOR_SECRET_ALIASES
