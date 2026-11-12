from __future__ import annotations

import logging
import random
import re
import string
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlsplit

import pytest
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st

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
        # A URL runs to the next whitespace, so the query takes the closing quote and comma
        # with it: JSON quoted in prose stops being valid JSON (plan v5.1 §V9, design page).
        (
            '{"hook": "https://u:p@h.example/x?k=1", "next": 1}',
            '{"hook": "https://h.example/x "next": 1}',
        ),
    ],
    ids=["password", "query", "after_newline", "in_brackets", "two_urls", "template", "quoted"],
)
def test_a_url_inside_prose_is_redacted_and_the_prose_kept(prose: str, expected: str) -> None:
    assert _redact(prose) == expected


def test_a_refused_url_inside_prose_becomes_redacted_and_the_rest_stays(
    caplog: pytest.LogCaptureFixture,
) -> None:
    prose = "Before https://admin:hunter2＃x@db.example/ and https://a:hunter2＃y@db.example/ after"

    with caplog.at_level(logging.DEBUG, logger="fi.alk.providers.agent"):
        assert _redact(prose) == "Before [redacted] and [redacted] after"

    # One line for the call, however many URLs in it were refused (#271).
    [logged] = [record for record in caplog.records if record.name == "fi.alk.providers.agent"]
    assert logged.getMessage() == "secrets redacted: url=ValueError"
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
    assert logged.getMessage() == "secrets redacted: url=ValueError"
    assert logged.exc_info is None
    assert "hunter2" not in caplog.text


def test_the_log_line_names_the_url_class_and_the_kinds_once_per_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """TH-9500 (plan v1.1 section 2, S6): one line per call, naming what was removed, never
    the text."""
    document = {
        "a": "password=Zq8wYv3Lp9",
        "b": ["https://u:p@h/x?k=1", "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"],
        "c": "Before https://admin:hunter2\uff03x@db.example/ after",
    }

    with caplog.at_level(logging.DEBUG, logger="fi.alk.providers.agent"):
        _redact(document)

    [logged] = [
        record for record in caplog.records if record.name == "fi.alk.providers.agent"
    ]
    assert logged.getMessage() == "secrets redacted: url=ValueError; kinds=github,named"
    assert "Zq8wYv3Lp9" not in caplog.text and "hunter2" not in caplog.text


def test_the_log_line_names_the_kinds_alone_when_no_url_was_refused(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger="fi.alk.providers.agent"):
        assert _redact("see https://bot:hun ter2@h/v1") == "see https://h/v1"

    assert [
        r.getMessage() for r in caplog.records if r.name == "fi.alk.providers.agent"
    ] == ["secrets redacted: kinds=rule1"]


@pytest.mark.parametrize(
    "text",
    [
        "password: required",
        "see https://h.example/x?k=1",
        "password=[redacted]",
        "no secret",
    ],
)
def test_nothing_is_logged_when_no_secret_rule_changed_the_text(
    caplog: pytest.LogCaptureFixture, text: str
) -> None:
    with caplog.at_level(logging.DEBUG, logger="fi.alk.providers.agent"):
        _redact(text)

    assert [r for r in caplog.records if r.name == "fi.alk.providers.agent"] == []


# -- TH-9491: a URL inside prose (plan v5.1) -------------------------------------------------------

