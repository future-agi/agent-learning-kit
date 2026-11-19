"""`credential_in`, the credential-named argument rule, and the named-value forms added with
them. A refusal is a value the platform keeps exactly as written, so it must hold no credential;
the kept cases are honest text that the cleaner would otherwise change."""

from __future__ import annotations

import time

import pytest

from fi.alk.providers import agent, secret_shapes

# ------------------------------------------------------------------------------ credential_in
_REFUSED = {
    "numeric password, a slash, then an at": "https://bot:8812/x@db.example.com/",
    "numeric password, a space, then an at": "https://bot:12 34@db.example.com",
    "numeric password, a slash, then an at and a port host": "https://bot:8812/x@db:5432/app",
    "numeric password, a slash, then an at and an address": "https://bot:8812/x@10.0.0.5/app",
    "numeric password, a slash, then an at and localhost": "https://bot:8812/x@localhost/app",
    "a value broken by a space in a credential-named query": "https://h/x?token=abc def",
    "a bare lower-case variable": "postgres://svc:$ecret@db/app",
    "a bare lower-case variable, underscored": "postgres://svc:$db_password@db/app",
    "a bare lower-case variable, no scheme": "svc:$db_password@db.internal/app",
    "a reference with a literal default": "postgres://svc:${A:-hunter2}@db/app",
    "a reference with a short literal default": "postgres://svc:${DB_PASSWORD:-x}@db/app",
    "a reference with a literal default, colon-less": "postgres://svc:${A-hunter2}@db/app",
    "port-shaped password run, then a slash": "postgres://svc:$ecret/P4ss@db/app",
    "port-shaped password run, then a space": "postgres://svc:$ecret P4ss@db/app",
    "port-shaped password run, a long one": "postgres://svc:$Zq9fakePw/x@db/app",
    "port-shaped password in angle brackets": "postgres://svc:<Zq9>/fakePw@db/app",
    "port-shaped password in braces": "postgres://svc:{Zq9}/fakePw@db/app",
    "port-shaped password, dotted host": "postgres://svc:$ecret/P4ss@db.example.com/app",
    "password in single braces": "postgres://svc:{Zq9fakePw}@db/app",
    "password in single braces, no scheme": "svc:{Zq9fakePw}@db.internal/app",
    "reference followed by text": "postgres://svc:${A}tail@db/app",
    "lower-case name with a digit and a mark": "postgres://svc:$a9x!@db/app",
    "percent-encoded start of a literal": "postgres://svc:%24ecret@db/app",
    "angle reference followed by text": "postgres://svc:<a>b@db/app",
    "template followed by text": "postgres://svc:{{a}}b@db/app",
    "path query key": "/v1/search?key=AbCdEf123456",
    "path query sig": "/c?sv=2020&sig=abcDEF123%2F",
    "path query pwd": "/v1/items?pwd=hunter2",
    "path query auth": "/login?auth=hunter2",
    "url query marker": "https://h/x?api_key=abc",
    "url query signature": "https://h/x?signature=zz",
    "url query pass": "https://h/x?a=1&pass=zz",
    "url query token marker": "https://h/x?access_token=abc",
    "fragment name": "https://h/x#password=abc",
    "semicolon name": "https://h/x?a=1;secret=abc",
    "query value of 16 key characters": "/v1/items?cursor=AbCdEfGhIjKlMnOp",
    "query value with a key shape": "/v1/items?x=AKIAIOSFODNN7EXAMPLE",
    "userinfo password": "https://u:pw@h/x",
    "userinfo literal password beside a reference host": "postgres://app:hunter2@${DB_HOST}/x",
    "userinfo password, other scheme": "postgres://admin:admin@localhost:5432/app",
    "userinfo password holding a slash": "http://svc:Zq9/fakePw@inventory:9000/v1/items",
    "userinfo password holding a slash, in a connection string": "postgres://app:k8/Qz3vLm@db/app",
    "userinfo password holding a hash": "postgres://svc:Zq9#fakePw@db/app",
    "userinfo password holding a question mark": "postgres://svc:Zq9?fakePw@db/app",
    "userinfo password holding a space": "postgres://svc:Zq9 fakePw@db/app",
    "userinfo password starting with a dollar": "postgres://svc:$ecretP4ss@db/app",
    "userinfo password starting with a dollar, http": "http://svc:$Zq9fakePw@db/app",
    "userinfo password starting with a dollar, no scheme": "//svc:$ecretP4ss@db/app",
    "userinfo password starting with an angle bracket": "postgres://svc:<Zq9fakePw@db/app",
    "userinfo password starting with two braces": "postgres://svc:{{Zq9fakePw@db/app",
    "userinfo password broken by a line break before the at": "postgres://svc:Zq9fakePw\n@db/app",
    "userinfo password broken by a tab before the at": "postgres://svc:Zq9fakePw\t@db/app",
    "userinfo password broken by a line break after the colon": "postgres://svc:\nZq9fakePw@db/app",
    "userinfo password broken by two line breaks": "postgres://svc:Zq9\n\nfakePw@db/app",
    "userinfo password broken by five tabs": "postgres://svc:Zq9\t\t\t\t\tfakePw@db/app",
    "scheme run starting with a digit": "9postgres://svc:Zq9fakePw@db?sslmode=require",
    "scheme run starting with a dash": "-postgres://svc:Zq9fakePw@db?sslmode=require",
    "scheme run starting with a dot": ".postgres://svc:Zq9fakePw@db:5432?x=1",
    "userinfo with no scheme, after //": "//svc:pw@db/app",
    "userinfo with no scheme, at the start": "svc:Zq9fakePw@db.internal/app",
    "userinfo with no scheme, a port after the host": "svc:Zq9fakePw@db.internal:5432",
    "key shape alone": "AKIAIOSFODNN7EXAMPLE",
    "key shape in prose": "use ghp_" + "X" * 36 + " here",
    "named value in text": "token=abc123def456 is the sample cursor",
    # The same name and value as a dict leaf is kept (see _KEPT); written as text it is a
    # named value the cleaner masks.
    "named value with a short value, as text": 'reset_token="RT4821X"',
    "slack hook path": "https://hooks.slack.com/services/T0000/B0000/XXXXXXXXXXXXXXXXXXXXXXXX",
    "key in the host": "https://api.sk_live_" + "a1" * 12 + ".example/x",
    # Letter runs and digit runs, mostly letters: a hex digest cut at dashes, not an id
    # (the ratio guard in _reads_as_names_or_an_id).
    "a hex digest cut into letter and digit runs": "https://api.example.com/x?x=deadbeef-1234-cafebabe-5678-feedface",
}


