"""`credential_in`, the credential-named argument rule, and the named-value forms added with
them. A refusal is a value the platform keeps exactly as written, so it must hold no credential;
the kept cases are honest text that the cleaner would otherwise change."""

from __future__ import annotations

import pytest

from fi.alk.providers import agent, secret_shapes

# ------------------------------------------------------------------------------ credential_in
_REFUSED = {
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
    "key shape alone": "AKIAIOSFODNN7EXAMPLE",
    "key shape in prose": "use ghp_" + "X" * 36 + " here",
    "named value in text": "token=abc123def456 is the sample cursor",
    # The same name and value as a dict leaf is kept (see _KEPT); written as text it is a
    # named value the cleaner masks.
    "named value with a short value, as text": 'reset_token="RT4821X"',
    "slack hook path": "https://hooks.slack.com/services/T0000/B0000/XXXXXXXXXXXXXXXXXXXXXXXX",
    "key in the host": "https://api.sk_live_" + "a1" * 12 + ".example/x",
}


@pytest.mark.parametrize("name", sorted(_REFUSED))
def test_credential_in_refuses(name: str) -> None:
    assert secret_shapes.credential_in(_REFUSED[name]) is True


_KEPT = {
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


@pytest.mark.parametrize("name", ["PASSWORD", "api_key", "reset_token", "clientSecret"])
def test_the_argument_name_is_matched_by_the_cleaners_markers(name: str) -> None:
    assert secret_shapes.credential_named_arg_values_refusal(name, ["x1"]) is True


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
    "padding of 17 spaces, a named limit": "password" + " " * 17 + "= hunter2x",
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
