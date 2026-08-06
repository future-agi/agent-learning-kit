"""Unit tests for Protect's metric_map contract.

Pins the canonical + legacy-alias set that must stay in sync between
the Python and TypeScript SDKs and match the templates the backend
accepts.
"""

import warnings

import pytest

from fi.evals.protect import Protect
from fi.evals.templates import (
    BiasDetection,
    DataPrivacyCompliance,
    PromptInjection,
    Toxicity,
)


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch):
    monkeypatch.setenv("FI_API_KEY", "test-key")
    monkeypatch.setenv("FI_SECRET_KEY", "test-secret")


def _client():
    return Protect()


CANONICAL = {
    "toxicity": Toxicity,
    "bias_detection": BiasDetection,
    "prompt_injection": PromptInjection,
    "data_privacy_compliance": DataPrivacyCompliance,
}
LEGACY_ALIASES = {
    "content_moderation": Toxicity,
    "security": PromptInjection,
    "Toxicity": Toxicity,
    "Sexism": BiasDetection,
    "Prompt Injection": PromptInjection,
    "Data Privacy": DataPrivacyCompliance,
}


@pytest.mark.parametrize("name,template", list(CANONICAL.items()))
def test_canonical_metric_resolves_to_expected_template(name, template):
    assert _client().metric_map[name] is template


@pytest.mark.parametrize("alias,template", list(LEGACY_ALIASES.items()))
def test_legacy_alias_resolves_to_same_template_as_canonical(alias, template):
    assert _client().metric_map[alias] is template


def test_tone_is_not_a_supported_metric():
    assert "Tone" not in _client().metric_map


def _swallowed_request(*args, **kwargs):
    # _check_rule_sync wraps the request in try/except and returns a
    # synthetic failed tuple, so the batch completes without raising.
    raise Exception("stubbed")


def test_legacy_alias_emits_future_warning(monkeypatch):
    client = _client()
    monkeypatch.setattr(client.evaluator, "request", _swallowed_request)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        client.protect(
            inputs="hello",
            protect_rules=[{"metric": "content_moderation"}],
        )
    assert any(
        issubclass(w.category, FutureWarning)
        and "content_moderation" in str(w.message)
        for w in caught
    ), "expected a FutureWarning for the deprecated alias"


def test_canonical_metric_does_not_emit_warning(monkeypatch):
    client = _client()
    monkeypatch.setattr(client.evaluator, "request", _swallowed_request)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        client.protect(
            inputs="hello",
            protect_rules=[{"metric": "toxicity"}],
        )
    assert not any(
        issubclass(w.category, FutureWarning) for w in caught
    ), "canonical metric must not raise a deprecation warning"