@pytest.mark.parametrize("name", sorted(_REFUSED))
def test_credential_in_refuses(name: str) -> None:
    assert secret_shapes.credential_in(_REFUSED[name]) is True


# A URL password broken by one of these characters, raw or percent-encoded, is still a password.
@pytest.mark.parametrize(
    "char", ["/", "#", "?", " ", "%2F", "%23", "%3F", "%20"], ids=lambda c: repr(c)
)
@pytest.mark.parametrize("scheme", ["http", "postgres"])
def test_credential_in_refuses_a_password_holding_a_url_break(
    scheme: str, char: str
) -> None:
    assert secret_shapes.credential_in(f"{scheme}://svc:Zq9{char}fakePw@db:9000/v1/x")


_KEPT = {
    "a port, a path and an address in a query": "http://localhost:8080/register?email=a@b.com",
    "a port, a path and an at-handle": "http://localhost:8080/users/@me",
    "localhost and a port": "http://localhost:3000",
    "a version after an at": "https://db:5432/lodash@4.17.21",
    "an address in prose, two words": "mail me, qa @acme.com for details, qa@acme.com in prose",
    "a template host with an at-handle": "http://{HOST}:{PORT}/v1/users/@me",
    "a value broken by a space under an ordinary query name": "https://h/x?q=abc def",
    "a port template then a path with an at": "http://svc:$PORT/path@x",
    "a braced port template then a path with an at": "http://svc:${PORT}/path@x",
    "a numeric port then a path with an at": "http://db:8080/pkg@1.2",
    "a lower-case port placeholder then a path with an at": "http://svc:<port>/path@x",
    "a reference with an error message": "postgres://svc:${DB_PASSWORD:?missing}@db/app",
    "a percent-encoded braced reference": "postgres://svc:%24%7BDB_PASSWORD%7D@db/app",
    "two shell references": "postgres://svc:${A}${B}@db/app",
    "two angle references": "postgres://svc:<a><b>@db/app",
    "two templates": "postgres://svc:{{a}}{{b}}@db/app",
    "a reference with an empty default": "postgres://svc:${A:-}@db/app",
    "a reference with a reference as its default": "postgres://svc:${A:-$B}@db/app",
    "a prose query": "https://shop.example.com/search?q=red+running+shoes+size",
    "a mixed-case path in a query": "https://api.example.com/forecast?tz=America/Los_Angeles",
    "an encoded mixed-case path in a query": "https://api.example.com/forecast?timezone=America%2FNew_York",
    "a snake_case sort key": "https://api.example.com/orders?sort=created_at_desc_order",
    "a dashed id of mostly digits": "https://api.example.com/items?sku=ABC-DEF-123456789",
    # The ratio guard keeps an id whose runs are mostly digits.
    "a dashed id of digit runs only": "https://api.example.com/items?ids=1234-5678-9012-3456",
    "an encoded path in a query": "/login?next=%2Faccount%2Fsettings%2Fbilling",
    "userinfo password, a full shell reference": "postgres://svc:${DB_PASSWORD}@db/app",
    "userinfo password, a full variable": "postgres://svc:$DB_PASSWORD@db/app",
    "userinfo password, a full placeholder": "postgres://svc:<db_password>@db/app",
    "userinfo password, a placeholder with a space": "postgres://app:<db password>@db/app",
    "userinfo password, a full template": "postgres://svc:{{db_password}}@db/app",
    "address in a query": "/o?email=a@b.com",
    "mailto": "mailto:a@b.com",
    "a user at a host": "user@host",
    "an email address": "email@example.com",
    "scheme-less userinfo from a reference": "//svc:${DB_PASSWORD}@db/app",
    "pagination name in a query": "/v1/items?nextPageToken=abc",
    "pagination name, snake case": "/v1/items?next_page_token=abc",
    "a count of tokens": "/v1/complete?max_tokens=256",
    "placeholder from the environment": "https://api.example.com/v1/search?key=${API_KEY}",
    "placeholder in braces": "https://api.example.com/v1/search?key={API_KEY}",
    "placeholder in angle brackets": "https://api.example.com/v1/search?key=<API_KEY>",
    "placeholder in a template": "https://api.example.com/v1/search?key={{api_key}}",
    "placeholder from a variable": "https://api.example.com/v1/search?api_key=$API_KEY",
    "a uuid in a query": "/o?id=550e8400-e29b-41d4-a716-446655440000",
    "a 16-digit number in a query": "/e?since=1700000000000000",
    "a model name in a query": "/v1/chat?model=gpt-4o-mini-2024-07-18",
    "a model id in a query": "/v1/chat?model_id=claude-opus-5-5-20261101",
    "query id": "/o?id=77",
    "query id on a url": "https://shop.example.com/o?id=77",
    "api-version": "https://acme.openai.azure.com/x?api-version=2024-01-01",
    "api-version on a path": "/x?api-version=2024-01-01",
    "sslmode": "postgresql://db:5432/app?sslmode=disable",
    "search query": "https://shop.example.com/search?q=running+shoes",
    "fragment": "https://docs.example.com/guide#install",
    "bare fragment": "#install",
    "capital host": "HTTPS://API.EXAMPLE.COM/v1",
    "host and port template": "{HOST}:{PORT}/v1",
    "host and port template on a url": "http://{HOST}:{PORT}/v1/items",
    "shell template": "${API_HOST}/v1",
    "mustache template": "{{BASE_URL}}/v1/items",
    "userinfo password from a shell reference": "postgres://app:${DB_PASSWORD}@db/x",
    "userinfo password from a variable": "postgres://app:$PASS@db/x",
    "userinfo password from a placeholder": "postgres://app:<password>@db/x",
    "userinfo password from a template": "postgres://app:{{DB_PASSWORD}}@db/x",
    "username without a password": "ssh://git@github.com/org/repo",
    "username and a port": "ssh://git@github.com:22/org/repo",
    "postgres user alone": "postgres://postgres@localhost:5432/app",
    "internal host": "http://inventory:9000/v1/items",
    "plain word": "hunter2",
    "uuid": "123e4567-e89b-12d3-a456-426614174000",
    "order id": "ORD-2024-000123",
    "named value as plain text, no value of its own": "reset the token",
    "a bare shapeless leaf under a credential name": "RT4821X",
}