# No shrinking: each assertion names its input, and a planted bug then fails in seconds.
_FAST = (Phase.explicit, Phase.generate)
_PROPERTY = settings(
    max_examples=400,
    derandomize=True,
    deadline=None,
    phases=_FAST,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
_UNRESERVED = string.ascii_letters + string.digits + "-._~"
_SUB_DELIMS = "!$&'()*+,;="
_QUOTES = "'\"<>`"
_NON_ASCII = "üé数据？＃"


def _frozen_base_url(value: str) -> str:
    """The base `_redact` string branch for a value that is one URL, frozen here.

    `_safe_profile_value` can't be the oracle: it raises on a non-numeric port. The copy carries
    the plan's named changes from the base (V3, W2): an IPv6 host keeps its brackets, a URL with
    a scheme but no host keeps its path and loses its query and fragment, and the result is built
    by hand, so a scheme urllib doesn't know keeps its //.
    """
    if "://" not in value:
        return value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "[redacted]"
    if not parsed.scheme:
        return value
    if not parsed.netloc:
        return f"{parsed.scheme}://{parsed.path}"
    hostname = parsed.hostname or ""
    if ":" in hostname:
        hostname = f"[{hostname}]"
    try:
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError:
        port = ""
    return f"{parsed.scheme}://{hostname}{port}{parsed.path}"


def _text(alphabet: str, *, min_size: int = 0, max_size: int = 8) -> st.SearchStrategy[str]:
    return st.text(st.sampled_from(alphabet), min_size=min_size, max_size=max_size)


@st.composite
def _lone_urls(draw: Any) -> str:
    """One URL, RFC 3986 shaped, with tabs and line breaks put somewhere after its ://."""
    scheme = draw(st.sampled_from(["https", "http", "postgres", "redis", "myapp", "git+ssh"]))
    wide = _UNRESERVED + _SUB_DELIMS + _QUOTES + "[]%" + _NON_ASCII
    userinfo = draw(
        st.one_of(
            st.just(""),
            st.builds(
                lambda user, password: f"{user}:{password}@",
                _text(_UNRESERVED, min_size=1),
                _text(wide, min_size=1),
            ),
        )
    )
    host = draw(st.one_of(_text(string.ascii_lowercase, min_size=1), st.just("[fd00::5]")))
    port = draw(st.sampled_from(["", ":8443", ":{PORT}", ":0"]))
    path = draw(_text(wide + "/:@", max_size=10))
    query = draw(st.one_of(st.just(""), _text(wide + "/?:@=&", max_size=10).map("?".__add__)))
    fragment = draw(st.one_of(st.just(""), _text(_UNRESERVED, max_size=4).map("#".__add__)))
    url = f"{scheme}://{userinfo}{host}{port}{('/' + path) if path else ''}{query}{fragment}"
    head = len(scheme) + 3
    for _ in range(draw(st.integers(0, 3))):
        at = draw(st.integers(head, len(url)))
        url = url[:at] + draw(st.sampled_from(["\t", "\n", "\r\n"])) + url[at:]
    return url


@_PROPERTY
@given(_lone_urls())
def test_a_single_url_field_is_cleaned_as_the_base_cleans_it(url: str) -> None:
    """B5: a single-URL field, after W1's join, gives the frozen base's result. TH-9500's rule 1
    first removes a userinfo whose text after "user:" a tab or a line break splits, which is
    what the base's join used to read as one password. The oracle runs `leftovers`, so it can't
    see a rule 1b condition that fires wrongly or not at all; the rows in
    test_secret_shapes.py (R1B_CLEANED, R1B_KEPT, R1B_LIMITS) pin those (cr2 L4)."""
    from fi.alk.providers.agent import _START
    from fi.alk.providers.secret_shapes import leftovers

    joined = url.replace("\t", "").replace("\r", "").replace("\n", "")
    if len(_START.findall(joined)) != 1:
        return
    # A one-URL value runs step 1 on its joined token (v2.5.2, M-A), as chat does.
    assert _redact(url) == _frozen_base_url(leftovers(joined)), url
    assert _only_userinfo_and_query_values_taken(joined, leftovers(joined)), url


_QUERY_VALUE = re.compile(r"[^&#\s]*")


def _only_userinfo_and_query_values_taken(url: str, left: str) -> bool:
    """What `leftovers` may take from a lone URL, checked without it (cr1 L1): the oracle above
    runs `leftovers` itself, so a rule 1 bug moves both sides alike. Rule 1 takes the userinfo
    right after the ://, up to and including its one @; rule 2 takes whole query values, each
    after an =. Rule 1b takes the userinfo up to the first path segment's @. Nothing else
    changes. It bounds what may go, not when: a 1b condition that fires wrongly or not at all
    passes it, and the rows in test_secret_shapes.py pin those (cr2 L4)."""
    head = url.index("://") + 3
    if left[:head] != url[:head]:
        return False
    rests = [url[head:]]
    at = url.find("@", head)
    if at >= 0:
        rests.append(url[at + 1 :])
    slash = url.find("/", head)
    path_at = url.find("@", slash) if slash >= 0 else -1
    if path_at >= 0:
        rests.append(url[path_at + 1 :])
    return any(_values_taken(rest, left[head:]) for rest in rests)


def _values_taken(full: str, left: str) -> bool:
    """Whether `left` is `full` less whole query values. A value taken is the whole run after
    its =, so the next character left is one that ends a value, never the value's first."""
    at = kept = 0
    while at < len(full):
        if at and full[at - 1] == "=":
            value = _QUERY_VALUE.match(full, at).group()
            if value and not left.startswith(value, kept):
                at += len(value)
                continue
        if kept >= len(left) or full[at] != left[kept]:
            return False
        at += 1
        kept += 1
    return kept == len(left)


# R's adversarial lone URLs (change review r1, b5.py): hosts that are names, ports that read as
# values, userinfo split by breaks, rule 2's query on the next line, and brackets.
_ADVERSARIAL_URLS = [
    f"https://{userinfo}{host}{port}{path}"
    for userinfo in ("", "u:p@")
    for host in ("token", "secret", "password", "a")
    for port in (":8443", ":0", ":{PORT}", "")
    for path in ("/a1", "")
] + [
    "https://u:pa\nss@h/x",
    "https://u:pa\tss@h/x",
    "https://u:p[a\nb@h/x",
    "https://u:a]b\n c@h/x",
    "https://u:12\nab@h",
    "https://u:{P}\nab@h",
    "https://u:ab\r\ncd@h/x?y=1",
    "https://u:ab\ncd@token:8443/a1",
    "redis://:pw\nx@cache:6379/0",
    "https://u:p@h/token=a1b2",
    "https://h/x\n?a=1",
    "https://h/x\n&a=1/b",
    "https://u:p@h/x\n&a=1/b",
    "https://u:x\ny@h/a\tb",
    "https://u:p@[fd00::5]:8443/x",
]


def test_leftovers_takes_only_a_userinfo_and_query_values_from_a_lone_url() -> None:
    """cr1 L1, beside B5 and without hypothesis: rule 1 and rule 2 checked independently."""
    from fi.alk.providers.secret_shapes import leftovers

    taken = {url: leftovers(url) for url in _ADVERSARIAL_URLS}

    assert {
        url: left
        for url, left in taken.items()
        if not _only_userinfo_and_query_values_taken(url, left)
    } == {}
    # Each rule takes something here, so the check is not passing on untouched input.
    assert taken["https://u:pa\nss@h/x"] == "https://h/x"
    assert taken["https://h/x\n?a=1"] == "https://h/x\n?a="


@st.composite
def _secret_urls(draw: Any, non_ascii: bool) -> tuple[str, str, str]:
    """A URL with a password and a key, each between markers no honest text holds."""
    extra = _NON_ASCII if non_ascii else ""
    password = "PWz" + draw(_text(_UNRESERVED + _SUB_DELIMS + _QUOTES + "%" + extra)) + "zQ"
    key = "SKz" + draw(_text(_UNRESERVED + _SUB_DELIMS + _QUOTES + "/?" + extra)) + "zK"
    user = draw(_text(_UNRESERVED, min_size=1, max_size=4))
    host = draw(_text(string.ascii_lowercase, min_size=1, max_size=6)) + ".example"
    port = draw(st.sampled_from(["", ":8443", ":{PORT}"]))
    # The path is kept (D2), so it never holds a marker's letters.
    plain = "".join(c for c in _UNRESERVED + _SUB_DELIMS + _QUOTES + ":@" + extra if c not in "PQSK")
    path = draw(_text(plain, max_size=6))
    query = draw(st.sampled_from([f"?key={key}", f"?a=1&key={key}", f"?next=/x&key={key}"]))
    fragment = draw(st.sampled_from(["", "#Frag"]))
    return f"https://{user}:{password}@{host}{port}/{path}{query}{fragment}", password, key


_WRAPPERS = [
    "{u}",
    "Post to {u} after.",
    "({u})",
    "'{u}'",
    '"{u}"',
    "[x]({u}).",
    "a\n{u}\nb",
    '{{"k": "{u}"}}',
    "get('{u}', t=3)",
    "请{u}然后",
    "go {u}`next` z",
    "x {u},postgres://u:PWzpwzQ@b/db y",
    # V1: text glued before the scheme, and a URL nested in a path.
    "1.{u}",
    "-{u}",
    "...{u}",
    "步骤2{u}",
    "#42{u}",
    "see https://h.example/a/2{u} ok",
]


def _leaked(out: str, password: str, key: str) -> bool:
    return any(marker in out for marker in (password[:3], password[-2:], key[:3], key[-2:]))


@pytest.mark.parametrize("non_ascii", [False, True], ids=["ascii", "non_ascii"])
@pytest.mark.parametrize("wrapper", _WRAPPERS)
def test_no_password_or_query_value_survives_in_any_wrapper(wrapper: str, non_ascii: bool) -> None:
    """B8, as a property over each wrapper."""

    @settings(max_examples=150, derandomize=True, deadline=None, phases=_FAST)
    @given(_secret_urls(non_ascii))
    def check(drawn: tuple[str, str, str]) -> None:
        url, password, key = drawn
        out = _redact(wrapper.format(u=url))
        assert not _leaked(out, password, key), (url, out)

    check()


@pytest.mark.parametrize("non_ascii", [False, True], ids=["ascii", "non_ascii"])
def test_no_password_or_query_value_survives_without_its_scheme(non_ascii: bool) -> None:
    """B8, V1: a scheme given by a template (`${S}://…`) starts a URL too."""

    @settings(max_examples=150, derandomize=True, deadline=None, phases=_FAST)
    @given(_secret_urls(non_ascii))
    def check(drawn: tuple[str, str, str]) -> None:
        url, password, key = drawn
        out = _redact("Use ${DB_SCHEME}" + url.removeprefix("https") + " now")
        assert not _leaked(out, password, key), (url, out)

    check()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "see https://h.example/x?next=https://u:pw@h2.example/y&key=sk-1 ok",
            "see https://h.example/x ok",
        ),
        (
            "see https://h.example/x/https://u:pw@h2.example/y?key=sk-1 ok",
            "see https://h.example/x/https://h2.example/y ok",
        ),
        ("postgres://u:pw@a/db,postgres://u:hunter2@b/db", "postgres://a/db,postgres://b/db"),
        (
            "https://api.example.com/v1/customers/<id>/orders?api_key=sk-1",
            "https://api.example.com/v1/customers/<id>/orders",
        ),
        ("https://h.example/x?city=Zürich&api_key=sk-1", "https://h.example/x"),
        ("https://h.example/données?api_key=sk-1", "https://h.example/données"),
        # W5: a URL with a scheme and no host loses its query, with and without a scheme name.
        ("file:///etc/x?token=sk-1", "file:///etc/x"),
        ("file:///etc/app.conf?token=sk-1", "file:///etc/app.conf"),
        ("${S}:///etc/x?token=sk-1", "${S}:///etc/x"),
        ("Use https://?key=sk-1 now", "Use https:// now"),
        # W1: one URL broken by a tab or a line break, with a second :// in it.
        ("https://h.example/cb\n?redirect=https://app.example/&key=sk-1", "https://h.example/cb"),
        (
            "https://bot:hun\tter2@h.example/cb?redirect=https://app.example/",
            "https://h.example/cb",
        ),
        ("postgres://u:hun\nter2@a/db,postgres://b/db", "postgres://a/db,postgres://b/db"),
        ("https://bot:hun\tter2@h.example/v1?key=sk-1", "https://h.example/v1"),
        ("https://bot:hunter2@h.example/v1\n?key=sk-1", "https://h.example/v1"),
        # V1: the round-3 glued prefixes.
        ("1.https://bot:hunter2@hooks.example/v1?key=sk-1", "1.https://hooks.example/v1"),
        ("-https://bot:hunter2@h.example/v1?key=sk-1", "-https://h.example/v1"),
        ("...https://bot:hunter2@h.example/v1?key=sk-1", "...https://h.example/v1"),
        ("步骤2https://bot:hunter2@h.example/v1?key=sk-1", "步骤2https://h.example/v1"),
        ("#42https://bot:hunter2@h.example/v1?key=sk-1", "#42https://h.example/v1"),
        ("${DB_SCHEME}://app:hunter2@db.example/x?key=sk-1", "${DB_SCHEME}://db.example/x"),
        (
            "see https://h.example/a/2https://bot:hunter2@h2.example/y?key=sk-1 ok",
            "see https://h.example/a/2https://h2.example/y ok",
        ),
        ("x 1a.https://u:hunter2@h.example/?key=sk-1 y", "x 1a.https://h.example/ y"),
        # V3: IPv6 keeps its brackets.
        ("postgres://u:hunter2@[fd00::5]:5432/app?key=sk-1", "postgres://[fd00::5]:5432/app"),
        # Change review r1, B-M1: a ? or # before the first scheme, then a second URL. The
        # ? or # is searched for from the first start, not the token's (plant BC1).
        ("ok?https://a.example/x,postgres://u:hunter2@b/db", "ok?https://a.example/x,postgres://b/db"),
        ("#1https://a/,postgres://u:hunter2@b/db", "#1https://a/,postgres://b/db"),
        # B-L2: a ? right before a scheme letter (plant BT3), and a refused schemeless URL,
        # written back whole (plant BS3).
        ("https://h.example/x?https://u:pw@a.example/y", "https://h.example/x"),
        ("${S}://[", "${S}[redacted]"),
        ("${S}://u:hunter2@[x/y", "${S}[redacted]"),
        # Change review r2, B-M1: a # owns the rest of its token as a ? does, so a redirect URL
        # and the token after it in an OAuth-style fragment go with it (plant N1).
        ("https://app.example/cb#next=https://h.example/&access_token=sk-1", "https://app.example/cb"),
        (
            "Login https://app.example/cb#state=x&redirect=https://h.example/done&token=sk-1 then",
            "Login https://app.example/cb then",
        ),
    ],
    ids=[
        "nested_in_query",
        "nested_in_path",
        "comma_joined",
        "id_in_path",
        "zurich",
        "donnees",
        "file_no_host",
        "file_conf_no_host",
        "template_no_host",
        "empty_host",
        "break_before_query",
        "tab_in_password_two_starts",
        "break_in_password_dsn",
        "tab_in_password",
        "break_before_key",
        "digit_dot",
        "dash",
        "dots",
        "cjk_digit",
        "issue_number",
        "template_scheme",
        "nested_after_digit",
        "letter_digit_dot",
        "ipv6",
        "query_before_first_scheme",
        "fragment_before_first_scheme",
        "query_right_before_a_scheme",
        "refused_schemeless",
        "refused_schemeless_password",
        "fragment_owns_a_redirect",
        "fragment_owns_a_redirect_in_prose",
    ],
)
def test_named_urls_lose_their_password_and_query(text: str, expected: str) -> None:
    """B8's named cases."""
    assert _redact(text) == expected
    assert "hunter2" not in _redact(text) and "sk-1" not in _redact(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # W2: a scheme urllib doesn't know keeps its //.
        ("DATABASE_URL=sqlite:///./app.db", "DATABASE_URL=sqlite:///./app.db"),
        ("sqlite:///./app.db", "sqlite:///./app.db"),
        ("unix:///var/run/docker.sock", "unix:///var/run/docker.sock"),
        ("postgresql:///mydb", "postgresql:///mydb"),
        ("Use the myapp:// deep link.", "Use the myapp:// deep link."),
        ("the :// separator", "the :// separator"),
        # W1: a line break before a URL is kept, and the word is not joined into its scheme.
        ("Steps\nhttps://u:p@h.example/x?key=sk-1", "Steps\nhttps://h.example/x"),
        # The costs the design page names (V9, D4): more is removed, never less.
        ("http://localhost:8000.", "http://localhost"),
        ("[https://docs.example.com]", "[[redacted]"),
        (
            "Docs (https://docs.example.com/page?lang=en) explain it.",
            "Docs (https://docs.example.com/page explain it.",
        ),
        (
            "requests.get('https://u:p@h/x?key=sk-1', timeout=3)",
            "requests.get('https://h/x timeout=3)",
        ),
        ("请访问https://api.example.com/v1?key=sk-1获取数据", "请访问https://api.example.com/v1"),
        # A value that is one URL once its line breaks are removed is joined, as on the base.
        ("https://h/x\nthen", "https://h/xthen"),
    ],
    ids=[
        "sqlite_in_env_line",
        "sqlite",
        "unix_socket",
        "postgresql_no_host",
        "deep_link",
        "separator",
        "word_then_break",
        "port_before_punctuation",
        "bracket_joined_to_host",
        "closing_bracket_after_query",
        "call_after_query",
        "cjk_after_query",
        "lone_url_then_break",
    ],
)
def test_honest_text_changes_only_as_the_design_page_says(text: str, expected: str) -> None:
    assert _redact(text) == expected


