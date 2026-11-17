"""`_redact(..., keep_scalars=True)` and `redact_strings`: the two cleaning treatments that
sit beside the default key-masking `_redact`."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from fi.alk.providers import agent

_REDACTED = "[redacted]"


# ------------------------------------------------------------------ _redact(keep_scalars=True)
def test_the_default_masks_every_number_under_a_credential_name() -> None:
    value = {"input_tokens": 5, "usage": {"output_tokens": 3}, "totalTokens": 900}
    assert agent._redact(value) == {
        "input_tokens": _REDACTED,
        "usage": {"output_tokens": _REDACTED},
        "totalTokens": _REDACTED,
    }


@pytest.mark.parametrize(
    "value",
    [
        {"input_tokens": 5},
        {"totalTokens": 900},
        {"TOTAL_TOKENS": 12},
        {"cache_read_input_tokens": 0},
        {"tokens": 7},
        {"a": {"b": {"output_tokens": 1.5}}},
    ],
)
def test_keep_scalars_keeps_a_number_under_a_key_ending_in_tokens(
    value: dict[str, Any],
) -> None:
    assert agent._redact(value, keep_scalars=True) == value


@pytest.mark.parametrize(
    "usage_key",
    ["usage", "modelUsage", "USAGE", "modelusage"],
)
def test_keep_scalars_keeps_every_number_inside_a_usage_dict(usage_key: str) -> None:
    # "secret_count" and "cookie_hits" carry a credential-sounding name, but sit in usage.
    value = {
        usage_key: {
            "secret_count": 4,
            "cookie_hits": 2,
            "claude-x": {"token_budget": 9, "nested": [{"api_key_uses": 1}]},
        }
    }
    assert agent._redact(value, keep_scalars=True) == value


@pytest.mark.parametrize(
    "value",
    [
        {"password": 12345678},
        {"token": 483920},
        {"api_key": 1234567890123},
        {"passphrase": 20240611},
        {"account": {"pin_secret": 4821}},
        {"credentials": 7},
        {"otp_token": 482913},
    ],
)
def test_keep_scalars_still_masks_any_other_number_under_a_credential_name(
    value: dict[str, Any],
) -> None:
    out = agent._redact(value, keep_scalars=True)
    assert _REDACTED in repr(out)
    for number in _numbers(value):
        assert number not in _numbers(out)


def test_keep_scalars_does_not_extend_the_usage_exemption_to_siblings() -> None:
    value = {"usage": {"input_tokens": 5}, "password": 12345678}
    assert agent._redact(value, keep_scalars=True) == {
        "usage": {"input_tokens": 5},
        "password": _REDACTED,
    }


def test_keep_scalars_masks_a_string_under_a_tokens_key() -> None:
    # Only a number is a count. A string under "tokens" can be a real token.
    assert agent._redact({"input_tokens": "sk-abc123"}, keep_scalars=True) == {
        "input_tokens": _REDACTED
    }


def test_keep_scalars_masks_a_list_or_dict_under_a_tokens_key() -> None:
    value = {"tokens": [11112222, 33334444], "access_tokens": {"a": 1}}
    assert agent._redact(value, keep_scalars=True) == {
        "tokens": _REDACTED,
        "access_tokens": _REDACTED,
    }


def test_keep_scalars_does_not_change_how_strings_are_cleaned() -> None:
    value = {"note": "see https://u:pw@h.example/x?a=1", "headers": {"X-A": "b"}}
    assert agent._redact(value, keep_scalars=True) == agent._redact(value)


def _numbers(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return [n for item in value.values() for n in _numbers(item)]
    if isinstance(value, list):
        return [n for item in value for n in _numbers(item)]
    return (
        [value]
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else []
    )


# --------------------------------------------------------------------------- redact_strings
def test_redact_strings_keeps_a_key_with_a_credential_name_and_cleans_text_only() -> (
    None
):
    value = {"password": {"note": "go to https://u:pw@h.example/x", "plain": "hello"}}
    assert agent.redact_strings(value) == {
        "password": {"note": "go to https://h.example/x", "plain": "hello"}
    }


def test_redact_strings_does_not_mask_a_string_by_its_key() -> None:
    value = {"token": "abc", "secret": "blue-sky", "headers": {"X-A": "b"}}
    assert agent.redact_strings(value) == value


def test_redact_strings_cleans_a_url_password_and_a_key_shape() -> None:
    out = agent.redact_strings(
        {"a": ["https://admin:hunter2@h.example/p", "key AKIAIOSFODNN7EXAMPLE end"]}
    )
    flat = repr(out)
    assert "hunter2" not in flat
    assert "AKIAIOSFODNN7EXAMPLE" not in flat


def test_redact_strings_cleans_a_named_value_in_prose() -> None:
    assert agent.redact_strings("set password=hunter2x now") == (
        "set password=[redacted] now"
    )


@pytest.mark.parametrize("leaf", [0, 7, 12345678, 1.5, True, False, None])
def test_redact_strings_passes_numbers_booleans_and_none_through(leaf: Any) -> None:
    value = {"password": leaf, "list": [leaf], "x": {"token": leaf}}
    out = agent.redact_strings(value)
    assert out == value
    assert out["password"] is leaf


def test_redact_strings_keeps_the_keys_exactly() -> None:
    value = {"Password": "a", 3: "b", "n": {"": "c"}}
    assert list(agent.redact_strings(value)) == ["Password", 3, "n"]


def test_redact_strings_returns_a_new_structure_and_leaves_the_input_alone() -> None:
    value = {"a": ["https://u:pw@h.example/x"]}
    out = agent.redact_strings(value)
    assert value == {"a": ["https://u:pw@h.example/x"]}
    assert out is not value


def test_redact_strings_logs_one_line_for_the_whole_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    value = {"a": "https://u:pw@h.example/x", "b": ["password=hunter2x", "ok"]}
    with caplog.at_level(logging.WARNING, logger=agent.logger.name):
        agent.redact_strings(value)
    lines = [r for r in caplog.records if r.getMessage().startswith("secrets redacted")]
    assert len(lines) == 1
    assert "hunter2" not in lines[0].getMessage()


def test_redact_strings_logs_nothing_for_clean_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=agent.logger.name):
        agent.redact_strings({"a": "hello", "b": [1, None]})
    assert not caplog.records