@pytest.mark.parametrize("name", sorted(_KEPT))
def test_credential_in_never_refuses_on_its_own(name: str) -> None:
    assert secret_shapes.credential_in(_KEPT[name]) is False


def test_credential_in_reads_strings_inside_lists_and_dicts() -> None:
    assert secret_shapes.credential_in(["ok", {"a": ["https://u:pw@h/x"]}]) is True
    assert secret_shapes.credential_in({"a": ["/x?id=77"], "b": "plain"}) is False


def test_a_long_query_value_is_still_refused_unless_it_reads_as_words_or_a_path() -> None:
    for text in (
        "/x?q=AbCd+EfGh/IjKl+MnOp/QrSt==",  # base64
        "/x?q=abcdefghijklmnopqrstuv",  # one long lowercase word
        "/x?q=abcd+efgh/ijkl+mnop",  # words and slashes mixed
        "/x?q=red+running+shoes+ghp_" + "X" * 36,  # a key shape inside words
        "/x?next=%2Fabcdefghijklmnopqrstuv",  # a single long segment
    ):
        assert secret_shapes.credential_in(text) is True, text


def test_a_query_value_that_only_starts_like_a_reference_is_refused() -> None:
    assert secret_shapes.credential_in("/x?password=$ecretP4ss99")


