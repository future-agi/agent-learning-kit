"""TH-9500: secrets found by their shape, in the kit's one cleaner (plan v1.1, tests S1-S5b).

Every key-shaped value here is fake. The corpus below is the design reviewers' case lists,
copied as data (W2), so a planted bug in a rule fails a named case.
"""

from __future__ import annotations

import itertools
import logging
import re
import subprocess
import sys

import pytest

from fi.alk.providers import secret_shapes
from fi.alk.providers.agent import (
    _LEADING_SCHEME,
    _REDACTED,
    _SPACE,
    _URL_BREAKS,
    _redact,
    _redact_token,
)


def _base(text: str) -> str:
    """TH-9491's URL step alone, frozen here: what the cleaner did before TH-9500."""
    if "://" not in text:
        return text
    if _LEADING_SCHEME.match(text):
        joined = text.translate(_URL_BREAKS)
        if not _SPACE.search(joined):
            return _redact_token(joined, [])
    return "".join(
        part if not part or part.isspace() else _redact_token(part, [])
        for part in _SPACE.split(text)
    )


def _logged(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "fi.alk.providers.agent"
    ]


def test_the_marker_and_the_held_bound_match_their_owners() -> None:
    from fi.alk.harness.hosted_chat_entrypoint import _HOLD_HARD

    assert secret_shapes.REDACTED == _REDACTED
    assert secret_shapes._LONGEST_HELD_KEY == _HOLD_HARD


# -- S1: the drift test, against the kit's guardrails list ------------------------------------

_SAMPLES = {
    "openai_api_key": "sk-"
    + "FAKEfake0123456789ab"
    + "T3BlbkFJ"
    + "FAKEfake0123456789ab",
    "openai_api_key_generic": "sk-proj-"
    + "FAKEfake0123456789abcdefFAKEfake0123456789ab",
    "aws_access_key": "AKIA" + "FAKEFAKE12345678",
    "google_api_key": "AIza" + "FAKEfake0123456789FAKEfake012345678",
    "github_pat": "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
    "github_oauth": "gho_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
    "github_user_token": "ghu_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
    "github_server_token": "ghs_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
    "github_refresh_token": "ghr_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
    "github_fine_grained_pat": "github_pat_"
    + "FAKEfake0123456789abcd"
    + "_"
    + "F" * 59,
    "gitlab_pat": "glpat-" + "FAKEfake0123456789ab",
    "slack_token": "xoxa-1234567890-1234567890-" + "FAKEfakeFAKEfakeFAKEfake",
    "slack_bot_token": "xoxb-12345678901-12345678901-" + "FAKEfakeFAKEfakeFAKEfake",
    "stripe_secret_key": "sk_live_" + "FAKEfake0123456789FAKEfa",
    "sendgrid_api_key": "SG."
    + "FAKEfake0123456789abcd"
    + "."
    + "FAKEfake0123456789" * 2
    + "abcdefg",
    "anthropic_api_key": "sk-ant-" + "FAKEfake0123456789" * 2 + "abcd",
    "huggingface_token": "hf_" + "FAKEfake0123456789FAKEfake01234567",
    "npm_token": "npm_" + "FAKEfake0123456789FAKEfake0123456789",
    "pypi_token": "pypi-AgEIcHlwaS5vcmc" + "FAKEfake0123456789" * 3,
    "private_key_header": "-----BEGIN RSA PRIVATE KEY-----\nMIIEFAKEQUJDREVG\n"
    "-----END RSA PRIVATE KEY-----",
    "pgp_private_key": "-----BEGIN PGP PRIVATE KEY BLOCK-----\nlQOYBFAKEQUJDREVG\n"
    "-----END PGP PRIVATE KEY BLOCK-----",
}
# Every other guardrails entry at confidence 0.9 or more, with why it isn't a prefix pattern.
_LEFT_OUT = {
    "aws_secret_key": "(?i) start; kept as its own pattern, from its own regex (r4 M3)",
    "azure_storage_key": "(?i) start; kept as its own pattern, from its own regex (r4 M3)",
    "firebase_api_key": "(?i) start; a name covered by the named-value rule, value AIza",
    "mailchimp_api_key": "no literal start; left out on purpose, it matches honest hex IDs",
    "discord_token": "no literal start; the bare Discord bot token is a named limit",
    "telegram_bot_token": "no literal start; the bare Telegram token is our own pattern",
    "database_url": "(?i) start; TH-9491's URL step removes the userinfo",
    "connection_string": "(?i) start, and its .* makes the regex quadratic",
}


def _guardrails() -> dict[str, tuple[str, float]]:
    from fi.evals.guardrails.scanners import secrets

    return {
        name: (pattern, confidence)
        for listed in vars(secrets).values()
        if isinstance(listed, list) and listed and isinstance(listed[0], tuple)
        for pattern, name, confidence in listed
    }


def _literal_prefix(pattern: str) -> str:
    out = []
    at = 0
    while at < len(pattern):
        char = pattern[at]
        if char == "\\" and at + 1 < len(pattern) and pattern[at + 1] in ".-/":
            out.append(pattern[at + 1])
            at += 2
            continue
        if char in "\\[](){}?*+|^$.":
            break
        out.append(char)
        at += 1
    return "".join(out)


def test_the_prefix_patterns_and_the_left_out_entries_are_the_guardrails_list() -> None:
    entries = {name: entry for name, entry in _guardrails().items() if entry[1] >= 0.9}
    prefixed = {
        name
        for name, (pattern, _) in entries.items()
        if len(_literal_prefix(pattern)) >= 3
    }

    assert prefixed == set(_SAMPLES)
    assert len(prefixed) == 21
    assert set(entries) - prefixed == set(_LEFT_OUT)


@pytest.mark.parametrize("name", sorted(_SAMPLES))
def test_each_prefix_patterns_sample_is_masked(name: str) -> None:
    pattern, _ = _guardrails()[name]
    sample = _SAMPLES[name]
    assert re.search(pattern, sample)

    out = _redact(f"Use this:\n{sample}\nthen stop.")

    assert sample.splitlines()[-2 if "\n" in sample else 0][-12:] not in out
    assert out.startswith("Use this:\n") and out.endswith("\nthen stop.")


def _name_forms(words: list[list[str]]) -> list[str]:
    """Every join a guardrails name allows: each optional word in or out, each join "", "_"
    or "-", in lower and upper case and as PascalCase."""
    forms = set()
    for kept in itertools.product(
        *[[word] if required else [word, ""] for word, required in words]
    ):
        parts = [part for part in kept if part]
        for joins in itertools.product(["", "_", "-"], repeat=len(parts) - 1):
            name = parts[0] + "".join(
                join + part for join, part in zip(joins, parts[1:])
            )
            forms |= {name, name.upper()}
            forms.add(
                parts[0].title()
                + "".join(join + part.title() for join, part in zip(joins, parts[1:]))
            )
    return sorted(forms)


_AWS = "wJalrXUtnFEMIFAKEK7MDENGbPxRfiCYFAKEKEY1"
_AZURE = ("FAKEazureFAKEkey0123456789abcdefFAKE+/" * 3)[:86] + "=="
_FIREBASE = "AIza" + "FAKEfake0123456789FAKEfake012345678"
_NAMED_ENTRIES = {
    "aws_secret_key": (
        [["aws", True], ["secret", True], ["access", False], ["key", True]],
        _AWS,
    ),
    "azure_storage_key": (
        [["azure", True], ["storage", False], ["account", False], ["key", True]],
        _AZURE,
    ),
    "firebase_api_key": (
        [["firebase", True], ["api", False], ["key", True]],
        _FIREBASE,
    ),
}


@pytest.mark.parametrize("entry", sorted(_NAMED_ENTRIES))
def test_every_name_form_a_named_entry_accepts_is_masked(entry: str) -> None:
    """The drift test for the named-value entries (r4 L5, M3): every name form the entry's
    regex accepts, through this module."""
    pattern, _ = _guardrails()[entry]
    words, value = _NAMED_ENTRIES[entry]
    leaked = []
    for form in _name_forms([[word, required] for word, required in words]):
        for join in ("=", " = ", ": "):
            text = f"{form}{join}{value}"
            assert re.search(pattern, text), text
            if value in _redact(text):
                leaked.append(text)
    assert leaked == []


@pytest.mark.parametrize("join", ["=", ": "])
def test_the_bare_telegram_token_is_masked(join: str) -> None:
    token = "123456789:AA" + "FAKEfake0123456789FAKEfake0123456"
    assert re.search(_guardrails()["telegram_bot_token"][0], token)
    assert _redact(f"BOT{join}{token} ok") == f"BOT{join}{_REDACTED} ok"


# Round 1 (r1cases.py), with its fake key shapes.
GH = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
SKP = "sk-proj-" + "FAKEfake0123456789abcdefFAKEfake0123456789ab"
AK = "AKIA" + "FAKEFAKE12345678"
XB = "xoxb-12345678901-12345678901-" + "FAKEfakeFAKEfakeFAKEfake"
SLACK = "https://hooks.slack.com/services/" + "T00FAKE00/B00FAKE00/" + "FAKEfakeFAKEfakeFAKEfake"
TG = "https://api.telegram.org/bot123456789:AAFAKEfakeFAKEfakeFAKEfakeFAKEfake_x/sendMessage"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlIn0.FAKEsigFAKEsig"
PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIEFAKEFAKEFAKE\nABCDEFFAKE==\n-----END RSA PRIVATE KEY-----"