@pytest.mark.parametrize(
    "value",
    [
        "postgres://u:p@[fd00::5]:5432/app?key=sk-1",
        "see postgres://u:p@[fd00::5]:5432/app now",
        "file:///etc/x?token=sk-1",
        "Use https://?key=sk-1 now",
        "${S}:///etc/x?token=sk-1",
        "sqlite:///./app.db",
        "no link here",
        "Call https://api.example.com/v1 for data.",
        "",
    ],
    ids=["ipv6", "ipv6_in_prose", "empty_host", "empty_host_in_prose", "template", "sqlite",
         "no_url", "nothing_to_clean", "empty"],
)
def test_cleaning_twice_changes_nothing(value: str) -> None:
    """B12's cleaner half (V3, W3): IPv6, empty-host and nothing-to-clean values."""
    once = _redact(value)
    assert _redact(once) == once


@_PROPERTY
@given(st.lists(_secret_urls(False), min_size=1, max_size=3))
def test_cleaning_twice_changes_nothing_for_urls_with_a_host(drawn: list) -> None:
    """W3: idempotent for every URL with a host, in prose."""
    once = _redact("Post " + " then ".join(url for url, _, _ in drawn) + " done")
    assert _redact(once) == once


_WORD = st.text(
    st.sampled_from(string.ascii_letters + string.digits + ".,;:!?'\"()[]/@#-数据é"),
    min_size=1,
    max_size=8,
).filter(lambda word: "://" not in word)
_GAP = st.sampled_from([" ", "  ", "\n", "\t", " \n ", "\r\n", "\u3000", "\u00a0"])