def test_credential_in_reads_dict_keys() -> None:
    assert secret_shapes.credential_in({"ghp_" + "X" * 36: {"owner": "alice"}}) is True
    assert secret_shapes.credential_in({"https://u:pw@h/x": 1}) is True


def test_a_pagination_or_token_count_argument_may_list_values() -> None:
    for name in ("nextPageToken", "next_page_token", "max_tokens", "total_tokens"):
        assert (
            secret_shapes.credential_named_arg_values_refusal(name, ["abc"]) is False
        )
    assert secret_shapes.credential_named_arg_values_refusal("page_token", ["a"]) is True


# Time grows with the size, so a pattern that restarts at each position of one long run
# shows at 16 KiB already. 1 MiB must take under 64 times the 64 KiB time plus 0.5 s: linear
# growth with room for a slow runner, where a quadratic pattern would need 256 times as long.
# Each size is timed three times and the median is read, so one stall on a loaded runner moves
# neither side of the ratio. There is no absolute bound: it measures the runner's load, not the
# pattern's growth.
_RUNS = {
    "letters": "a",
    "scheme characters": "a.-",
    "digits and underscores": "a1_",
    "a run behind a scheme": "x://a",
    "digits": "1",
    "a digit and a letter": "1a",
    "dots and dashes": ".-",
    "a digit-led scheme": "9a://",
    "a digit-led scheme and a colon": "1a:",
    "a port-shaped run then a space": "x://a:b ",
    "a port-shaped run then a slash": "x://a:$b/",
    "digits then a slash": "x://a:1/",
    "digits then a space": "x://a:1 ",
    "one long digit run after a colon": "x://a:" + "1" * 4096,
}