R1_CASES = {
    "page: ws in password": "https://bot:hun ter2@h/v1",
    "page: lb in password": "see https://bot:hun\n    ter2@h/v1 is the hook",
    "page: tab in password": "see https://bot:hun\tter2@h/v1 is the hook",
    "page: query next line": "see https://u:pw@h/x\n  ?key=sk-1 and more",
    "page: ?# in password": "https://bot:8812#x@db/",
    "page: / in password": "https://bot:pa/ss@db",
    "page: :// in password": "https://bot:pa://ss@db",
    "bare ghp prose": f"Use {GH} to push.",
    "bare sk-proj prose": f"key is {SKP}.",
    "bare AKIA quoted": f'aws="{AK}"',
    "bare xoxb json": f'{{"t": "{XB}"}}',
    "slack hook": f"Post to {SLACK} daily",
    "telegram": f"Call {TG} now",
    "jwt prose": f"Bearer {JWT}",
    "pem": f"key:\n{PEM}\nend",
    "pem header only": "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAAFAKE\n(truncated)",
    "assign pw": "password=hunter22",
    "assign spaced": "token: s3cr3tvalue99",
    "tab query": "see https://h/x\t?key=sk-1 more",
    "mid-query break": "see https://h/x?a=1&\n    key=sk-1 more",
    "break before ? no indent": "see https://h/x\n?key=sk-1 more",
    "nbsp query": "see https://h/x ?key=sk-1 more",
    "nbsp password": "https://bot:hun ter2@h/v1",
    # honest text
    "H port+email": "Run at http://localhost:3000 and email support@acme.com",
    "H query email": "https://host:8080/path?email=a@b.com",
    "H npm scoped": "Install from https://registry.npmjs.org:443/@babel/core today",
    "H medium": "Read https://medium.com/@user for more",
    "H port then mention": "Open http://localhost:8080 and ping @oncall in Slack",
    "H md amp": "See https://docs.x.com/a\n&copy=2024 Acme",
    "H password required": "password: required",
    "H token expires": "the token: expires hourly",
    "H secret never": "secret: never tell anyone",
    "H env": 'api_key=os.environ["OPENAI_API_KEY"]',
    "H env placeholder": "Send the token: ${SLACK_BOT_TOKEN} in the header",
    "H commit": "Fixed in 1ac329e16b0f2c9a8e7d6c5b4a3f2e1d0c9b8a7f",
    "H uuid": "order 123e4567-e89b-12d3-a456-426614174000",
    "H b64img": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
    "H order": "Order ID ORD-2024-000123 and SKU AKIAMODEL",
    "H token count": "max_tokens: 4096 and token=streaming",
}

# Round 1's p2 and p5 additions (round 3's cases.py).
A = "FAKEfake0123456789"
R1_EXTRA = {
    "r1p2 clone x:ghp": f"https://x:{GH}@github.com/org/repo.git",
    "r1p5 anthropic": "sk-ant-api03-" + (A * 6)[:95],
    "r1p5 google": "AIza" + (A * 2)[:35],
    "r1p5 gitlab": "glpat-" + (A * 2)[:20],
    "r1p5 hf": "hf_" + (A * 2)[:34],
    "r1p5 BOT_TOKEN": "BOT_TOKEN=Zq8wYv3Lp9Kd",
    "r1p5 yaml client_secret": "client_secret: Zq8wYv3Lp9Kd2",
    "r1p5 PEM PKCS8": "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----",
}

# Round 2 (p1.py).
R2_CASES = {
    "owl p0 mid-query 2": "https://h/x?a=1&\n    key=sk-1",
    "owl p0 slack": "https://hooks.slack.com/services/" + "T00000000/B00000000/" + "XXXXXXXXXXXXXXXXXXXXXXXX",
    "owl p0 token sk-proj-abc": "token: sk-proj-abc",
    "H site residue": "Visit https://site.com:8443?ref=me@x.com",
    "H port/ email": "Login at http://localhost:8080/login as admin@acme.com",
    "r1 digits ws": "https://bot:12 34@h/v1",
    "r1 digits slash": "https://bot:8812/x@db/",
    "r1 two ws runs": "https://bot:hun ter 2@h",
    "r1 user dot": "https://my.bot:hun ter2@h",
    "r1 user dash": "https://ci-bot:hun ter2@h",
    "r1 empty user redis": "redis://:pa/ss@cache:6379/0",
    "r1 empty user ws": "redis://:hun ter2@cache:6379/0",
    "H a.com:x y@z": "see http://a.com:x y@z now",
    "H template port": "POST https://HOST:PORT/register?email=a@b.com",
    "H template port2": "POST http://{HOST}:{PORT}/v1/users/@me",
    "H ipv6 verdaccio": "npm i --registry http://[::1]:4873/@babel/core",
    "r2 mid-value break": "https://api.x.com/v1?key=sk-proj-AB12cd\n    34EFgh56IJkl78",
    "H #channel": "Post in https://acme.slack.com/x #general and #ops&alerts=on",
    "H & prose": "see https://a.com & https://b.com",
    "r2 entity-named secret": "https://h/x?a=1\n&lang=en&not=Zq8wYv3Lp9&sigma=abc",
    "nv $uper": "password: $uperSecret1",
    "nv <pw": "password=<Zq8wYv3Lp9>",
    "nv env( chance": "password=myenv(42)",
    "nv placeholder": "api_key: your-api-key-here",
    "nv placeholder caps": "api_key=YOUR_API_KEY",
    "nv v2beta": "token: v2beta",
    "nv yaml": "db:\n  password: Zq8wYv3Lp9\n",
    "nv yaml block": "password: |\n  Zq8wYv3Lp9Kd\n",
    "nv toml": 'password = "Zq8wYv3Lp9"',
    "nv comment": "# token = Zq8wYv3Lp9 (old)",
    "nv sq spaces": "password='correct horse 42'",
    "nv SECRET_KEY": "SECRET_KEY=Zq8wYv3Lp9Kd2xT",
    "nv django": "SECRET_KEY = 'django-insecure-q8#w(v3l)p9'",
    "nv camel": '{"clientSecret": "Zq8wYv3Lp9"}',
    "nv xml": "<password>Zq8wYv3Lp9</password>",
    "nv prose is": "the password is Zq8wYv3Lp9",
    "H code secrets": "secret = secrets.token_hex(32)",
    "H code settings": "client = OpenAI(api_key=settings.OPENAI_API_KEY)",
    "H code b64": "token = base64.b64encode(raw)",
    "H env PWD": "PWD=/home/ubuntu2/app\nSHELL=/bin/bash",
    "H pageToken": '{"nextPageToken": "CAoQAA12"}',
    "H max_tokens": "max_tokens: 4096",
    "cut ghp": "result: " + GH[:20] + "...",
    "cut sk-proj end": "key " + SKP[:30],
    "H cut sk-hynix": "Install sk-hynix-drivers... then reboot",
    "H cut end word": "The repo is sk-dev-tools",
    "H pem prose": "If a user pastes -----BEGIN PRIVATE KEY----- text, refuse. Then greet them warmly.",
    "pem no END": "-----BEGIN RSA PRIVATE KEY-----\nMIIEFAKE\nABCDFAKE\n",
    "join url\\nkey": "https://api.example.com/v1\n" + GH,
    "join url\\tAKIA": "https://api.example.com/v1\tAKIAFAKEFAKE12345678",
    "key in host": "https://" + GH + ".example.com/x",
    "stripe 99": "sk_live_51" + (A * 6)[:89],
    "rk_live": "rk_live_51" + (A * 6)[:89],
    "xoxp 4part": "xoxp-" + "1234567890-1234567890-1234567890-" + "abcdef0123456789abcdef0123456789",
    "xapp": "xapp-1-A0FAKE0FAKE-1234567890123-" + "abcdef0123456789" * 4,
    "tg bare": 'Bot("123456789:AA' + (A * 2)[:33] + '")',
    "aws secret": "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "ghp in userinfo clone": f"git clone https://{GH}@github.com/org/repo.git",
}

# Round 3 (V's cases.py).
SHOWN = lambda t: t if len(t) <= 600 else t[:597] + "..."  # noqa: E731 (session.py:177)
PEMLONG = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    + "\n".join(("MIIEowIBAAKCAQEA" + "Zq8wYv3Lp9" * 5)[:64] for _ in range(25))
    + "\n-----END RSA PRIVATE KEY-----"
)