@_PROPERTY
@given(
    st.lists(
        st.tuples(st.one_of(_WORD, _secret_urls(True).map(lambda drawn: drawn[0])), _GAP),
        min_size=1,
        max_size=12,
    )
)
def test_honest_text_and_its_whitespace_are_kept_exactly(parts: list) -> None:
    """B10 (and B1's whitespace half): removing the URLs from the input and the output leaves the
    same text. The prose opens with a word, so it is never read as a single-URL field. The split
    is the test's own, not the module's, so a change to the cleaner's split is seen (plant N5)."""
    split = re.compile(r"(\s+)")

    text = "Note " + "".join(token + gap for token, gap in parts)
    out = _redact(text)
    given_parts, cleaned_parts = split.split(text), split.split(out)
    assert len(given_parts) == len(cleaned_parts)
    for given_part, cleaned_part in zip(given_parts, cleaned_parts):
        if "://" not in given_part:
            assert cleaned_part == given_part


def _timed(statement: str, timeout: float = 60.0) -> float:
    """Seconds one call takes, in a child process: a quadratic scan is stopped, not waited for."""
    code = (
        "import logging, time\n"
        "logging.disable(logging.WARNING)\n"
        "from fi.alk.providers.agent import _redact\n"
        f"value = {statement}\n"
        "start = time.perf_counter()\n"
        "_redact(value)\n"
        "print(time.perf_counter() - start)\n"
    )
    try:
        done = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=timeout, check=True
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"cleaning {statement} took more than {timeout} s")
    return float(done.stdout.strip())