def _seconds(function, text: str) -> float:
    start = time.perf_counter()
    function(text)
    return time.perf_counter() - start


def _median_seconds(function, text: str, runs: int = 3) -> float:
    return sorted(_seconds(function, text) for _ in range(runs))[runs // 2]


@pytest.mark.parametrize("run", sorted(_RUNS))
@pytest.mark.parametrize(
    "function",
    [
        secret_shapes.credential_in,
        secret_shapes.credential_in_check_code,
        secret_shapes.rule1,
    ],
    ids=lambda function: function.__name__,
)
def test_a_long_run_is_read_in_linear_time(function, run: str) -> None:
    unit = _RUNS[run]
    times = {}
    for kib in (16, 64, 256, 1024):
        text = (unit * (kib * 1024 // len(unit) + 1))[: kib * 1024]
        times[kib] = _median_seconds(function, text)
    assert times[1024] < 64 * times[64] + 0.5, times


def test_credential_in_ignores_numbers_booleans_and_none() -> None:
    assert secret_shapes.credential_in([4821, 12345678, True, None, 1.5]) is False


def test_credential_in_does_not_judge_a_dict_by_its_key_names() -> None:
    # A shapeless value under a credential name is the argument rule's job, not this one's.
    assert secret_shapes.credential_in({"password": "hunter2"}) is False


def test_credential_in_is_not_the_cleaner_changing_the_text() -> None:
    # Each is changed by the cleaner, and none holds a credential.
    for text in ("HTTPS://API.EXAMPLE.COM/v1", "/o?id=77", "{HOST}:{PORT}/v1"):
        assert secret_shapes.credential_in(text) is False
    assert agent._redact("https://shop.example.com/o?id=77") != (
        "https://shop.example.com/o?id=77"
    )


# The check-code entry point reads one more thing: a credential-named assignment.
_CODE_REFUSED = {
    "a credential named for itself, holding a scheme word": 'password = "Bearer"',
    "a token type with a real value": 'token_type = "hunter2x"',
    "assignment": 'password = "x"',
    "assignment, single quotes": "password = 'x'",
    "assignment, no spaces": 'password="x"',
    "indented in a function": 'def check(w):\n    api_key = "abc"\n    return True',
    "dict literal": '{"password": "x"}',
    "item assignment": 'creds["password"] = "x"',
    "upper-case name": 'DB_PASSWORD = "x"',
    "name with a marker inside": 'reset_token = "RT4821X"',
    "still refuses what credential_in does": "https://u:pw@h/x",
}


@pytest.mark.parametrize("name", sorted(_CODE_REFUSED))
def test_check_code_refuses(name: str) -> None:
    assert secret_shapes.credential_in_check_code(_CODE_REFUSED[name]) is True


_CODE_KEPT = {
    "a token type": 'token_type = "Bearer"',
    "a token type, dict literal, any case": '{"token_type": "basic"}',
    "an auth scheme": 'auth_scheme = "Token"',
    "a count of tokens": 'max_tokens = "4096"',
    "a pagination name": 'expected = {"next_page_token": "abc"}',
    "assignment from a reference": 'password = "$PASSWORD"',
    "assignment from a placeholder": 'password = "<password>"',
    "assignment from a template": 'password = "{{PASSWORD}}"',
    "assignment from the environment": 'password = os.environ["PASSWORD"]',
    "an empty literal": 'password = ""',
    "a comparison": 'if name == "password":\n    pass',
    "a name with no marker": 'username = "alice"',
    "a plain check": "return world['orders'][0]['status'] == 'shipped'",
}


@pytest.mark.parametrize("name", sorted(_CODE_KEPT))
def test_check_code_never_refuses_on_its_own(name: str) -> None:
    assert secret_shapes.credential_in_check_code(_CODE_KEPT[name]) is False


def test_the_assignment_rule_is_only_for_check_code() -> None:
    assert secret_shapes.credential_in('password = "x"') is False


# ------------------------------------------------------ credential_named_arg_values_refusal
def test_a_credential_named_argument_may_not_list_values() -> None:
    assert (
        secret_shapes.credential_named_arg_values_refusal("password", ["hunter2"])
        is True
    )
    assert (
        secret_shapes.credential_named_arg_values_refusal(
            "password", ["hunter2", "correcthorse"]
        )
        is True
    )


@pytest.mark.parametrize(
    "name",
    [
        "PASSWORD",
        "passwd",
        "api_key",
        "apiKey",
        "api-key",
        "secret",
        "client_secret",
        "reset_token",
        "clientSecret",
        "access_token",
        "token",
        "Authorization",
        "db_password",
        # cookie and credential(s) as the last word.
        "session_cookie",
        "auth_cookie",
        "aws_credentials",
        "user_credentials",
        "sessionCookie",
        "cookie",
        "credentials",
    ],
)
def test_the_argument_name_is_judged_by_the_named_value_forms(name: str) -> None:
    assert secret_shapes.credential_named_arg_values_refusal(name, ["x1"]) is True


@pytest.mark.parametrize(
    "name,values",
    [
        ("authorization_status", ["approved", "denied", "pending"]),
        ("credential_type", ["passport", "driver_license"]),
        ("secretary_name", ["Ana"]),
        ("cookie_flavor", ["oatmeal", "chocolate"]),
        ("token_count", [5, 10]),
        ("tokenizer", ["cl100k", "o200k"]),
    ],
)
def test_an_honest_argument_that_only_contains_a_marker_may_list_values(
    name: str, values: list
) -> None:
    assert secret_shapes.credential_named_arg_values_refusal(name, values) is False


@pytest.mark.parametrize("reference", ["$PASSWORD", "<password>", "{{password}}"])
def test_references_are_allowed_in_a_credential_named_argument(reference: str) -> None:
    assert (
        secret_shapes.credential_named_arg_values_refusal("password", [reference])
        is False
    )


def test_one_literal_among_references_is_refused() -> None:
    assert (
        secret_shapes.credential_named_arg_values_refusal(
            "password", ["$PASSWORD", "hunter2"]
        )
        is True
    )


def test_a_number_under_a_credential_name_is_refused() -> None:
    assert (
        secret_shapes.credential_named_arg_values_refusal("pin_token", [4821]) is True
    )


@pytest.mark.parametrize(
    "values", [["alice"], ["a", "b"], [], None], ids=["one", "two", "empty", "none"]
)
def test_other_arguments_are_allowed_their_values(values: list[str] | None) -> None:
    assert (
        secret_shapes.credential_named_arg_values_refusal("username", values) is False
    )


def test_a_credential_named_argument_with_no_values_is_allowed() -> None:
    assert secret_shapes.credential_named_arg_values_refusal("password", []) is False
    assert secret_shapes.credential_named_arg_values_refusal("password", None) is False


def test_the_markers_are_the_cleaners() -> None:
    assert secret_shapes.REDACTION_MARKERS is agent._REDACTION_MARKERS


# ------------------------------------------------------------------- the named-value forms
def _cleaned(text: str) -> str:
    return agent._redact(text)


_NEWLY_MASKED = {
    "secretkey, lower case": ("secretkey=abc123def456", "secretkey=[redacted]"),
    "secretkey, colon": ("secretkey: abc123def456", "secretkey: [redacted]"),
    "secretkey after a separator": (
        "app_secretkey=abc123def456",
        "app_secretkey=[redacted]",
    ),
    "Pass after a lower-case letter": ("smtpPass=hunter2x", "smtpPass=[redacted]"),
    "Pass, other word": ("firstPass=abc123x", "firstPass=[redacted]"),
    "Passwd after a lower-case letter": (
        "smtpPasswd=hunter2x",
        "smtpPasswd=[redacted]",
    ),
    "Pwd after a lower-case letter": ("dbPwd: abc123x", "dbPwd: [redacted]"),
    "Pwd, equals": ("dbPwd=abc123x", "dbPwd=[redacted]"),
    "padding of 17 spaces": (
        "password" + " " * 17 + "= hunter2x",
        "password" + " " * 17 + "= [redacted]",
    ),
    "padding of 4000 spaces": (
        "password" + " " * 4000 + "= hunter2x",
        "password" + " " * 4000 + "= [redacted]",
    ),
    "padding of 16 spaces": (
        "password" + " " * 16 + "= hunter2x",
        "password" + " " * 16 + "= [redacted]",
    ),
}


@pytest.mark.parametrize("name", sorted(_NEWLY_MASKED))
def test_the_named_value_forms_that_are_now_masked(name: str) -> None:
    text, expected = _NEWLY_MASKED[name]
    assert _cleaned(text) == expected


_STAY_UNMASKED = {
    "bypass": "bypass=abc123x",
    "compass": "compass: abc123x",
    "passport": "passport: X1234567",
    "Passport": "passportNumber: X1234567",
    "pwd alone is the working directory": "pwd=/home/user1",
    "pass alone": "pass=abc123x",
    "Pass not after a lower-case letter": "Pass=abc123x",
}


@pytest.mark.parametrize("name", sorted(_STAY_UNMASKED))
def test_the_named_value_forms_that_stay_unmasked(name: str) -> None:
    text = _STAY_UNMASKED[name]
    assert _cleaned(text) == text


# Written out, not read from the module: a name dropped there must fail here.
_PAGINATION_KEPT = (
    "next_page_token",
    "prev_page_token",
    "nextPageToken",
    "prevPageToken",
    "nextForwardToken",
    "nextBackwardToken",
    "next_forward_token",
    "continuationToken",
)


@pytest.mark.parametrize("name", _PAGINATION_KEPT)
def test_each_pagination_name_in_the_list_keeps_its_value(name: str) -> None:
    for text in (f"{name}: abc123def456", f'{{"{name}": "CAoQAA12"}}'):
        assert _cleaned(text) == text


_PAGE_TOKENS_MASKED = (
    "FB_PAGE_TOKEN=EAAGm0PX4ZCpsBAabc123def456ghi789",
    "page_token=EAAGm0PX4ZCpsBAabc123def456",
    "fb_page_token: EAAGm0PX4ZCpsBAabc123",
    '{"page_token": "EAAGm0PX4ZCpsBAabc123"}',
)


@pytest.mark.parametrize("text", _PAGE_TOKENS_MASKED)
def test_a_page_token_that_is_not_in_the_list_stays_masked(text: str) -> None:
    out = _cleaned(text)
    assert out != text
    assert "EAAGm0PX4ZCpsBAabc123" not in out


def test_a_model_name_lifts_only_the_length_rule_not_a_key_shape() -> None:
    # A model name is long and key-like by length alone; a real key under that name is still one.
    assert secret_shapes.credential_in("/v1/chat?model=AKIAIOSFODNN7EXAMPLE")
    assert secret_shapes.credential_in("/v1/chat?model=ghp_" + "X" * 36)


# Padding between a name and its value has no bound, so its reading must stay linear: 1 MiB
# of it takes about 16 times as long as 64 KiB, and the bound allows twice that and 0.5 s.
@pytest.mark.parametrize(
    "shape",
    ["name, padding, value", "name and padding only", "names between padding", "padding after ="],
)
def test_unbounded_padding_is_cleaned_in_linear_time(shape: str) -> None:
    def build(size: int) -> str:
        if shape == "name, padding, value":
            return "password" + " " * size + "= hunter2x"
        if shape == "name and padding only":
            return "password" + " " * size
        if shape == "names between padding":
            return ("password" + " " * 24) * (size // 32)
        return "password=" + " " * size + "hunter2x"

    times = {}
    for kib in (64, 1024):
        text = build(kib * 1024)
        times[kib] = _median_seconds(agent._redact, text)
    assert times[1024] < 32 * times[64] + 0.5, times