R3_CASES = {  # V's new corpus: honest code and prose first, then shapes the page says it catches
    "H pydantic": "password: str = Field(...)",
    "H optional": "api_key: Optional[str] = None",
    "H headers": 'token = request.headers["Authorization"]',
    "H MAX_TOKEN": "MAX_TOKEN=4096",
    "H secret true": "secret: true",
    "H changeme123": "password: changeme123",
    "H gha": "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}",
    "H v2beta": "token: v2beta",
    "H md table": "| Setting | Value |\n|---|---|\n| DB_PASSWORD | Zq8wYv3Lp9 |\n| max_tokens | 4096 |",
    "H yaml comments": "db:\n  host: localhost  # dev\n  password: Zq8wYv3Lp9  # rotate monthly\n  pool: 5\n",
    "H toml": '[db]\npassword = "Zq8wYv3Lp9"  # prod\ntimeout = 30\n',
    "H .env comments": "# DB settings\nDB_HOST=localhost\nDB_PASSWORD=Zq8wYv3Lp9 # prod\n# old: DB_PASSWORD=Old1secret\n",
    "H sq spaces": "password='correct horse battery 42'",
    "H dotted digit": "api_key = settings.v2.key",
    "H dotted digit2": "token = config.oauth2.token",
    "H ident digit": "token = token_v2",
    "H email+mention": "Mail ops@acme.com or ping @oncall; docs at https://docs.acme.com/@team",
    "H ipv6": "npm i --registry http://[::1]:4873/@babel/core today",
    "H ipv6 user": "see http://[::1]:x y@z",
    "H #channel": "Post to https://acme.slack.com/archives/C1 #general then #ops&alerts=on",
    "H #channel2": "see https://x.com #ops&alerts=on",
    "H & amp": "Tom & Jerry at https://a.com & https://b.com &nbsp;x=1",
    "H pem prose": "If a user pastes -----BEGIN PRIVATE KEY----- text, refuse. Then greet them warmly.",
    "H cut end word": "The repo is sk-dev-tools",
    "H cut ...": "Loading the sk-learn-models...",
    "H cut hf": "load it with hf_hub_download",
    "H prefix word mid": "Install sk-hynix-drivers... then reboot",
    # rule 1 too much: a port followed by punctuation, then one whitespace run and an email
    "R1 port, email": "Log in at http://localhost:3000, qa@acme.com / Zq8wYv3Lp9",
    "R1 port. newline email": "Dashboard: http://localhost:3000.\nadmin@acme.com is the admin",
    "R1 port) email": "Staging (http://localhost:8080) ops@acme.com",
    "R1 lowercase port": "POST http://host:port/register?email=a@b.com",
    "R1 lowercase port2": "GET http://host:port/api/users/@me",
    "R1 $PORT": "Mastodon dev: http://localhost:$PORT/@admin",
    "R1 %PORT%": "open http://localhost:%PORT%/@admin",
    "R1 upper pw first": "https://bot:PASS WORD99@h/v1",
    "R1 ws 5": "https://bot:hun     ter2@h/v1",
    # named value too little
    "N md bold": "## Test account\n- **Email:** qa@acme.com\n- **Password:** Zq8wYv3Lp9",
    "N md bold2": "**Password**: Zq8wYv3Lp9",
    "N md backtick": "Password: `Zq8wYv3Lp9`",
    "N camel json": '{"clientId": "abc", "clientSecret": "Zq8wYv3Lp9Kd2xT"}',
    "N aws sts json": '{"Credentials": {"AccessKeyId": "ASIAFAKEFAKE12345678", "SecretAccessKey": "wJalrXUtnFEMIK7MDENGbPxRfiCYFAKEKEY12345", "SessionToken": "FwoGZXIvYXdzEFAKEfake0123456789"}}',
    "N accessToken": '{"accessToken": "ya29.a0AfFAKEfake0123456789"}',
    "N PGPASSWORD": "PGPASSWORD=Zq8wYv3Lp9 psql -h db",
    "N MYSQL_PWD": "MYSQL_PWD=Zq8wYv3Lp9",
    "N DB_PASS": "DB_PASS=Zq8wYv3Lp9\nSMTP_PASS=Zq8wYv3Lp9",
    "N php =>": "'password' => 'Zq8wYv3Lp9',",
    "N go :=": 'password := "Zq8wYv3Lp9"',
    "N cli flag": "docker login -u bot --password Zq8wYv3Lp9 registry.acme.com",
    "N bearer": 'curl -H "Authorization: Bearer Zq8wYv3Lp9Kd2xTq7" https://api.acme.com',
    "N yaml phrase": "password: correct horse battery staple",
    "N url\\npassword": "https://api.example.com/v1\npassword=Zq8wYv3Lp9",
    "N url\\nDB_PASSWORD": "https://api.example.com/v1\nDB_PASSWORD=Zq8wYv3Lp9",
    # key shapes
    "K discordapp": "Post to https://discordapp.com/api/webhooks/123456789012345678/"
    + (A * 4)[:68],
    "K discord": "Post to https://discord.com/api/webhooks/123456789012345678/"
    + (A * 4)[:68],
    "K akia host": "https://AKIAFAKEFAKE12345678.example.com/x",
    "K pem crlf no END": "-----BEGIN PRIVATE KEY-----\r\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\r\nZq8wYv3Lp9Zq8wYv3Lp9\r\n",
    "K pem 600 cut": SHOWN("cat key.pem\n" + PEMLONG),
    "K pem 600 cut short": SHOWN(
        "x" * 560
        + "\n-----BEGIN EC PRIVATE KEY-----\nMHcCAQEEIBZq8wYv3Lp9Zq8wYv3Lp9Zq8wYv3Lp9Zq8wYv3Lp9oAoGCCqGSM49"
    ),
    "K pem json cut": SHOWN(
        '{"type": "service_account", "private_key": "-----BEGIN PRIVATE KEY-----\\n'
        + "\\n".join(["MIIEvQIBADANBgkqhkiG9w0BAQEFAASC" * 2] * 20)
        + '\\n-----END PRIVATE KEY-----\\n"}'
    ),
    "K pem single line": "PRIVATE_KEY=-----BEGIN PRIVATE KEY-----MIIEvQIBADANBgkqhkiG9w0BAQEFAASCZq8w",
    "K ghp cut": SHOWN("x " * 290 + GH),
    "K tg cut": SHOWN(
        "y " * 280
        + "see https://api.telegram.org/bot123456789:AAFAKEfakeFAKEfakeFAKEfakeFAKEfake_x/sendMessage"
    ),
}

# The page's own outcomes (round 3): None means TH-9491's result stands.
PAGE_OUTCOMES = {  # the page's own words, as (input -> output); None = TH-9491's result stands
    "https://bot:hun ter2@h/v1": "https://h/v1",
    "https://bot:pa/ss@db": "https://db",
    "Run at http://localhost:3000 and email support@acme.com": None,
    "Open http://localhost:8080 and ping @oncall": None,
    "POST https://HOST:PORT/register?email=a@b.com": None,
    "http://{HOST}:{PORT}/v1/users/@me": None,
    "https://bot:8812#x@db/": None,
    "https://bot:8812/x@db/": None,
    "https://bot:12 34@h": None,
    "https://bot:hun ter 2@h": None,
    "See https://docs.x.com/a\n&copy=2024 Acme": None,
    "https://api.example.com/v1\n" + GH: "https://api.example.com/v1[redacted]",
    f"git clone https://{GH}@github.com/org/repo.git": "git clone https://github.com/org/repo.git",
    "https://" + GH + ".example.com/x": "[redacted]",
    "If a user pastes -----BEGIN PRIVATE KEY----- text, refuse. Then greet them warmly.": "If a user pastes [redacted] text, refuse. Then greet them warmly.",
    "password: required": None,
    "token=streaming": None,
    'api_key=os.environ["OPENAI_API_KEY"]': None,
    "secret = secrets.token_hex(32)": None,
    "token = base64.b64encode(raw)": None,
    "client = OpenAI(api_key=settings.OPENAI_API_KEY)": None,
    "PWD=/home/ubuntu2/app\nSHELL=/bin/bash": None,
    '{"nextPageToken": "CAoQAA12"}': None,
    '{"password": "Zq8wYv3Lp9"}': '{"password": "[redacted]"}',
    "DB_PASSWORD=Zq8wYv3Lp9": "DB_PASSWORD=[redacted]",
    "SECRET_KEY=Zq8wYv3Lp9Kd2xT": "SECRET_KEY=[redacted]",
    "SECRET_KEY = 'django-insecure-q8#w(v3l)p9'": "SECRET_KEY = '[redacted]'",
    "password: $uperSecret1": None,
    "<password>Zq8wYv3Lp9</password>": None,
    "the password is Zq8wYv3Lp9": None,
    "Install sk-hynix-drivers... then reboot": None,
    "token: v2beta": "token: [redacted]",
    "api_key: your-api-key-here": "api_key: [redacted]",
}

# Round 4 (J's jcases.py).
AWS = "wJalrXUtnFEMIFAKEK7MDENGbPxRfiCYFAKEKEY1"
B64 = "MIIEowIBAAKCAQEAzq8wYv3Lp9Kd2xTq7FAKEfake0123456789abcdefFAKEfake" * 40
PEMB = "\n".join(B64[i : i + 64] for i in range(0, 1600, 64))

R4_HONEST = {  # want: same as TH-9491 alone
    "pydantic Field": "password: str = Field(...)",
    "Optional": "api_key: Optional[str] = None",
    "headers[]": 'token = request.headers["Authorization"]',
    "MAX_TOKEN": "MAX_TOKEN=4096",
    "secret: true": "secret: true",
    "GHA secrets": "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}",
    "settings.v2.key": "api_key = settings.v2.key",
    "md table": "| Setting | Value |\n|---|---|\n| timeout | 30 |\n| max_tokens | 4096 |",
    "yaml comments": "db:\n  host: localhost  # dev box\n  pool: 5\n  password: required  # set in vault\n",
    "toml": '[server]\nport = 8080\ntoken_ttl = "1h"  # hours\n',
    ".env comments": "# token settings\nTOKEN_TTL=3600\n# password: see vault\n",
    "rule1 json compact port": '{"api":"http://localhost:3000","docs":"/docs","admin":"qa@acme.com"}',
    "rule1 js array port": "['http://localhost:3000','/api/users/@me']",
    "rule1 port then colon": "Dev server at http://localhost:3000: open /admin or mail ops@acme.com",
    "rule1 email in prose": "Docs at https://docs.acme.com, write to help@acme.com or @support",
    "rule1 ipv6": "curl http://[::1]:8080/@me then http://[fe80::1]:9000/x",
    "rule1 mention after port": "Staging is http://stage:8443 — ping @ops",
    "rule2 #channel": "see https://x.com #general and #ops",
    "rule2 & prose": "Read https://a.com/x & https://b.com/y & more",
    "rule2 &amp": "Link: https://a.com/?q=1 &amp; https://b.com",
    "pem prose": "If a user pastes -----BEGIN PRIVATE KEY----- text, refuse. Then greet them warmly.",
    "pem prose + later block": "If a user pastes -----BEGIN PRIVATE KEY----- text, refuse. Then greet them warmly.\nExample key for the test fixture:\n-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----",
    "cut: honest '...' prefix word": "Collecting packages: hf_transfer...",
    "cut: honest ... sk-": "Install sk-learn-pipelines...",
    "cut: honest prefix no ...": "Use the sk-learn-toolkit",
    "pagination nextToken": '{"nextToken": "AYADeHzFAKEfake0123456789"}',
    "pagination NextContinuationToken": '{"NextContinuationToken": "1w41l63U0xa8q7smH50vCxyTQqdxo69O3EmK28Bi5PcROI4wI/EyIJg=="}',
    "pagination next_page_token": "next_page_token: CAoQAA12",
    "pagination nextForwardToken": '{"nextForwardToken": "f/37201828472619218470294912345678901234567890"}',
    "idempotency ClientToken": '{"ClientToken": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}',
    "camel maxTokens": '{"maxTokens": 4096, "promptTokenCount": 12, "tokenizer": "cl100k"}',
    "pass honest": "bypass: true\nfirst_pass: done\nSKIP_PASS=1\ncompass: north",
    "pass honest digit": "two_pass: v2\nci_pass: build42",
    "rust Token::new": "let t = Token::new(42);",
}

