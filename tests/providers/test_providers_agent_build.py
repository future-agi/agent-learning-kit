from __future__ import annotations

import logging
from typing import Any

import pytest

from fi.alk.harness.provider_import import _safe_profile_value
from fi.alk.providers.agent import _redact, build_authored_agent

PLAIN_DOCUMENTS: list[Any] = [
    {"name": "Support line", "voice": {"voiceId": "marissa", "speed": 1.0}},
    {
        "api_key": "sk-live-1",
        "APIKey": "x",
        "Authorization": "Bearer y",
        "client_secret": 3,
    },
    {"credentials": {"user": "u"}, "tokens": ["a", "b"], "refresh_token": None},
    {"server": {"url": "https://user:pw@hooks.example.com:8443/call?sig=abc#frag"}},
    {"links": ["http://example.com/a?b=c", "not a url", "mailto:someone@example.com"]},
    {"nested": [{"deep": {"secretValue": "s"}}, 1, True, None, 2.5]},
    ["https://example.com/x?y=1", {"api_key": "k"}],
    "plain string",
    42,
]


@pytest.mark.parametrize("document", PLAIN_DOCUMENTS)
def test_redaction_equals_safe_profile_value_on_plain_documents(document: Any) -> None:
    assert _redact(document) == _safe_profile_value(document)


def test_added_markers_headers_and_parameters() -> None:
    document = {
        "Cookie": "session=abc",
        "passphrase": "correct horse",
        "x-api-key": "k-1",
        "private_key": "-----BEGIN-----",
        "PrivateKeyPath": "kept",
        "nested": {"Set-Cookie": "id=1", "keep": "me"},
        "headers": {"X-Trace": "t-1", "Accept": "application/json"},
        "server": {"headers": ["a", "b"], "url": "https://example.com/x"},
    }

    redacted = _redact(document)

    assert redacted["Cookie"] == "[redacted]"
    assert redacted["passphrase"] == "[redacted]"
    assert redacted["x-api-key"] == "[redacted]"
    assert redacted["private_key"] == "[redacted]"
    assert redacted["PrivateKeyPath"] == "kept"
    assert redacted["nested"] == {"Set-Cookie": "[redacted]", "keep": "me"}
    assert redacted["headers"] == {"X-Trace": "[redacted]", "Accept": "[redacted]"}
    assert redacted["server"] == {
        "headers": "[redacted]",
        "url": "https://example.com/x",
    }


@pytest.mark.parametrize(
    ("prose", "expected"),
    [
        (
            "Call https://u:hunter2@api.example.com/v1 for data.",
            "Call https://api.example.com/v1 for data.",
        ),
        (
            "Use https://api.example.com/v1?api_key=sk-123#frag always",
            "Use https://api.example.com/v1 always",
        ),
        ("line1\nhttps://u:p@h.example/x", "line1\nhttps://h.example/x"),
        ("See (https://u:p@h.example/x) first", "See (https://h.example/x) first"),
        (
            "a https://u:p@one.example/1 and postgres://db:pw@two.example:5432/app?ssl=1",
            "a https://one.example/1 and postgres://two.example:5432/app",
        ),
        # The host is lowercased, as it always was for a URL on its own.
        ("x\thttp://{HOST}:{PORT}/hook y", "x\thttp://{host}/hook y"),
        (
            '{"hook": "https://u:p@h.example/x?k=1", "next": 1}',
            '{"hook": "https://h.example/x", "next": 1}',
        ),
    ],
    ids=["password", "query", "after_newline", "in_brackets", "two_urls", "template", "quoted"],
)
def test_a_url_inside_prose_is_redacted_and_the_prose_kept(prose: str, expected: str) -> None:
    assert _redact(prose) == expected


def test_a_refused_url_inside_prose_becomes_redacted_and_the_rest_stays(
    caplog: pytest.LogCaptureFixture,
) -> None:
    prose = "Before https://admin:hunter2＃x@db.example/ after"

    assert _redact(prose) == "Before [redacted] after"
    assert "hunter2" not in caplog.text


@pytest.mark.parametrize(
    "text", ["no link here", "mailto:someone@example.com", "ratio 3:1 at 10:30", ""]
)
def test_a_string_without_a_url_is_unchanged(text: str) -> None:
    assert _redact(text) == text


def test_many_refused_urls_in_one_document_log_one_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A full-width number sign NFKC-normalises to "#", and "[" opens an IPv6 host that never
    # closes: urlsplit refuses both.
    leaky = "https://admin:hunter2\uff03x@db.example/"
    refused = [leaky, *(["a://["] * 1000)]
    document = {
        "agent": "support",
        "modality": "chat",
        "system_prompt_excerpt": leaky,
        "tools": [{"name": "add", "args": ["k"], "arg_values": {"k": refused}}],
    }

    with caplog.at_level(logging.DEBUG, logger="fi.alk.providers.agent"):
        agent = build_authored_agent(document)

    assert agent["behaviour"][0]["instructions"] == "[redacted]"
    assert agent["tools"][0]["parameters"]["properties"]["k"]["enum"] == ["[redacted]"] * 1001
    [logged] = [record for record in caplog.records if record.name == "fi.alk.providers.agent"]
    assert logged.getMessage() == "a URL was redacted whole: class=ValueError"
    assert logged.exc_info is None
    assert "hunter2" not in caplog.text