def test_a_long_run_of_scheme_characters_cleans_in_linear_time() -> None:
    """B9: the lookbehind means a start inside a run of scheme characters is never tried."""
    assert _timed('"a" * 1_000_000 + "!://x"') < 1.0
    # Change review r2, B-L2: the lookbehind's other scheme characters too (plant N2).
    assert _timed('"-" * 1_000_000 + "!://x"') < 1.0
    assert _timed('"a.-+" * 250_000 + "!://x"') < 1.0
    assert _timed('"p " + "1" * 1_000_000 + "://x"') < 1.0


def test_a_token_of_many_starts_cleans_in_linear_time() -> None:
    """B9c (W4): the ?/# position is cached, so one token of 400,000 starts is searched once."""
    assert _timed('"p " + "a://x/" * 400_000') < 2.0


def test_five_mib_of_url_dense_prose_cleans_in_two_seconds() -> None:
    """B9: varied URLs, so urlsplit's own cache doesn't flatter it (V4)."""
    pick = random.Random(9)
    sentences = []
    size = 0
    while size < 5 * 1024 * 1024:
        host = "".join(pick.choice(string.ascii_lowercase) for _ in range(8))
        key = "".join(pick.choice(_UNRESERVED) for _ in range(10))
        sentence = f"Call https://u{size}:p{key}@{host}.example/v{size % 97}?key={key} then reply. "
        sentences.append(sentence)
        size += len(sentence)
    prose = "".join(sentences)
    start = time.perf_counter()
    out = _redact(prose)
    elapsed = time.perf_counter() - start
    # Booleans, so a failure never asks pytest to explain a 5 MiB string.
    kept_a_query = "?key=" in out
    assert elapsed < 2.0
    assert not kept_a_query