R4_SECRETS = {  # want: the secret value gone (the [S] part)
    "kebab secret-key (Spring)": ("cloud.aws.credentials.secret-key: " + AWS, AWS),
    "kebab access-key k8s": (
        f"kubectl create secret generic s3 --from-literal=access-key=AKIAFAKEFAKE12345678 --from-literal=secret-key={AWS}",
        AWS,
    ),
    "kebab private-key": ("private-key: Zq8wYv3Lp9Kd2xTq7", "Zq8wYv3Lp9Kd2xTq7"),
    "aligned tfvars (terraform fmt)": (
        'admin_username = "admin"\npassword       = "Zq8wYv3Lp9"',
        "Zq8wYv3Lp9",
    ),
    "aligned yaml": ("user:         admin\npassword:     Zq8wYv3Lp9", "Zq8wYv3Lp9"),
    "aligned ini": (
        "[db]\nhost     = db.internal\npassword = Zq8wYv3Lp9\napi_key      = Zq8wYv3Lp9Kd2xTq7",
        "Zq8wYv3Lp9Kd2xTq7",
    ),
    "dotted password": ("DB_PASSWORD=Acme.Admin1", "Acme.Admin1"),
    "dotted random": ("password=x7.Kq9vL2mPz", "x7.Kq9vL2mPz"),
    "ya29 unquoted": (
        "GOOGLE_ACCESS_TOKEN=ya29.a0AfB_byCFAKEfake0123456789abcdefFAKE",
        "ya29.a0AfB_byCFAKEfake0123456789abcdefFAKE",
    ),
    "ya29 quoted": (
        '{"access_token": "ya29.a0AfB_byCFAKEfake0123456789abcdefFAKE"}',
        "ya29.a0AfB_byCFAKEfake0123456789abcdefFAKE",
    ),
    "yaml block scalar": ("password: |\n  Zq8wYv3Lp9Kd\n", "Zq8wYv3Lp9Kd"),
    "unquoted passphrase": ("password: correct horse battery 42", "battery 42"),
    "rule1 trailing space before break": (
        "hook: https://bot:hun \n    ter2@h/v1",
        "ter2",
    ),
    "rule1 blank line": ("hook: https://bot:hun\n\n  ter2@h/v1", "ter2"),
    "pem END-less escaped JSON, no name": (
        SHOWN(
            '{"key": "-----BEGIN RSA PRIVATE KEY-----\\n'
            + PEMB.replace("\n", "\\n")
            + '\\n-----END RSA PRIVATE KEY-----"}'
        ),
        B64[:40],
    ),
    "pem END-less flattened spaces": (
        SHOWN(
            "TLS_KEY=-----BEGIN RSA PRIVATE KEY----- "
            + PEMB.replace("\n", " ")
            + " -----END RSA PRIVATE KEY-----"
        ),
        B64[:40],
    ),
    "pem END-less encrypted, cut": (
        SHOWN(
            "-----BEGIN RSA PRIVATE KEY-----\nProc-Type: 4,ENCRYPTED\nDEK-Info: AES-128-CBC,3F2A9C1B7E4D6F80A1B2C3D4E5F60718\n\n"
            + PEMB
            + "\n-----END RSA PRIVATE KEY-----"
        ),
        B64[:40],
    ),
    "pem END-less Read tool + arrow": (
        SHOWN(
            "\n".join(
                f"{i + 1:6}→{line}"
                for i, line in enumerate(
                    ("-----BEGIN RSA PRIVATE KEY-----\n" + PEMB).split("\n")
                )
            )
        ),
        B64[:40],
    ),
    "camel passwd/pwd": (
        '{"dbPwd": "Zq8wYv3Lp9", "rootPasswd": "Zq8wYv3Lp9"}',
        "Zq8wYv3Lp9",
    ),
    "pass camel": ('{"smtpPass": "Zq8wYv3Lp9"}', "Zq8wYv3Lp9"),
}

AZ = ("FAKEazureFAKEkey0123456789abcdefFAKE+/" * 3)[:86] + "=="
FB = "AIza" + "FAKEfake0123456789FAKEfake012345678"

R4_DRIFT = [
    ("aws_secret_key", f"aws_secret_key = {AWS}"),
    ("aws_secret_key", f"AWS_SECRET_ACCESS_KEY={AWS}"),
    ("aws_secret_key", f"aws-secret-key: {AWS}"),
    ("aws_secret_key", f"awssecretkey={AWS}"),
    ("aws_secret_key", f"AWSSecretKey={AWS}"),
    ("azure_storage_key", f"AZURE_STORAGE_KEY={AZ}"),
    ("azure_storage_key", f"azure_key = {AZ}"),
    ("azure_storage_key", f"azure-storage-key: {AZ}"),
    ("azure_storage_key", f"AzureStorageAccountKey={AZ}"),
    ("firebase_api_key", f"FIREBASE_API_KEY={FB}"),
    ("firebase_api_key", f"firebasekey: {FB}"),
    ("mailchimp_api_key", "MC key 0123456789abcdef0123456789abcdef" + "-us12"),
    (
        "discord_token",
        "MTAxNTI3OTM4NjQ5Nzc0MDAzMg" + ".GFAKEf." + "FAKEfake0123456789FAKEfake012",
    ),
    ("telegram_bot_token", "123456789:AAFAKEfakeFAKEfakeFAKEfakeFAKEfake_x"),
    ("database_url", "postgres://app:Zq8wYv3Lp9@db.internal:5432/app"),
    ("connection_string", "Server=db;Database=app;User Id=sa;Pwd=Zq8wYv3Lp9;"),
    ("connection_string", "Host=db;Username=app;Password=Zq8wYv3Lp9;"),
]


# -- S2: the design page's cases, rule by rule --------------------------------------------------

_B64 = "MIIEowIBAAKCAQEAzq8wYv3Lp9Kd2xTq7FAKEfake0123456789abcdefFAKEfake" * 40
_PEM_JSON = "\\n".join(_B64[i : i + 64] for i in range(0, 1600, 64))
_SHOWN = lambda text: text if len(text) <= 600 else text[:597] + "..."  # noqa: E731
_GH = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
_PW = "Zq8wYv3Lp9"

