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