_MASKED = {
    # Rule 1: a password broken by whitespace, /, ? or #, with up to 64 characters a side.
    "rule1 space": ("https://bot:hun ter2@h/v1", "https://h/v1"),
    "rule1 break + indent": (
        "see https://bot:hun\n    ter2@h/v1 is the hook",
        "see https://h/v1 is the hook",
    ),
    "rule1 slash": ("https://bot:pa/ss@db", "https://db"),
    "rule1 empty user": ("redis://:hun ter2@cache:6379/0", "redis://cache:6379/0"),
    "rule1 64 a side": ("https://bot:" + "a" * 64 + " b@h/v1", "https://h/v1"),
    # Rule 2: a query after a break.
    "rule2 next line": (
        "see https://u:pw@h/x\n  ?key=sk-1 and more",
        "see https://h/x\n  ?key= and more",
    ),
    "rule2 query ends in &": (
        "see https://h/x?a=1&\n    key=sk-1 more",
        "see https://h/x\n    key= more",
    ),
    # A named value: up to 16 spaces or tabs of alignment, or up to 4 whitespace with a break.
    "named json": ('{"password": "Zq8wYv3Lp9"}', '{"password": "[redacted]"}'),
    "named env": ("DB_PASSWORD=Zq8wYv3Lp9", "DB_PASSWORD=[redacted]"),
    "named django": (
        "SECRET_KEY = 'django-insecure-q8#w(v3l)p9'",
        "SECRET_KEY = '[redacted]'",
    ),
    "named 16 spaces": (
        "password" + " " * 16 + "= " + _PW,
        "password" + " " * 16 + "= [redacted]",
    ),
    "named break after :": ("password:\n   " + _PW, "password:\n   [redacted]"),
    "named space break after :": ("password: \n  " + _PW, "password: \n  [redacted]"),
    "named break after =": ("password=\n   " + _PW, "password=\n   [redacted]"),
    "named break before :": ("password\n   : " + _PW, "password\n   : [redacted]"),
    "named break before =": ("password\n   = " + _PW, "password\n   = [redacted]"),
    "named md bold label": ("- **Password:** " + _PW, "- **Password:** [redacted]"),
    "named md bold name": ("**Password**: " + _PW, "**Password**: [redacted]"),
    "named kebab": (
        "cloud.aws.credentials.secret-key: " + _PW,
        "cloud.aws.credentials.secret-key: [redacted]",
    ),
    "named camel": (
        '{"clientSecret": "Zq8wYv3Lp9Kd2xT"}',
        '{"clientSecret": "[redacted]"}',
    ),
    "named DB_PASS": ("DB_PASS=" + _PW, "DB_PASS=[redacted]"),
    # An upper-case name joined by "-" (r4 L2): "-" is a join, not only a boundary.
    "named DB-PASS": ("DB-PASS=" + _PW, "DB-PASS=[redacted]"),
    "named SMTP-PASS": ("SMTP-PASS=" + _PW, "SMTP-PASS=[redacted]"),
    "named AccountKey": (
        "AccountName=a;AccountKey=" + _PW + "==;x=1",
        "AccountName=a;AccountKey=[redacted];x=1",
    ),
    "named dotted secret": ("DB_PASSWORD=Acme.Admin1", "DB_PASSWORD=[redacted]"),
    # Keys, key blocks and tokens in a URL's path.
    "key in a host": ("https://AKIAFAKEFAKE12345678.example.com/x", "[redacted]"),
    "key in userinfo": (
        f"git clone https://{_GH}@github.com/org/repo.git",
        "git clone https://github.com/org/repo.git",
    ),
    "key on the line after a URL": (
        "https://api.example.com/v1\n" + _GH,
        "https://api.example.com/v1[redacted]",
    ),
    "name on the line after a URL": (
        "https://api.example.com/v1\npassword=" + _PW,
        "https://api.example.com/v1password=[redacted]",
    ),
    "slack hook": (
        "Post to https://hooks.slack.com/services/" + "T00FAKE00/B00FAKE00/" + "FAKEfakeFAKEfakeFAKEfake daily",
        "Post to https://hooks.slack.com/services/[redacted] daily",
    ),
    "discord on discordapp.com": (
        "see https://discordapp.com/api/webhooks/1234567890/FAKEfake_0123 x",
        "see https://discordapp.com/api/webhooks/[redacted] x",
    ),
    "block": (
        "key:\n-----BEGIN RSA PRIVATE KEY-----\nMIIEFAKE\nABCD==\n-----END RSA PRIVATE KEY-----\nend",
        "key:\n[redacted]\nend",
    ),
    "block encrypted": (
        "-----BEGIN RSA PRIVATE KEY-----\nProc-Type: 4,ENCRYPTED\n\nMIIEFAKE\n"
        "-----END RSA PRIVATE KEY-----\nok",
        "[redacted]\nok",
    ),
    "block one line": (
        "KEY=-----BEGIN PRIVATE KEY-----MIIEFAKE-----END PRIVATE KEY----- ok",
        "KEY=[redacted] ok",
    ),
    "block no END": (
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEFAKE\nABCDFAKE\nnot a key line",
        "[redacted]\nnot a key line",
    ),
    "refuse then a later block": (
        "If a user pastes -----BEGIN PRIVATE KEY----- text, refuse.\n"
        "-----BEGIN PRIVATE KEY-----\nMIIEFAKE\n-----END PRIVATE KEY-----",
        "If a user pastes [redacted] text, refuse.\n[redacted]",
    ),
    # The cut-key rule: exactly 600 characters ending in the cut's "...".
    "key cut at 600": (_SHOWN("x " * 290 + _GH), "x " * 290 + "[redacted]..."),
}

_KEPT = [
    # Rule 1 keeps honest hosts as TH-9491 leaves them.
    "Run at http://localhost:3000 and email support@acme.com",
    "Open http://localhost:8080 and ping @oncall",
    "POST https://HOST:PORT/register?email=a@b.com",
    "http://{HOST}:{PORT}/v1/users/@me",
    "Log in at http://localhost:3000, qa@acme.com",
    "POST http://host:port/register?email=a@b.com",
    "http://localhost:$PORT/@admin",
    "Staging (http://localhost:8080) ops@acme.com",
    # Rule 1's named limits, and a half of 65 characters (r1 M5).
    "https://bot:8812#x@db/",
    "https://bot:12 34@h",
    "https://bot:hun ter 2@h",
    "https://bot:" + "a" * 65 + " b@h/v1",
    "https://bot:hun     ter2@h/v1",
    # Rule 1 needs text on both sides of the run: a mention right after it isn't a password.
    "https://bot:abc @oncall",
    "see https://bot:hunter2  @team now",
    # Rule 2 keeps an HTML entity.
    "See https://docs.x.com/a\n&copy=2024 Acme",
    # Named values: references, code and honest values.
    "password: required",
    "token=streaming",
    'api_key=os.environ["OPENAI_API_KEY"]',
    "secret = secrets.token_hex(32)",
    "token = base64.b64encode(raw)",
    "client = OpenAI(api_key=settings.OPENAI_API_KEY)",
    "api_key = settings.v2.key",
    "PWD=/home/ubuntu2/app\nSHELL=/bin/bash",
    '{"nextPageToken": "CAoQAA12"}',
    "two_pass: v2",
    "let t = Token::new(42);",
    "password: $uperSecret1",
    "<password>Zq8wYv3Lp9</password>",
    "the password is Zq8wYv3Lp9",
    "password" + " " * 17 + "= " + _PW,
    # A quoted value for each reference form (r1 M5).
    'token: "{{ secrets.TOKEN_1 }}"',
    'token: "$TOKEN_1"',
    'token: "${TOKEN_1}"',
    'token: "<token_1>"',
    'token: "os.environ.get(TOKEN_1)"',
    'token: "process.env.TOKEN_1"',
    'token: "env(TOKEN_1)"',
    # A break with 5 whitespace characters on either side of the = or : (r1 M2).
    "password:\n    " + _PW,
    "password=\n    " + _PW,
    "password\n    : " + _PW,
    "password\n    = " + _PW,
    # The cut-key rule needs both the length and the "..." (r4 L1).
    "Collecting packages: hf_transfer...",
    "Install sk-hynix-drivers... then reboot",
    "The repo is sk-dev-tools",
    # 601 characters, so not the cut.
    "x " * 289 + _GH[:20] + "...",
]


@pytest.mark.parametrize("name", sorted(_MASKED))
def test_the_pages_masked_cases(name: str) -> None:
    text, expected = _MASKED[name]
    assert _redact(text) == expected


# Written out, not read from the module: a name dropped there must fail here.
_PAGINATION_NAMES = (
    "ClientToken",
    "NextContinuationToken",
    "NextToken",
    "continuationToken",
    "nextPageToken",
    "nextToken",
    "pageToken",
)


@pytest.mark.parametrize("name", _PAGINATION_NAMES)
def test_every_pagination_name_keeps_its_value(name: str) -> None:
    """Each "not a name" is kept, not only the ones the page quotes."""
    text = '{"%s": "CAoQAA12"}' % name
    assert _redact(text) == text


@pytest.mark.parametrize("text", _KEPT, ids=range(len(_KEPT)))
def test_the_pages_kept_cases_come_out_as_th9491_leaves_them(text: str) -> None:
    assert _redact(text) == _base(text)


# cr1 M1 (change review r1, R's hostname.py): a host whose name ends in a name, with a port,
# as Compose and Kubernetes service names do. Its ":" opens no value, so the URL stays as
# TH-9491 leaves it.
_HOST_NAMES = [
    "Call the issuer at http://auth-token:8080/v1/issue before each run.",
    "Vault runs at http://vault-secret:8200/v1/kv in compose.",
    "The token service: http://token:9000/oauth/token",
    "GET http://api-key:3000/keys then POST",
    "Use http://secret:8080 for local dev.",
    "Use http://my-password:5000/reset to reset.",
    "postgres://app@db-password:5432/app",
    "see http://Auth-Token:8080/v1 now",
    "base_url: http://token-svc:8080/v1",
    "http://auth-token.internal:8080/v1",
]


@pytest.mark.parametrize("text", _HOST_NAMES, ids=range(len(_HOST_NAMES)))
def test_a_host_named_like_a_secret_keeps_its_port_and_path(text: str) -> None:
    assert _redact(text) == _base(text)


def test_a_named_value_after_a_url_in_one_run_is_still_masked() -> None:
    """cr1 M1: only a port after the name keeps it; this "=" opens a value."""
    out = _redact("url=https://api.x.com,token=Zq8wYv3Lp9Kd next words")

    assert "zq8wyv3lp9kd" not in out.lower()
    assert out.endswith(" next words")


# -- Rule 1b: an @ before the password's / (design addendum v2.5.2) -----------------------------
# Each cleaned row as (input, output). The chat stream runs these same rows at every split
# (test_builder_chat.py), and outbound runs the first four (test_outbound.py).
R1B_CLEANED = {
    # Dileep's #278 threads P1 and P3: a password holding / or #, rule 1's own.
    "Dileep P1": (
        "postgres://admin:Xk9/Qp+Zr8@db.example.com:5432/app",
        "postgres://db.example.com:5432/app",
    ),
    "Dileep P3": ("https://user:pa#ss@host.example/x", "https://host.example/x"),
    # Ash's two: an @ in the password, and an @ in the user.
    "Ash P@ss": (
        "postgres://admin:P@ss/Xk9@db.example.com:5432/app",
        "postgres://db.example.com:5432/app",
    ),
    "Ash smtp": (
        "smtp://me@gmail.com:app/pass@smtp.gmail.com:587",
        "smtp://smtp.gmail.com:587",
    ),
    "redis": ("redis://:p@w/x9@cache.internal:6379/0", "redis://cache.internal:6379/0"),
    ":99": (
        "postgres://admin:P@ss:99/Xk9@db.example.com/app",
        "postgres://db.example.com/app",
    ),
    # M2's three hosts.
    "localhost": (
        "postgres://admin:P@ss/Xk9@localhost/app",
        "postgres://localhost/app",
    ),
    "a single label": ("postgres://admin:P@ss/Xk9@db/app", "postgres://db/app"),
    "IPv6 with a port": (
        "postgres://admin:P@ss/Xk9@[::1]:5432/app",
        "postgres://[::1]:5432/app",
    ),
    # M3 and M-A: a one-URL value runs step 1 on its joined token.
    "M3 broken at Xk\\n9": (
        "postgres://admin:P@ss/Xk\n9@db.example.com:5432/app",
        "postgres://db.example.com:5432/app",
    ),
    "M-A broken at Pa\\nss": (
        "postgres://admin:Pa\nss@w/x9@db.example.com/app",
        "postgres://db.example.com/app",
    ),
    "M-A redis": (
        "redis://:pa\nss@w/x9@cache.internal:6379/0",
        "redis://cache.internal:6379/0",
    ),
    # M-B's hosts.
    "M-B postgres_db:5432": (
        "postgres://admin:P@ss/Xk9@postgres_db:5432/app",
        "postgres://postgres_db:5432/app",
    ),
    "M-B my_db/app": ("postgres://admin:P@ss/Xk9@my_db/app", "postgres://my_db/app"),
    "M-B a backtick": ("`redis://:p@w/x9@redis`", "`redis://redis`"),
    "M-B db:${DB_PORT}": (
        "postgres://admin:P@ss/Xk9@db:${DB_PORT}/app",
        "postgres://db/app",
    ),
    "M-B db:$DB_PORT": (
        "postgres://admin:P@ss/Xk9@db:$DB_PORT/app",
        "postgres://db/app",
    ),
    "M-B db?sslmode": ("postgres://admin:P@ss/Xk9@db?sslmode=disable", "postgres://db"),
    "M-B redis#0": ("redis://:p@w/x9@redis#0", "redis://redis"),
    "M-B )": ("(amqp://u:p@w/x9@rabbitmq)", "(amqp://rabbitmq)"),
    "M-B ,": ("amqp://u:p@w/x9@rabbitmq, then", "amqp://rabbitmq, then"),
    "M-B ;": ("amqp://u:p@w/x9@rabbitmq; then", "amqp://rabbitmq; then"),
    "M-B >": ("<amqp://u:p@w/x9@rabbitmq>", "<amqp://rabbitmq>"),
    # TH-9491 refuses the stray ], as it would with no password.
    "M-B ]": ("[amqp://u:p@w/x9@rabbitmq]", "[[redacted]"),
    "M-B zoned IPv6": (
        "postgres://admin:P@ss/Xk9@[fe80::1%25en0]:5432/app",
        "postgres://[fe80::1%25en0]:5432/app",
    ),
    # M4: one sorted, merged list of rule 1's and 1b's spans.
    "M4 1b then rule 1": (
        "see postgres://admin:P@ss/Xk9@db.example.com/app and https://bot:hun ter2@h/v1 now",
        "see postgres://db.example.com/app and https://h/v1 now",
    ),
    "M4 rule 1 then 1b": (
        "see https://bot:hun ter2@h/v1 and postgres://admin:P@ss/Xk9@db.example.com/app now",
        "see https://h/v1 and postgres://db.example.com/app now",
    ),
    "M4 overlapping": ("a://u:x://v:p@h/x@host.example", "a://host.example"),
}
# M1 and M-C: a user with no password keeps its host, and a path's @ before no host is left
# alone, as TH-9491 leaves them.
R1B_KEPT = [
    "http://admin@localhost:8080/bob@example.com",
    "ssh://git@gitlab.example.com:2222/ops@infra.example/repo.git",
    "https://ci@registry.example.com:8443/lodash@4.17.21",
    "ssh://git@host.example:22/org@team/repo",
    "Read https://medium.com/@user for more",
    "Install from https://registry.npmjs.org:443/@babel/core today",
    "http://admin@[::1]/bob@example.com",
    "ssh://git@[fd00::5]/org@team/repo",
    "http://admin@[::1]:8080/bob@example.com",
    # A user with no : at all.
    "https://ci@registry.example.com/lodash@4.17.21",
    "ssh://git@github.com/org@team/repo",
    # A path's @ with no host after it: rule 1b doesn't hold, and TH-9491 removes the userinfo.
    "Mail https://u:p@h.example/to@~team now",
]
# The addendum's named limits, pinned as they come out.
R1B_LIMITS = {
    # L1: the host moves; the password is still removed.
    "https://u:p@api.example.com/a@b.com": "https://b.com",
    "https://u:p@api.example.com/lodash@4.17.21": "https://4.17.21",
    "https://u:p@api.example.com/react@18?dev": "https://18",
    "git+https://u:${TOKEN}@git.example.com/repo.git@main": "git+https://main",
    # L2: the scp form inside a scheme.
    "git+ssh://git@github.com:org/repo@v1.2.0": "git+ssh://v1.2.0",
    "git+ssh://git@github.com:org/repo@main": "git+ssh://main",
    # M-C: a port rule 1 doesn't read.
    "http://admin@localhost:{{port}}/bob@example.com": "http://example.com",
    "http://admin@localhost:$(PORT)/bob@example.com": "http://example.com",
    "http://admin@localhost:/bob@example.com": "http://example.com",
    # Still leaks, as on the base: a password that reads as a port, an empty segment, two /,
    # a segment over its bound, %40, and a line break inside the span in prose.
    "smtp://me@gmail.com:1234/pass@smtp.gmail.com": "smtp://gmail.com:1234/pass@smtp.gmail.com",
    "postgres://admin:P@ss/@db.example.com/app": "postgres://ss/@db.example.com/app",
    "postgres://admin:P@ss/a/b@db.example.com/app": "postgres://ss/a/b@db.example.com/app",
    "postgres://admin:P@ss/" + "a" * 65 + "@db.example.com/app": "postgres://ss/"
    + "a" * 65
    + "@db.example.com/app",
    "postgres://admin:P%40ss/Xk9@db.example.com/app": "postgres://admin/Xk9@db.example.com/app",
    "see postgres://admin:P@ss/Xk\n9@db.example.com/app now": "see postgres://ss/Xk\n9@db.example.com/app now",
}


@pytest.mark.parametrize("name", list(R1B_CLEANED))
def test_rule_1b_cleans_the_addendums_rows(name: str) -> None:
    text, expected = R1B_CLEANED[name]
    assert _redact(text) == expected
    assert _redact(expected) == expected


@pytest.mark.parametrize("text", R1B_KEPT, ids=range(len(R1B_KEPT)))
def test_rule_1b_leaves_these_to_th9491(text: str) -> None:
    assert _redact(text) == _base(text)


@pytest.mark.parametrize("text", list(R1B_LIMITS), ids=range(len(R1B_LIMITS)))
def test_rule_1bs_named_limits_are_pinned(text: str) -> None:
    assert _redact(text) == R1B_LIMITS[text]


def test_rule_1bs_bounds_hold_at_each_edge() -> None:
    """The bounds (L3): 64 before the first : or @, 64 from the last @ to the /, 1-64 from the
    / to the path's @, and 129 up to the last @."""
    tail = "@db.example.com/app"
    assert (
        _redact("postgres://" + "a" * 64 + ":P@ss/Xk9" + tail)
        == "postgres://db.example.com/app"
    )
    assert "Xk9" in _redact("postgres://" + "a" * 65 + ":P@ss/Xk9" + tail)
    assert (
        _redact("postgres://admin:P@" + "s" * 64 + "/Xk9" + tail)
        == "postgres://db.example.com/app"
    )
    assert "Xk9" in _redact("postgres://admin:P@" + "s" * 65 + "/Xk9" + tail)
    assert (
        _redact("postgres://admin:P@ss/" + "x" * 64 + tail)
        == "postgres://db.example.com/app"
    )
    # Before the authority's last @: 129 characters cleans, 130 doesn't.
    for before, cleaned in ((129, True), (130, False)):
        user = "a:P@" + "q" * (before - 4) + "@"
        out = _redact("postgres://" + user + "s/Xk9" + tail)
        assert (out == "postgres://db.example.com/app") is cleaned, before


def test_rule1_returns_one_sorted_list_with_overlaps_merged() -> None:
    """M4: `remove` needs spans in order and apart."""
    text = "a://u:x://v:p@h/x@host.example"
    assert secret_shapes.rule1(text) == [(4, 18)]
    assert secret_shapes.remove(text, secret_shapes.rule1(text)) == "a://host.example"


# -- Change review r2 (cr2) ----------------------------------------------------------------------


def test_a_one_url_value_cut_at_600_masks_its_cut_key() -> None:
    """cr2 M5: the join-first branch keeps the raw value's 600-character test, so a key cut short
    at the end is masked to it, joined or not."""
    for between in ("/tokens/", "\n/tokens/"):
        value = _SHOWN("https://h.example/" + "a" * 547 + between + _GH)
        assert len(value) == 600

        out = _redact(value)

        assert out.endswith("/tokens/[redacted]..."), between
        assert "ghp_A1b2" not in out


@pytest.mark.parametrize(
    "text, expected",
    [
        ("password:2024/Zq8wYv3Lp9 ok", "password:[redacted] ok"),
        ("see token:8080/x9 now", "see token:[redacted] now"),
    ],
)
def test_a_name_with_a_port_like_value_outside_a_host_is_masked(
    text: str, expected: str
) -> None:
    """cr2 M5: only a name in a URL's host is skipped for its port (cr1 M1); outside a host, a
    value that begins with digits and a / is still a value."""
    assert _redact(text) == expected


def test_a_one_url_value_whose_password_held_a_space_is_joined_whole() -> None:
    """cr2 L1, a named limit: once rule 1 removes a password broken by a space, the value is one
    URL, and TH-9491's join takes the next line into it. No secret goes; the line is lost."""
    assert _redact("redis://:pa ss@h:6379\nREDIS_DB=0") == "redis://h"


@pytest.mark.parametrize(
    "text, expected",
    [
        # `_URL_END` stops at the next ://, so the URL after a span in a host is kept.
        (
            "see x://password=Zq8w1,b/https://h.example/docs now",
            "see [redacted]https://h.example/docs now",
        ),
        # Rule 1b's host: localhost with a trailing dot, and with a port rule 1 doesn't read.
        (
            "postgres://admin:P@ss/Xk9@localhost.:5432/app",
            "postgres://localhost.:5432/app",
        ),
        (
            "postgres://admin:P@ss/Xk9@localhost:{{port}}/app",
            "postgres://localhost/app",
        ),
    ],
)
def test_the_url_end_and_rule_1bs_localhost_host(text: str, expected: str) -> None:
    """cr2 L5."""
    assert _redact(text) == expected


def test_a_url_query_before_a_key_body_doesnt_defeat_the_cut_rules() -> None:
    """r1 M1: Terraform state cut at 600 with a query URL before private_key_pem. Both
    600-character tests are made on the raw value, which step 2 would shorten."""
    state = _SHOWN(
        '{"attributes": {"endpoint": "https://sts.amazonaws.com/?Action=AssumeRole&Version=2011-'
        '06-15", "private_key_pem": "-----BEGIN RSA PRIVATE KEY-----\\n'
        + _PEM_JSON
        + '\\n-----END RSA PRIVATE KEY-----\\n"}}'
    )
    assert len(state) == 600

    out = _redact(state)

    assert "MIIEowIBAAKC" not in out and "FAKEfake0123" not in out
    assert out.endswith('"private_key_pem": "[redacted]')
    # A key cut short at 600 is masked to the end, behind a query too.
    shown = _SHOWN("see https://h.example/x?a=1 then " + "y " * 270 + _GH)
    assert _redact(shown).endswith(" [redacted]...")


def test_the_page_outcomes_hold() -> None:
    """The page's own outcomes, as round 3 listed them: None means TH-9491's result stands."""
    for text, expected in PAGE_OUTCOMES.items():
        assert _redact(text) == (_base(text) if expected is None else expected), text


# -- S3 and S4: the corpus ----------------------------------------------------------------------


def _corpus() -> dict[str, str]:
    cases = {}
    for label, group in (
        ("r1", R1_CASES),
        ("r1 extra", R1_EXTRA),
        ("r2", R2_CASES),
        ("r3", R3_CASES),
        ("r4 honest", R4_HONEST),
    ):
        cases.update({f"{label}: {name}": text for name, text in group.items()})
    cases.update({f"r4 secret: {name}": text for name, (text, _) in R4_SECRETS.items()})
    cases.update(
        {f"r4 drift {i}: {name}": text for i, (name, text) in enumerate(R4_DRIFT)}
    )
    return cases


CORPUS = _corpus()
# Pieces of the fake secrets in the corpus. None may survive cleaning, outside the limits below.
SECRET_PARTS = (
    "A1b2C3d4E5f6G7h8I9j0",
    "FAKEfake0123456789",
    "FAKEFAKE12345678",
    "FAKEfakeFAKEfakeFAKEfake",
    "AAFAKEfake",
    "FAKEsigFAKEsig",
    "MIIEFAKE",
    "ABCDEFFAKE",
    "ABCDFAKE",
    "MHcCA",
    "hunter2",
    "ter2",
    "Zq8wYv3Lp9",
    "s3cr3tvalue99",
    "wJalrXUtnFEMI",
    "MIIEowIBAAKC",
    "MIIEvQIBADAN",
    "b3BlbnNzaC1rZXkt",
    "Acme.Admin1",
    "x7.Kq9vL2mPz",
    "ya29.a0Af",
    "FAKEazureFAKEkey",
    "battery",
    "correct horse",
    "sk-1",
    "pa/ss",
    "pa://ss",
    "q8#w(v3l)p9",
    "FwoGZXIv",
    "XXXXXXXXXXXXXXXX",
    "abcdef0123456789abcdef",
    "A0FAKE0FAKE",
    "8812",
    "ghp_A1b2C3d4",
)
# Cases where a secret is kept, each a limit the design page names.
SECRETS_KEPT = {
    "r1: page: ?# in password": "a password that starts with digits",
    "r2: r1 digits slash": "a password that starts with digits",
    "r2: nv <pw": "a value starting with < reads as a reference",
    "r2: nv yaml block": "a YAML block scalar (r4 L6)",
    "r2: nv xml": "XML elements aren't matched",
    "r2: nv prose is": "prose such as 'the password is' isn't matched",
    "r3: H md table": "a Markdown table cell has no = or :",
    "r3: R1 port, email": "honest text: the value after the port isn't a password",
    "r3: R1 ws 5": "a run of 5 spaces in a password",
    "r3: N aws sts json": "ASIA is no prefix; AccessKeyId is no name",
    "r3: N php =>": "=> isn't matched (r3 H3)",
    "r3: N go :=": ":= isn't matched (r3 H3)",
    "r3: N cli flag": "--password X isn't matched (r3 H3)",
    "r3: N bearer": "Authorization: Bearer X isn't matched (r3 H3)",
    "r3: N yaml phrase": "an unquoted passphrase with spaces (r4 L6)",
    "r4 secret: yaml block scalar": "a YAML block scalar (r4 L6)",
    "r4 secret: unquoted passphrase": "an unquoted passphrase with spaces (r4 L6)",
    "r4 secret: rule1 blank line": "a blank line inside a password (r4 L3)",
    "r4 secret: camel passwd/pwd": "dbPwd isn't a name (r4 L6)",
    "r4 secret: pass camel": "smtpPass, a best-effort form (TH-9555)",
    "r4 drift 15: connection_string": "Pwd= isn't a name",
    "r4 drift 11: mailchimp_api_key": "Mailchimp's shape is left out on purpose",
    "r4 drift 12: discord_token": "the bare Discord bot token is a named limit (r4 L5)",
    "r2: cut ghp": "a key cut short in a text that isn't the 600-character cut (r4 L1)",
    "r2: cut sk-proj end": "shorter than the key's least length, and not the cut (r4 L1)",
    "r4 honest: pagination nextToken": "a pagination name's value is honest (r4 L2)",
}
# Text with no secret that the rules change, each as the design page says they would.
HONEST_CHANGED = {
    "r2: H a.com:x y@z": "rule 1 reads 'x y' as a password broken by a space",
    "r2: nv placeholder": "a placeholder 16 or more characters long (page, what can go wrong)",
    "r2: nv v2beta": "token: v2beta is masked (page, what can go wrong)",
    "r2: H pem prose": "the bare header is masked; the instruction after it is kept",
    "r3: H changeme123": "a letter and a digit",
    "r3: H v2beta": "token: v2beta is masked (page, what can go wrong)",
    "r3: H ident digit": "an identifier with a digit isn't code",
    "r3: H #channel2": "a markdown anchor after an address loses its values (r3 L6)",
    "r3: H pem prose": "the bare header is masked; the instruction after it is kept",
    "r4 honest: pem prose": "the bare header is masked; the instruction after it is kept",
    "r4 honest: pagination next_page_token": "a best-effort form (TH-9555)",
    "r4 honest: pagination nextForwardToken": "a best-effort form (TH-9555)",
}


def test_every_secret_in_the_corpus_is_masked() -> None:
    leaked = {
        name: [part for part in SECRET_PARTS if part in text and part in _redact(text)]
        for name, text in CORPUS.items()
        if name not in SECRETS_KEPT
    }
    assert {name: parts for name, parts in leaked.items() if parts} == {}


def test_honest_text_in_the_corpus_comes_out_unchanged() -> None:
    """IDs, hashes, base64, code, markdown and pagination names: as TH-9491 leaves them."""
    changed = [
        name
        for name, text in CORPUS.items()
        if not any(part in text for part in SECRET_PARTS)
        and name not in HONEST_CHANGED
        and _redact(text) != _base(text)
    ]
    assert changed == []
    for name in HONEST_CHANGED:
        assert _redact(CORPUS[name]) != _base(CORPUS[name]), name


def test_cleaning_the_corpus_twice_changes_nothing_and_logs_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """S4: idempotence, and one log line per call, none on the second pass."""
    for name, text in CORPUS.items():
        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger="fi.alk.providers.agent"):
            once = _redact(text)
            first = _logged(caplog)
            caplog.clear()
            twice = _redact(once)
            second = _logged(caplog)
        assert twice == once, name
        assert len(first) <= 1 and second == [], name


# -- S5 and S5b: speed --------------------------------------------------------------------------

_SETUP = r"""
import logging, random, string, time
logging.disable(logging.WARNING)
from fi.alk.providers.agent import _redact
GH = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
def rep(unit):
    return (unit * (M // len(unit) + 1))[:M]
def dense():
    pick, unreserved, out, size = random.Random(9), string.ascii_letters + string.digits + "-._~", [], 0
    while size < M:
        host = "".join(pick.choice(string.ascii_lowercase) for _ in range(8))
        key = "".join(pick.choice(unreserved) for _ in range(10))
        sentence = f"Call https://u{size}:p{key}@{host}.example/v{size % 97}?key={key} then reply. "
        out.append(sentence)
        size += len(sentence)
    return "".join(out)
"""

# The budget for each 5 MiB shape below, best of 3 (design "Speed", and TH-9553's four shapes).
# Set by the Coordinator for 3.12.3 under load (change review r1, cr1 M2).
_S5_BUDGET_S = 1.5
_MIB = 1024 * 1024
_SHAPES = {
    "prose with no key": 'rep("The quick brown fox jumps over the lazy dog. ")',
    "prose with names and no key": 'rep("Each token costs; the secret is batching, the password policy is long. ")',
    "TH-9491 address-dense": "dense()",
    "eyJ repeated": 'rep("eyJ")',
    "digits then :AA": 'rep("1234567:AA")',
    "an unterminated key block": '"-----BEGIN RSA PRIVATE KEY-----\\n" + rep("A" * 64 + "\\n")',
    "-----BEGIN headers with no END": 'rep("-----BEGIN RSA PRIVATE KEY-----")',
    "overlapping named values": 'rep("password=")',
    "TH-9553 URLs with a key in each host": 'rep("https://" + GH + ".example.com/x ")',
    "TH-9553 a one-URL value with a name on every line": '"https://h.example/x" + rep("\\npassword=Zq8w1")',
    "TH-9553 token: abc repeated": 'rep("token: abc ")',
}


# cr1 H1 (change review r1): a name in a URL's host, repeated with no whitespace, and one such
# span before a long run of scheme characters. Each took over 10 s at 1 MiB or less when
# _url_around searched forward from every span. 1 MiB, best of 3, under 1 s: pinned for
# linearity, not timed at 5 MiB (the Coordinator).
_H1_BUDGET_S = 1.0
_H1_SHAPES = {
    "a name in a host, repeated": 'rep("a://password=Zq8w1,b/")',
    "one host span, then a run, then /y://z": '"a://password=Zq8w1,b/" + "x" * (M - 27) + "/y://z"',
}


def _best_of_three(shape: str, expression: str, size: int) -> float:
    code = (
        _SETUP
        + f"M = {size}\n"
        + f"value = {expression}\n"
        + "best = 9.0\n"
        + "for _ in range(3):\n"
        + "    start = time.perf_counter()\n"
        + "    _redact(value)\n"
        + "    best = min(best, time.perf_counter() - start)\n"
        + "print(best)\n"
    )
    try:
        done = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"{shape}: three cleans took more than 60 s")
    return float(done.stdout.strip())


@pytest.mark.parametrize("shape", list(_SHAPES))
def test_five_mib_cleans_within_the_budget(shape: str) -> None:
    best = _best_of_three(shape, _SHAPES[shape], 5 * _MIB)
    assert best < _S5_BUDGET_S, f"{shape}: {best:.3f} s"


@pytest.mark.parametrize("shape", list(_H1_SHAPES))
def test_a_name_in_a_url_host_cleans_in_linear_time(shape: str) -> None:
    best = _best_of_three(shape, _H1_SHAPES[shape], _MIB)
    assert best < _H1_BUDGET_S, f"{shape}: {best:.3f} s"


# Rule 1b's shapes and rule 1's, 5 MiB each (design addendum v2.5.2, "Speed"; the Coordinator's
# call): the cleaner with 1b against the same build without it, `secret_shapes.rule1b` swapped
# out in the same process, best of 5 each, interleaved. With 1b, at most 2 times without it,
# and under 5 s. A shape with no @ runs no 1b match, so it can't fail on the ratio: the 5 s
# ceiling is what pins it, as the Coordinator's call has it (cr2 L6).
_RULE1B_RATIO = 2.0
_RULE1B_CEILING_S = 5.0
_RULE1_SHAPES = {
    "rule 1's scheme://u: with no @": 'rep("https://u:")',
    # One @ at the very end: rule 1 must find it once, not again from every scheme://.
    "rule 1's scheme://u: then one @": 'rep("https://u:")[:-1] + "@"',
    "TH-9553 https://u:p repeated": 'rep("https://u:p ")',
    "rule 1's u:p@h/x@, no host": 'rep("https://u:p@h/x@ ")',
    "rule 1's u:p@h/x@h.io": 'rep("https://u:p@h/x@h.io ")',
    # Owl's reviewer S's and Hawk's reviewer T's shapes for 1b.
    "1b a://u:": 'rep("a://u:")',
    "1b a://u:p@h/x@": 'rep("a://u:p@h/x@")',
    "1b a://:@/@": 'rep("a://:@/@")',
    "1b a://u@": 'rep("a://u@")',
    "1b a:// then x: 40 times": 'rep("a://" + "x:" * 40)',
    "1b a:// then x@ 40 times": 'rep("a://" + "x@" * 40)',
    "1b each part at its bound, no host": 'rep("a://" + "u" * 30 + ":" + "p" * 29 + "@"'
    ' + "q" * 60 + "/" + "s" * 60 + "@ ")',
    "1b port guard, :1 95 times": 'rep("a://u@" + ":1" * 95 + "/")',
    "1b port guard, :{ 95 times": 'rep("a://u@" + ":{" * 95 + "/")',
    "1b port guard, u@h:80/x@h.io": 'rep("a://u@h:80/x@h.io ")',
    "1b a label run after the path's @": 'rep("a://u:p@h/x@" + "a" * 60 + "!")',
    "1b a label host, one URL": 'rep("a://u:p@h/x@db/")',
    "1b a dotted run after the path's @": 'rep("a://u:p@h/x@" + "a." * 30 + "!")',
    "1b a hit, one URL": 'rep("a://u:p@h/x@h.io")',
    "1b a hit and rule 1 together": 'rep("a://u:x://v:p@h/x@h.io ")',
}


def _with_and_without_1b(shape: str, expression: str) -> tuple[float, float]:
    """The best of 5 without rule 1b and with it, interleaved in one process."""
    code = (
        _SETUP
        + f"M = {5 * _MIB}\n"
        + f"value = {expression}\n"
        + "from fi.alk.providers import secret_shapes\n"
        + "with_1b = secret_shapes.rule1b\n"
        + "best = {False: 99.0, True: 99.0}\n"
        + "for _ in range(5):\n"
        + "    for on in (False, True):\n"
        + "        secret_shapes.rule1b = with_1b if on else (lambda text: [])\n"
        + "        start = time.perf_counter()\n"
        + "        _redact(value)\n"
        + "        best[on] = min(best[on], time.perf_counter() - start)\n"
        + "print(best[False], best[True])\n"
    )
    try:
        done = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=150,
            check=True,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"{shape}: ten cleans took more than 150 s")
    without, with_1b = map(float, done.stdout.split())
    return without, with_1b


@pytest.mark.parametrize("shape", list(_RULE1_SHAPES))
def test_rule_1b_costs_at_most_twice_the_cleaner_without_it(shape: str) -> None:
    without, with_1b = _with_and_without_1b(shape, _RULE1_SHAPES[shape])
    assert with_1b <= _RULE1B_RATIO * without, (
        f"{shape}: {with_1b:.3f} s, {without:.3f} s without"
    )
    assert with_1b < _RULE1B_CEILING_S, f"{shape}: {with_1b:.3f} s"


# Rule 1's three S5 shapes, 5 MiB against 1 MiB (the Coordinator's call, cr3): a slowdown that
# grows faster than the text fails here, at 5 times the size, without waiting for a timeout.
_RULE1_GROWTH = 6.0
_RULE1_LINEAR_SHAPES = (
    "rule 1's scheme://u: with no @",
    "rule 1's scheme://u: then one @",
    "TH-9553 https://u:p repeated",
)


@pytest.mark.parametrize("shape", _RULE1_LINEAR_SHAPES)
def test_rule_1s_shapes_grow_with_the_text(shape: str) -> None:
    """Best of 5 at each size, interleaved in one process: 5 MiB at most 6 times 1 MiB."""
    code = (
        _SETUP
        + "sizes = (1024 * 1024, 5 * 1024 * 1024)\n"
        + "values = {}\n"
        + "for M in sizes:\n"
        + f"    values[M] = {_RULE1_SHAPES[shape]}\n"
        + "best = dict.fromkeys(sizes, 99.0)\n"
        + "for _ in range(5):\n"
        + "    for size in sizes:\n"
        + "        start = time.perf_counter()\n"
        + "        _redact(values[size])\n"
        + "        best[size] = min(best[size], time.perf_counter() - start)\n"
        + "print(best[sizes[0]], best[sizes[1]])\n"
    )
    try:
        done = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=150,
            check=True,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"{shape}: ten cleans took more than 150 s")
    one, five = map(float, done.stdout.split())
    assert five <= _RULE1_GROWTH * one, (
        f"{shape}: {five:.3f} s at 5 MiB, {one:.3f} s at 1"
    )


class _Spy:
    """Stands in for a compiled pattern and counts every call made on it."""

    calls = 0

    def __init__(self, pattern: re.Pattern[str]) -> None:
        self._pattern = pattern

    def __getattr__(self, name: str):
        method = getattr(self._pattern, name)
        if not callable(method):
            return method

        def counted(*args, **kwargs):
            _Spy.calls += 1
            return method(*args, **kwargs)

        return counted


def _spied(value):
    if isinstance(value, re.Pattern):
        return _Spy(value)
    if isinstance(value, tuple):
        return tuple(_spied(item) for item in value)
    if isinstance(value, list):
        return [_spied(item) for item in value]
    if isinstance(value, dict):
        return {key: _spied(item) for key, item in value.items()}
    return value


def test_text_with_no_literal_and_no_name_calls_no_pattern(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S5b (r1 M5): the str.find pre-check, not the regex engine's own literal search, is what
    passes such text over."""
    for name, value in list(vars(secret_shapes).items()):
        spied = _spied(value)
        if spied is not value and not callable(value):
            monkeypatch.setattr(secret_shapes, name, spied)
    _Spy.calls = 0

    assert (
        secret_shapes.find("The quick brown fox jumps over the lazy dog. " * 2000) == []
    )
    assert _Spy.calls == 0
    # The spies do see a call when there is something to look at.
    assert secret_shapes.find("password=" + _PW)
    assert _Spy.calls > 0
