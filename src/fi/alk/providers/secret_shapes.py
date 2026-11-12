"""Secrets found by their shape, for the kit's one cleaner (TH-9500, design page v2.4).

`providers.agent._redact_text` cleans a string in three steps: the two leftover rules here, on
the raw value; TH-9491's URL step; then the key patterns here (`find`), on that step's output.
Hosted chat sends its reply in pieces through the same cleaner, and `ChatHold` says where a
piece may end, so no rule here is split between two pieces.

Nothing here does I/O, and nothing is imported but `re` and `bisect`. The kit's own pattern list
(`fi/evals/guardrails/scanners/secrets.py`) costs about 0.25 s to import and belongs to the evals
package, so only the drift test reads it, to tie the prefix patterns here to it.
"""

import re
from bisect import bisect_left

REDACTED = "[redacted]"

# A key's own characters. A literal that follows one of them is inside a longer run, so no
# pattern starts there: each run is tried once, which keeps every scan linear.
_KEY = r"A-Za-z0-9_\-"
# The 600-character cut (`session._shown`) ends a text with this.
_AT_CUT = r"(?=\.\.\.\Z)"
_CUT_LENGTH = 600


def _start(literal: str) -> str:
    escaped = re.escape(literal)
    # The literal comes first, so the regex engine's literal search finds each start.
    return f"{escaped}(?<![{_KEY}]{escaped})"


# (kind, literal, the rest of the key, the characters a key cut short at 600 is made of).
# The guardrails entries with confidence 0.9 or more whose regex opens with a literal of 3 or
# more characters, then the shapes the design page adds. A length is a minimum unless the issuer
# documents a fixed one, so a longer modern key is masked whole.
_PREFIX_TABLE = (
    ("openai", "sk-", r"[a-zA-Z0-9]{20,}T3BlbkFJ[a-zA-Z0-9]{20,}", "A-Za-z0-9"),
    ("openai", "sk-", r"(?:proj-)?[a-zA-Z0-9\-_]{40,}", _KEY),
    ("aws", "AKIA", r"[0-9A-Z]{16}(?![0-9A-Z])", "0-9A-Z"),
    ("google", "AIza", r"[0-9A-Za-z\-_]{35,}", _KEY),
    ("github", "ghp_", r"[0-9a-zA-Z]{36,}", "0-9a-zA-Z"),
    ("github", "gho_", r"[0-9a-zA-Z]{36,}", "0-9a-zA-Z"),
    ("github", "ghu_", r"[0-9a-zA-Z]{36,}", "0-9a-zA-Z"),
    ("github", "ghs_", r"[0-9a-zA-Z]{36,}", "0-9a-zA-Z"),
    ("github", "ghr_", r"[0-9a-zA-Z]{36,}", "0-9a-zA-Z"),
    ("github", "github_pat_", r"[0-9a-zA-Z]{22,}_[0-9a-zA-Z]{59,}", "0-9a-zA-Z_"),
    ("gitlab", "glpat-", r"[0-9a-zA-Z\-_]{20,}", _KEY),
    (
        "slack",
        "xox",
        r"[baprs]-[0-9]{10,13}-[0-9]{10,13}-[a-zA-Z0-9]{24,}",
        r"0-9A-Za-z\-",
    ),
    ("slack", "xoxb-", r"[0-9]{11}-[0-9]{11}-[a-zA-Z0-9]{24,}", r"0-9A-Za-z\-"),
    ("stripe", "sk_live_", r"[0-9a-zA-Z]{24,}", "0-9a-zA-Z"),
    (
        "sendgrid",
        "SG.",
        r"[a-zA-Z0-9\-_]{22}\.[a-zA-Z0-9\-_]{43}(?![a-zA-Z0-9\-_])",
        _KEY + ".",
    ),
    ("anthropic", "sk-ant-", r"[a-zA-Z0-9\-_]{40,}", _KEY),
    ("huggingface", "hf_", r"[a-zA-Z0-9]{34,}", "a-zA-Z0-9"),
    ("npm", "npm_", r"[A-Za-z0-9]{36,}", "A-Za-z0-9"),
    ("pypi", "pypi-AgEIcHlwaS5vcmc", r"[A-Za-z0-9\-_]{50,}", _KEY),
    ("stripe", "rk_live_", r"[0-9a-zA-Z]{24,}", "0-9a-zA-Z"),
    ("slack", "xoxp-", r"[0-9A-Za-z\-]{20,}", r"0-9A-Za-z\-"),
    ("slack", "xoxe-", r"[0-9A-Za-z\-]{20,}", r"0-9A-Za-z\-"),
    ("slack", "xapp-", r"[0-9A-Za-z\-]{20,}", r"0-9A-Za-z\-"),
)
_PREFIXES = tuple(
    (
        kind,
        literal,
        re.compile(_start(literal) + body),
        re.compile(_start(literal) + f"[{cut}]{{8,}}" + _AT_CUT),
    )
    for kind, literal, body, cut in _PREFIX_TABLE
)
# A bare Telegram bot token: 6 to 12 digits, ":AA" and 33 key characters. The search starts at
# ":AA", the literal, and the digits before it are checked on their own.
_TELEGRAM = re.compile(r":AA[0-9A-Za-z\-_]{33,}")
_TELEGRAM_CUT = re.compile(r":AA[0-9A-Za-z\-_]{8,}" + _AT_CUT)
_BOT_ID = re.compile(r"(?:\A|[^0-9])([0-9]{6,12})\Z")
_JWT = re.compile(
    _start("eyJ") + r"[A-Za-z0-9_\-]*\.eyJ[A-Za-z0-9_\-]*\.[A-Za-z0-9_\-]*"
)
# Tokens in a URL's path: the secret segments go and the host stays.
_PATHS = (
    (
        "slack_hook",
        "hooks.slack.com/services/",
        re.compile(
            r"hooks\.slack\.com/services/(T[A-Za-z0-9]+/B[A-Za-z0-9]+/[A-Za-z0-9]+)"
        ),
        re.compile(r"(?<=hooks\.slack\.com/services/)[A-Za-z0-9/]{8,}" + _AT_CUT),
    ),
    (
        "telegram_path",
        "api.telegram.org/bot",
        re.compile(r"api\.telegram\.org/bot([0-9]+:[A-Za-z0-9_\-]+)"),
        re.compile(r"(?<=api\.telegram\.org/bot)[0-9A-Za-z:_\-]{8,}" + _AT_CUT),
    ),
    (
        "discord_hook",
        "discord.com/api/webhooks/",
        re.compile(r"discord\.com/api/webhooks/([0-9]+/[A-Za-z0-9_\-]+)"),
        re.compile(r"(?<=discord\.com/api/webhooks/)[0-9A-Za-z/_\-]{8,}" + _AT_CUT),
    ),
    (
        "discord_hook",
        "discordapp.com/api/webhooks/",
        re.compile(r"discordapp\.com/api/webhooks/([0-9]+/[A-Za-z0-9_\-]+)"),
        re.compile(r"(?<=discordapp\.com/api/webhooks/)[0-9A-Za-z/_\-]{8,}" + _AT_CUT),
    ),
)
# aws_secret_key and azure_storage_key, built from their own guardrails regexes (r4 M3): the
# named-value rule misses AZURE_KEY= and awssecretkey=. They run on the lower-cased text, as the
# guardrails' (?i) would, and mask the value; the issuer documents both lengths.
_GUARD_NAMES = (
    r"aws[_\-]?secret[_\-]?(?:access[_\-]?)?key",
    r"azure[_\-]?(?:storage[_\-]?)?(?:account[_\-]?)?key",
)
_GUARDS = (
    (
        "aws_secret_key",
        "aws",
        re.compile(_GUARD_NAMES[0] + r"[\s:=]+['\"]?([a-z0-9/+=]{40})(?![a-z0-9/+=])"),
    ),
    (
        "azure_storage_key",
        "azure",
        re.compile(_GUARD_NAMES[1] + r"[\s:=]+['\"]?([a-z0-9+/=]{88})(?![a-z0-9+/=])"),
    ),
)

# A private-key block runs from its header to the END that pairs with it: the nearest header
# before that END (r4 L4). Whatever lies between is masked (r3 H1).
_PEM = re.compile(r"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----")
_PEM_END = re.compile(r"-----END [A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----")
# With no END, the block runs through the key lines after its header (r3 M5): base64 and "=",
# after an indent, or after a line number and a tab (the Read tool's form).
_KEY_LINE = re.compile(r"[ \t]*(?:\d+\t)?[A-Za-z0-9+/=]+")

# Named values. A name is found in the lower-cased text, which also finds DB_PASSWORD; how it
# sits in its identifier is then checked on the text as written. The kinds:
#   base: the whole identifier, its end after "_" or "-", or a camelCase ending (clientSecret)
#   flat: apikey, as base
#   compact: two words with no join, only as a camelCase ending or the whole identifier
#            (SecretKey, AccountKey); the second word's capital is at the index given
#   upper: "pass" only ending an upper-case identifier, after "_" or "-" (DB_PASS, r4 L2)
#   exact: the whole identifier, or its end after "_" or "-"
_FORMS = {
    "password": ("base", 0),
    "passwd": ("base", 0),
    "secret": ("base", 0),
    "token": ("base", 0),
    "api_key": ("base", 0),
    "api-key": ("base", 0),
    "secret_key": ("base", 0),
    "private_key": ("base", 0),
    "access_key": ("base", 0),
    "signing_key": ("base", 0),
    "storage_key": ("base", 0),
    "account_key": ("base", 0),
    "aws_secret_access_key": ("base", 0),
    # Multi-word names joined by "-" (r4 H1): Spring's secret-key, kubectl's --from-literal.
    "secret-key": ("base", 0),
    "private-key": ("base", 0),
    "access-key": ("base", 0),
    "signing-key": ("base", 0),
    "storage-key": ("base", 0),
    "account-key": ("base", 0),
    "aws-secret-access-key": ("base", 0),
    "apikey": ("flat", 0),
    "secretkey": ("compact", 6),
    "privatekey": ("compact", 7),
    "accesskey": ("compact", 6),
    "signingkey": ("compact", 7),
    "storagekey": ("compact", 7),
    "accountkey": ("compact", 7),
    "pass": ("upper", 0),
    "pgpassword": ("exact", 0),
    "mysql_pwd": ("exact", 0),
}
# Pagination and idempotency names (r4 L2): honest values, never secrets.
_PAGINATION = frozenset(
    {
        "pageToken",
        "nextPageToken",
        "NextToken",
        "nextToken",
        "continuationToken",
        "NextContinuationToken",
        "ClientToken",
    }
)
_LONGEST_NAME = max(map(len, _FORMS))
# Longest first, so secret_key is tried before secret at the same start.
_NAME_ALTERNATION = "|".join(map(re.escape, sorted(_FORMS, key=len, reverse=True)))
# Around the "=" or ":": up to 16 spaces or tabs of alignment, or up to 4 whitespace characters
# with a line break. The break branch is tried first, and only when text follows within 4
# characters: tried second, the alignment branch would match empty before the break (r1 M2).
_AROUND = r"(?:(?=\s{0,4}\S)\s{0,4}|[ \t]{0,16})"
# A quote may close a quoted name; Markdown emphasis or a backtick may sit around the ":".
_TAIL = (
    r"[\"'`]?(?:[*_`]{0,4}"
    + _AROUND
    + r":(?:[*_]{1,4}|`(?=\s))?|"
    + _AROUND
    + r"=)"
    + _AROUND
)
_PLAIN_VALUE = r"[^\s\"'`,;)\]}]"


def _value_start(letter: str) -> str:
    # A letter and a digit, or 16 or more characters. Read from the value's start and bounded
    # at 16, so a name whose value fails is dropped by the engine, not by a Python check.
    def test(char: str) -> str:
        return rf"(?:(?={char}{{16}})|(?={char}{{0,15}}?{letter})(?={char}{{0,15}}?\d))"

    double, single, back = r'[^"\n]', r"[^'\n]", r"[^`\n]"
    # A value already masked fails at once, so a second pass costs little.
    return (
        r"(?![\"'`]?\[redacted\])"
        r'(?:(?P<dq>")'
        + test(double)
        + r"|(?P<sq>')"
        + test(single)
        + r"|(?P<bq>`)"
        + test(back)
        + "|"
        + test(_PLAIN_VALUE)
        + ")"
    )


def _per_form(start: str) -> dict[str, re.Pattern[str]]:
    # One regex per name, on the lower-cased text, each opening with its name, so the engine's
    # literal search finds it as str.find would: a run of a name's own first letter never
    # starts an attempt. `start` is a lookbehind on the name that says where it may sit.
    tail = r"(?!::)" + _TAIL + _value_start("[a-z]")
    return {
        form: re.compile(re.escape(form) + start.format(re.escape(form)) + tail)
        for form in _FORMS
    }


# A name followed by "::" is code, not a name (Token::new(42), r1 L2).
# Not right after a letter or digit: the whole identifier, or its end after "_" or "-". "pass"
# only ever follows "_" or "-".
_NAMED = _per_form(r"(?<![^\W_]{})")
_NAMED["pass"] = _per_form(r"(?<=[_\-]{})")["pass"]
# Right after a letter or digit: a camelCase or PascalCase ending (clientSecret) when its first
# letter is a capital as written, which is checked on the text itself.
_NAMED_CAMEL = {
    form: pattern
    for form, pattern in _per_form(r"(?<=[^\W_]{})").items()
    if _FORMS[form][0] in ("base", "flat", "compact")
}
# At the start of a line or after a tab, for a value TH-9491 joins into one URL.
_NAMED_AT_LINE = _per_form(r"(?<=[\n\t]{})")
_QUOTE_END = {quote: re.compile(f"[{quote}\\n]") for quote in "\"'`"}
_PLAIN_END = re.compile(r"[\s\"'`,;)\]}]")
# A value is a reference, never masked, when it starts with one of these or holds one of those.
_REFERENCE_STARTS = ("$", "<", "{{")
# Unquoted code: an identifier or dotted name called, indexed or followed by "::", or a dotted
# name whose first part is lower-case letters only (settings.v2.key; r3 L6, r4 M5).
_CODE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*(?:[(\[]|::)"
    r"|[a-z]+(?:\.[A-Za-z][A-Za-z0-9_]*)+\Z"
)

_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_KEY_LITERALS = tuple(dict.fromkeys(literal for _, literal, _, _ in _PREFIXES))
# What a key may open with, the bare Telegram token and a JWT included. When a string holds none,
# step 2 checks no URL's host for a key.
HOST_LITERALS = tuple(sorted({*_KEY_LITERALS, ":AA", "eyJ"}, key=len, reverse=True))
_HOST_LITERAL = re.compile("|".join(map(re.escape, HOST_LITERALS)))
_PATH_LITERALS = tuple(literal for _, literal, _, _ in _PATHS)
# Short pieces at least one of which every literal holds, so most text is passed over with a
# few searches: each name, guard name included, holds one of the name roots.
_LITERAL_ROOTS = (
    "sk",
    "gh",
    "github_pat_",
    "AKIA",
    "AIza",
    "glpat-",
    "xo",
    "xa",
    "SG.",
    "hf_",
    "npm_",
    "pypi-",
    "rk_",
    ":AA",
    "eyJ",
    "-----BEGIN ",
    "hooks.",
    "api.telegram",
    "discord",
)
_NAME_ROOTS = ("pass", "secret", "token", "key", "pwd")


def may_hold_key(text: str) -> bool:
    """False when `text` holds no key literal at all, so no URL's host needs checking."""
    return any(root in text for root in _LITERAL_ROOTS)


_HEADER = "-----BEGIN "


# ---------------------------------------------------------------------------------- step 1
# Rule 1: from scheme://user: to the next @, when the password is broken by whitespace, ?, #
# or / (TH-9491's URL step stops a URL at each). Its userinfo is removed when the text after
# "user:" doesn't begin with a port, holds at most one run of whitespace with text on both
# sides, and is at most 64 characters on each side of that run.
_PORT = (
    r"(?:\d+|\$?\{[^{}\s]*\}|\$[A-Za-z_][A-Za-z0-9_]*|%[A-Za-z_][A-Za-z0-9_]*%|<[^<>\s]*>"
    r"|[A-Z][A-Z0-9_]*|[Pp][Oo][Rr][Tt])(?=[^A-Za-z0-9_\-@]|\Z)"
)
_HALF = r"[^\s@]{1,64}"
# A line break, after up to 4 spaces or tabs, with up to 16 characters of indent; or up to 4
# spaces, tabs or NBSPs (r3 H2, r4 L3).
_RUN = r"(?:[ \t]{0,4}\r?\n[ \t]{0,16}|[ \t\xa0]{1,4})"
_SCHEME = r"(?<![A-Za-z0-9+.\-])[A-Za-z][A-Za-z0-9+.\-]*://"
# One match per start, bounded by the two halves: the next @ is the one the match reaches, and
# no position is searched for again from another start.
_RULE1 = re.compile(
    _SCHEME
    + r"([^\s/?#@:\[\]]*:"
    + rf"(?!{_PORT})"
    # An @ within reach, looked for first, so a start with no @ near it fails after one
    # bounded scan.
    + r"(?=[^@]{0,150}@)"
    + rf"(?:{_HALF}{_RUN}{_HALF}|(?=[^\s@]{{0,63}}[/?#]){_HALF})@)"
)

# Rule 1b (design addendum v2.5.2): a userinfo that holds an @ before the password's /, as in
# postgres://admin:P@ss/Xk9@db.example.com, where rule 1 stops at the first @. The authority runs
# from scheme:// to the first /, and may hold @. Its userinfo is removed, up to the path's @,
# when the first path segment is text, then @, then a host.
# A host after the path's @ (M-B): a dotted name, a name and a port rule 1 reads, localhost, a
# bracketed IPv6 address with or without a zone, or a single label followed by the end or by
# what ends a port.
_RULE1B_HOST = (
    r"(?:[A-Za-z0-9_\-]+(?:\.[A-Za-z0-9_\-]+)+"
    + rf"|[A-Za-z0-9_\-]+:(?:\d|{_PORT})"
    + r"|localhost\b"
    + r"|\[[0-9A-Fa-f:.%A-Za-z]+\]"
    + r"|[A-Za-z0-9_\-]+(?=[/\"'`\s?#),;>\]}|]|\Z))"
)
_RULE1B = re.compile(
    _SCHEME
    # The port test (M1): with no : before the authority's first @, the text after its last :
    # must not read as a port, so admin@localhost:8080/bob@example.com keeps its host.
    + rf"(?![^\s/?#:@]{{0,129}}@[^\s/?#]{{0,194}}:{_PORT}/)"
    + r"("
    # The authority holds a :, and it comes before any [ (M-C): admin@[::1]/bob@x keeps its host.
    + r"(?=[^\s/?#\[]{0,129}:)"
    # At most 64 characters before its first : or @.
    + r"(?=[^\s/?#:]{0,64}[:@])"
    # Up to the authority's last @, then at most 64 to the /, then 1-64 to the path's @.
    + r"[^\s/?#\[\]]{0,129}@[^\s/?#@]{0,64}/[^\s/?#@]{1,64}@"
    + r")"
    + rf"(?={_RULE1B_HOST})"
)

# Rule 2: the piece after an address piece loses its query values when it starts with ?, & or
# #, or whatever it starts with when the address's query ends in & or ?.
# The lookahead finds the piece's :// once (a lookahead never retries), so a long piece with
# many :// is scanned a bounded number of times.
_THEN_PIECE = re.compile(r"(?<!\S)(?=\S*?://)(\S+)(?:(?<=[&?])\s+(?=\S)|\s+(?=[?&#]))")
_PIECE = re.compile(r"\S+")
_PARAM = re.compile(r"(^|[?&#;])([^=&#?\s]*)=([^&#\s]*)")
# HTML 4.01's character entities, and &apos;. An & that starts one is text, not a query
# parameter, so &copy=2024 keeps its value.
_ENTITIES = frozenset(
    """
    AElig Aacute Acirc Agrave Alpha Aring Atilde Auml Beta Ccedil Chi Dagger Delta ETH Eacute
    Ecirc Egrave Epsilon Eta Euml Gamma Iacute Icirc Igrave Iota Iuml Kappa Lambda Mu Ntilde Nu
    OElig Oacute Ocirc Ograve Omega Omicron Oslash Otilde Ouml Phi Pi Prime Psi Rho Scaron Sigma
    THORN Tau Theta Uacute Ucirc Ugrave Upsilon Uuml Xi Yacute Yuml Zeta aacute acirc acute
    aelig agrave alefsym alpha amp and ang apos aring asymp atilde auml bdquo beta brvbar bull
    cap ccedil cedil cent chi circ clubs cong copy crarr cup curren dArr dagger darr deg delta
    diams divide eacute ecirc egrave empty emsp ensp epsilon equiv eta eth euml euro exist fnof
    forall frac12 frac14 frac34 frasl gamma ge gt hArr harr hearts hellip iacute icirc iexcl
    igrave image infin int iota iquest isin iuml kappa lArr lambda lang laquo larr lceil ldquo
    le lfloor lowast loz lrm lsaquo lsquo lt macr mdash micro middot minus mu nabla nbsp ndash
    ne ni not notin nsub ntilde nu oacute ocirc oelig ograve oline omega omicron oplus or ordf
    ordm oslash otilde otimes ouml para part permil perp phi pi piv plusmn pound prime prod prop
    psi quot rArr radic rang raquo rarr rceil rdquo real reg rfloor rho rlm rsaquo rsquo sbquo
    scaron sdot sect shy sigma sigmaf sim spades sub sube sum sup sup1 sup2 sup3 supe szlig tau
    there4 theta thetasym thinsp thorn tilde times trade uArr uacute uarr ucirc ugrave uml upsih
    upsilon uuml weierp xi yacute yen yuml zeta zwj zwnj
    """.split()
)


def rule1(text: str) -> list[tuple[int, int]]:
    """The userinfo rules 1 and 1b remove from `text`: each span runs from the user to its @.

    One sorted list, with spans that overlap or touch merged, since `remove` needs them in order
    and apart (addendum M4): a://u:x://v:p@h/x@host.example is both rules' at once."""
    if "://" not in text or "@" not in text:
        return []
    spans = [match.span(1) for match in _RULE1.finditer(text)] + rule1b(text)
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def rule1b(text: str) -> list[tuple[int, int]]:
    """The userinfo rule 1b removes from `text`, up to the path's @. Its own function, so the
    speed test can time the cleaner without it."""
    return [match.span(1) for match in _RULE1B.finditer(text)]


def rule2(text: str) -> list[tuple[int, int]]:
    """The query values rule 2 empties in `text`: the piece after an address piece."""
    # It needs a ?, & or # somewhere: a query ends in one, or the next piece starts with one.
    if "://" not in text or ("?" not in text and "&" not in text and "#" not in text):
        return []
    spans: list[tuple[int, int]] = []
    for match in _THEN_PIECE.finditer(text):
        address = match.group(1)
        if (
            address[-1] in "&?"
            and "?" not in address
            and text[match.end()] not in "?&#"
        ):
            continue
        piece = _PIECE.match(text, match.end())
        start = piece.start()
        for param in _PARAM.finditer(piece.group()):
            lead, name, value = param.groups()
            # An empty value or the marker never matches, so cleaning twice changes nothing.
            if not value or value == REDACTED:
                continue
            if lead == "&" and name.split(";")[0] in _ENTITIES:
                continue
            spans.append((start + param.start(3), start + param.end(3)))
    return spans


def remove(text: str, spans: list[tuple[int, int]]) -> str:
    """`text` without `spans`, which are in order and don't overlap."""
    out, at = [], 0
    for start, end in spans:
        out.append(text[at:start])
        at = end
    out.append(text[at:])
    return "".join(out)


def leftovers(value: str, hits: list[str] | None = None) -> str:
    """Step 1: rule 1, then rule 2 on what rule 1 leaves."""
    spans = rule1(value)
    if spans:
        value = remove(value, spans)
        if hits is not None:
            hits.append("rule1")
    spans = rule2(value)
    if spans:
        value = remove(value, spans)
        if hits is not None:
            hits.append("rule2")
    return value


# ---------------------------------------------------------------------------------- step 3
def is_cut(value: str) -> bool:
    """Whether `value` is a text cut at 600 characters: both the length and the "..." (r4 L1).
    Tested on the raw value, before step 1, so a URL query that step 2 drops before a key body
    can't move it (r1 M1)."""
    return len(value) == _CUT_LENGTH and value.endswith("...")


def find(text: str, cut: bool = False) -> list[tuple[int, int, str]]:
    """Step 3: what to mask in `text`, as (start, end, kind).

    Keys by prefix or shape, key blocks, tokens in a URL's path and named values. With `cut`,
    the raw value was a 600-character cut, so a key block with no END runs to the end and a key
    cut short at the end is masked to it.

    Literals and names are looked for with `in` first (names in the lower-cased text), so text
    with neither runs no pattern regex.
    """
    low = text.translate(_ASCII_LOWER)
    names = any(root in low for root in _NAME_ROOTS)
    if not names and not any(root in text for root in _LITERAL_ROOTS):
        return []
    literals = [literal for literal in _KEY_LITERALS if literal in text]
    paths = [path for path in _PATHS if path[1] in text]
    header = _HEADER in text
    telegram = ":AA" in text
    jwt = "eyJ" in text
    guards = [guard for guard in _GUARDS if guard[1] in low] if names else []
    spans: list[tuple[int, int, str]] = []
    for kind, literal, pattern, cut_pattern in _PREFIXES:
        if literal in literals:
            spans.extend((m.start(), m.end(), kind) for m in pattern.finditer(text))
            if cut:
                spans.extend(
                    (m.start(), m.end(), "cut_" + kind)
                    for m in cut_pattern.finditer(text)
                )
    for kind, _, pattern, cut_pattern in paths:
        spans.extend((m.start(1), m.end(1), kind) for m in pattern.finditer(text))
        if cut:
            spans.extend(
                (m.start(), m.end(), "cut_" + kind) for m in cut_pattern.finditer(text)
            )
    if telegram:
        _telegram(text, _TELEGRAM, "telegram", spans)
        if cut:
            _telegram(text, _TELEGRAM_CUT, "cut_telegram", spans)
    if jwt:
        spans.extend((m.start(), m.end(), "jwt") for m in _JWT.finditer(text))
    for kind, _, pattern in guards:
        spans.extend((m.start(1), m.end(1), kind) for m in pattern.finditer(low))
    if header:
        spans.extend((start, end, "private_key") for start, end in _blocks(text, cut))
    if names:
        _named(text, low, spans)
    return spans


def line_starts(text: str) -> list[tuple[int, int, str]]:
    """Keys and named values that begin a line or follow a tab in `text`.

    For a value TH-9491 cleans as one URL: it joins the value's lines, which puts each line's
    first word right after the URL's last character, where no pattern starts (r2 M1, r3 M7).
    """
    spans: list[tuple[int, int, str]] = []
    for kind, literal, pattern, _ in _PREFIXES:
        if literal in text:
            spans.extend(
                (m.start(), m.end(), kind)
                for m in pattern.finditer(text)
                if m.start() and text[m.start() - 1] in "\n\r\t"
            )
    if ":AA" in text:
        found: list[tuple[int, int, str]] = []
        _telegram(text, _TELEGRAM, "telegram", found)
        spans.extend(
            span for span in found if span[0] and text[span[0] - 1] in "\n\r\t"
        )
    if "eyJ" in text:
        spans.extend(
            (m.start(), m.end(), "jwt")
            for m in _JWT.finditer(text)
            if m.start() and text[m.start() - 1] in "\n\r\t"
        )
    low = text.translate(_ASCII_LOWER)
    if any(root in low for root in _NAME_ROOTS):
        _named(text, low, spans, at_line=True)
    return spans


def mask(
    text: str,
    spans: list[tuple[int, int, str]],
    hits: list[str] | None = None,
    widen: bool = True,
) -> str:
    """`text` with each span replaced by the marker, overlapping spans as one.

    A span that touches a URL's host widens to that whole URL: step 3 never writes inside a
    host, so a second pass changes nothing (r2 L3)."""
    if not spans:
        return text
    if widen and "://" in text:
        widened = []
        for start, end, kind in spans:
            url = _url_around(text, start, end)
            widened.append((url[0], url[1], kind) if url else (start, end, kind))
        spans = widened
    spans.sort()
    out: list[str] = []
    kinds: set[str] = set()
    at, begin, finish, group = 0, -1, -1, []
    for start, end, kind in spans + [(len(text) + 1, 0, "")]:
        if start < finish:
            # Overlapping spans are masked as one.
            finish = max(finish, end)
            group.append(kind)
            continue
        if begin >= 0:
            if finish - begin != len(REDACTED) or text[begin:finish] != REDACTED:
                kinds.update(group)
            out.append(text[at:begin])
            out.append(REDACTED)
            at = finish
        begin, finish, group = start, end, [kind]
    out.append(text[at:])
    if hits is not None:
        hits.extend(sorted(kinds))
    return "".join(out)


def host_key(url: str) -> str | None:
    """The kind of a key in `url`'s host, or None. Matched on the URL as written, before
    urlsplit lowers the host, so an upper-case key is still found (r3 M7)."""
    found = _NETLOC.search(url)
    if found is None:
        return None
    host = found.group(1).rpartition("@")[2]
    if not _HOST_LITERAL.search(host):
        return None
    for kind, literal, pattern, _ in _PREFIXES:
        if literal in host and pattern.search(host):
            return kind
    if ":AA" in host:
        found_tokens: list[tuple[int, int, str]] = []
        _telegram(host, _TELEGRAM, "telegram", found_tokens)
        if found_tokens:
            return "telegram"
    if "eyJ" in host and _JWT.search(host):
        return "jwt"
    return None


_NETLOC = re.compile(r"://([^/?#]*)")
_HOST_STOP = re.compile(r"[/?#\s]")
_SCHEME_RUN_BEFORE = re.compile(r"[A-Za-z0-9+.\-]*\Z")
# A URL runs to the next whitespace or the next URL's ://, whichever comes first.
_URL_END = re.compile(r"\s|://")
# A host is looked for this far back from a match, so the search is bounded.
_HOST_REACH = 256
# A name in a URL's host followed by a port, as in http://auth-token:8080/v1 (cr1 M1).
_HOST_PORT = re.compile(r":\d+(?=[/?#\s]|\Z)")


def _host_start(text: str, start: int) -> int:
    """Where the host that `start` sits in begins, after its ://, or -1 when `start` is in no
    URL's host. Looked for at most _HOST_REACH characters back."""
    scheme_end = text.rfind("://", max(0, start - _HOST_REACH), start)
    if scheme_end < 0 or _HOST_STOP.search(text, scheme_end + 3, start):
        return -1
    return scheme_end + 3


def _url_around(text: str, start: int, end: int) -> tuple[int, int] | None:
    """The span of the URL whose host [start, end) touches, or None."""
    host = _host_start(text, start)
    if host < 0:
        return None
    scheme_end = host - 3
    stop = _HOST_STOP.search(text, start)
    host_end = stop.start() if stop else len(text)
    at = text.rfind("@", host, host_end)
    if at >= 0:
        host = at + 1
    if end <= host or start >= host_end:
        return None
    scheme = _SCHEME_RUN_BEFORE.search(text, max(0, scheme_end - 64), scheme_end)
    begin = scheme.start() if scheme else scheme_end
    # One search forward for the URL's end, and the next scheme's run looked for at most 64
    # back from its ://, as above: searched from every span instead, both were quadratic in a
    # long run with no whitespace (cr1 H1).
    stop = _URL_END.search(text, host_end)
    finish = stop.start() if stop else len(text)
    if stop and stop.group() == "://":
        scheme = _SCHEME_RUN_BEFORE.search(text, max(host_end, finish - 64), finish)
        finish = scheme.start() if scheme else finish
    return begin, finish


def _telegram(
    text: str, pattern: re.Pattern[str], kind: str, spans: list[tuple[int, int, str]]
) -> None:
    for match in pattern.finditer(text):
        digits = _BOT_ID.search(text[max(0, match.start() - 13) : match.start()])
        if digits:
            spans.append((match.start() - len(digits.group(1)), match.end(), kind))


def _blocks(text: str, cut: bool) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    headers = list(_PEM.finditer(text))
    end_at, end = -2, None
    done = 0
    for index, header in enumerate(headers):
        if header.start() < done:
            continue
        # The next END is found once and reused for every header before it (r3 M6).
        if end_at != -1 and end_at < header.end():
            end = _PEM_END.search(text, header.end())
            end_at = end.start() if end else -1
        following = (
            headers[index + 1].start() if index + 1 < len(headers) else len(text) + 1
        )
        if end is not None and header.end() <= end_at < following:
            stop = end.end()
        elif cut:
            # Cut at 600 with no END: the block runs to the end of the text (r4 M6).
            stop = len(text)
        else:
            stop = _key_lines_end(text, header.end())
        spans.append((header.start(), stop))
        done = stop
    return spans


def _key_lines_end(text: str, start: int) -> int:
    """Where a block with no END stops: after the key lines that follow its header, at the
    first line that isn't one (r3 M5)."""
    stop = start
    rest = text[start : start + 1]
    line = start if rest in ("", "\n", "\r") else _key_line_end(text, start)
    if line < 0:
        return stop
    stop = line
    newline = text.find("\n", line)
    while newline >= 0:
        line = _key_line_end(text, newline + 1)
        if line < 0:
            break
        stop = line
        newline = text.find("\n", line) if line < len(text) else -1
        if newline > line + 1:
            # Something other than a \r came before the line break.
            break
    return stop


def _key_line_end(text: str, start: int) -> int:
    """The end of the key line at `start` (without its \\r), or -1 if it isn't one. The last
    line may end in the cut's "..."."""
    line = _KEY_LINE.match(text, start)
    if line is None or line.end() == start:
        return -1
    end = line.end()
    if end + 3 == len(text) and text.startswith("...", end):
        return len(text)
    carriage = text[end : end + 1] == "\r"
    if end + carriage == len(text) or text[end + carriage : end + carriage + 1] == "\n":
        return end
    return -1


def _named(
    text: str, low: str, spans: list[tuple[int, int, str]], at_line: bool = False
) -> None:
    if at_line:
        # A value never runs past its line, so names at line starts never overlap.
        for form in _FORMS:
            if form in low:
                for match in _NAMED_AT_LINE[form].finditer(low):
                    _take(text, match, spans)
        return
    found: list[tuple[int, str, re.Match[str]]] = []
    runs = 0
    for form in _FORMS:
        if form not in low:
            continue
        before = len(found)
        found.extend((m.start(), form, m) for m in _NAMED[form].finditer(low))
        if form in _NAMED_CAMEL:
            found.extend(
                (m.start(), form, m)
                for m in _NAMED_CAMEL[form].finditer(low)
                if text[m.start()].isupper()
            )
        runs += len(found) > before
    if runs > 1:
        found.sort(key=_first)
    at = 0
    for start, form, match in found:
        # A name inside a matched value is not a start (r4 M7): overlapping values stay linear.
        if (
            start >= at
            and _name_sits(text, start, form)
            and not _host_name(text, start, form)
        ):
            at = _take(text, match, spans)


def _host_name(text: str, start: int, form: str) -> bool:
    """Whether the name at `start` ends a URL's host name followed by its port, as a Compose or
    Kubernetes service name does (http://vault-secret:8200/v1/kv, cr1 M1): its ":" opens no
    value. The port is checked first, so most names never look back for a host."""
    return (
        _HOST_PORT.match(text, start + len(form)) is not None
        and _host_start(text, start) >= 0
    )


def _take(text: str, match: re.Match[str], spans: list[tuple[int, int, str]]) -> int:
    """Masks the value a name's match opens, unless it is a reference. Returns its end."""
    begin = match.end()
    # The value's opening quote is the last group matched, if it has one.
    quote = text[begin - 1] if match.lastgroup else ""
    stop = (_QUOTE_END[quote] if quote else _PLAIN_END).search(text, begin)
    finish = stop.start() if stop else len(text)
    if _secret_value(text[begin:finish], bool(quote)):
        spans.append((begin, finish, "named"))
    return finish


def _first(found: tuple[int, str, re.Match[str]]) -> int:
    return found[0]


def _name_sits(text: str, start: int, form: str) -> bool:
    """Whether the name at `start` sits in its identifier as its kind allows."""
    kind, capital = _FORMS[form]
    before = text[start - 1] if start else ""
    joined = before == "_" or before == "-"
    whole = not before or not (before.isalnum() or joined)
    if kind == "upper":
        return joined and text.startswith("PASS", start) and _upper_before(text, start)
    if kind == "exact":
        return whole or joined
    camel = (
        text[start].isupper()
        and before.isalnum()
        and (not before.isupper() or text[start + 1 : start + 2].islower())
    )
    if kind == "compact":
        return text[start + capital].isupper() and (whole or camel)
    if form == "token" and _identifier(text, start, start + 5) in _PAGINATION:
        return False
    return whole or joined or camel


def _identifier(text: str, start: int, end: int) -> str:
    # Bounded: no pagination name is longer than 21 characters.
    begin = start
    while (
        begin > 0
        and start - begin < 17
        and (text[begin - 1].isalnum() or text[begin - 1] == "_")
    ):
        begin -= 1
    return text[begin:end]


def _upper_before(text: str, start: int) -> bool:
    # The identifier before PASS holds no lower-case letter, looked for at most 64 back.
    begin = start
    while (
        begin > 0
        and start - begin < 64
        and (text[begin - 1].isalnum() or text[begin - 1] == "_")
    ):
        begin -= 1
        if text[begin].islower():
            return False
    return True


def _secret_value(value: str, quoted: bool) -> bool:
    """Whether a value that passed the length test is masked: never when it is a reference."""
    if value.isalnum():
        # Letters and digits alone are neither a reference nor code.
        return True
    if (
        value.startswith(_REFERENCE_STARTS)
        or "os.environ" in value
        or "process.env" in value
        or "env(" in value
    ):
        return False
    return quoted or _CODE.match(value) is None


# ---------------------------------------------------------------------------------- chat
# A key is held whole up to this many characters, the cleaner's _HOLD_HARD; past it the
# cleaner's own long-URL rule applies. A key block has no such bound (r4 L7).
_LONGEST_HELD_KEY = 16_384
_SPACE_RUN = re.compile(r"\s+")
_SEAM_NAME = re.compile(_NAME_ALTERNATION)
_SEAM_GUARD = re.compile("|".join(_GUARD_NAMES))
_TAIL_PATTERN = re.compile(_TAIL)
# Every beginning of a tail: while what follows a name is one, its match is still open.
_TAIL_OPEN = re.compile(
    r"[\"'`]?[*_`]{0,4}(?:[ \t]{0,16}|\s{0,4})(?:[=:][*_`]{0,4}(?:[ \t]{0,16}|\s{0,4}))?"
)
_GUARD_TAIL = re.compile(r"[\s:=]+['\"]?")
_GUARD_TAIL_OPEN = re.compile(r"[\s:=]*")
_GUARD_VALUE_END = re.compile(r"[^A-Za-z0-9/+=]")
_PLAIN_START = re.compile(_PLAIN_VALUE)
_USER_END = re.compile(r"[\s/?#@:\[\]]")
# Every beginning of rule 1's text after "user:", while no @ has come: a CRLF's \r alone too,
# when its \n is still to come in the next delta (cr2 M3).
_RULE1_OPEN = re.compile(rf"[^\s@]{{0,64}}(?:{_RUN}[^\s@]{{0,64}}|[ \t]{{0,4}}\r)?")
_HEADER_OPEN = re.compile(r"-----BEGIN[A-Z0-9 ]{0,60}-{0,5}")
_SEAM_KEY = _HOST_LITERAL
_URL_MARK = re.compile("://")
_LONGEST_END = len("-----END " + "X" * 40 + "PRIVATE KEY BLOCK-----")
# How far back a delta's search reaches into the text before it: the longest name, guard name
# or literal, less one, so one split across two deltas is still found.
_REACH = (
    max(_LONGEST_NAME, len("azure_storage_account_key"), *map(len, _KEY_LITERALS)) - 1
)

# A named value's phases in ChatHold.
_FRAME, _QUOTED, _PLAIN = range(3)


def _piece_start(piece: "_Piece") -> int:
    return piece.start


class _Piece:
    """A whitespace-separated piece of the stream."""

    __slots__ = ("start", "end", "address", "after", "extends", "query_end")

    def __init__(self, start: int, before: "_Piece | None") -> None:
        self.start = start
        self.end = -1
        self.address = False
        # The address piece this one follows, if any: a candidate here holds from there.
        self.after = before if before is not None and before.address else None
        # Whether rule 2 reaches into this piece from the address piece before it.
        self.extends = False
        # For an address piece: its query ends in & or ?, so rule 2 reaches the next piece.
        self.query_end = False


class ChatHold:
    """Where hosted chat may cut its streamed reply, so no rule here is split between two
    pieces (design page, "The hold rule" and "The seam").

    `_DeltaCleaner` holds the text after its last cut and cleans each piece it lets out. Rule 1,
    rule 2, a named value and a key block match across whitespace, so the cleaner asks here how
    far it may go: never past its own cut, never into a match that is still open or has just
    closed, and never into an address piece holding a candidate, or the piece after one.

    The work per delta is bounded by the delta's length plus a constant (r4 H2, r1 M7): the
    state is kept between deltas, and only the new text and a short tail are searched. A
    candidate's match is decided once, when what closes it arrives. Positions count from the
    start of the prose stream; the held text is always the end of it.
    """

    def __init__(self) -> None:
        # Set by the cleaner, whose `_learn` alone decides it. Before prose the cleaner never
        # cuts, since a lone URL broken by a line break is one URL (r4 M2), so neither does this.
        self.prose = False
        self._end = 0
        # Just after the last whitespace: the cleaner's own cut.
        self._space_end = 0
        self._pieces: list[_Piece] = []
        # Open candidates, by where each starts: named values, guard names, rule 1's
        # scheme://user: and key headers still arriving.
        self._names: dict[int, list] = {}
        self._guards: dict[int, list] = {}
        self._users: dict[int, list] = {}
        self._headers: dict[int, int] = {}
        # Named values and guard names that have closed, so the tail search doesn't take them
        # again.
        self._done: set[int] = set()
        # Matches that have closed, as (start, end, hold start): no cut falls inside one, so a
        # match that has just closed goes out whole (r3 M3).
        self._closed: list[tuple[int, int, int]] = []
        # An open key block: [hold start, header start, where END is next looked for].
        self._block: list[int] | None = None
        # A block no END can close, since a later header came first: it holds to the end.
        self._forever: int | None = None
        # Where a key in the last, unfinished piece holds from.
        self._key: int | None = None
        # The last "://" and "-----BEGIN" taken, so each is taken once.
        self._url_seen = -1
        self._header_seen = -1

    def cut(self, held: str, delta: str) -> int:
        """The index into `held + delta` up to which the text may be sent."""
        if not self.prose:
            return 0
        base = self._end - len(held)
        old_end = self._end
        self._end += len(delta)
        whole = held + delta
        self._forget(base)
        if self._forever is not None:
            return 0
        self._split(whole, base, old_end)
        start = max(base, old_end - _REACH)
        while True:
            if self._block is not None:
                closed = self._block_end(whole, base)
                if closed < 0:
                    break
                start = max(start, closed)
            if not self._look(whole, base, start):
                break
        if self._forever is not None:
            return 0
        self._follow(whole, base)
        return self._limit(base) - base

    def shed_cut(self, held: str) -> int | None:
        """Where the cleaner's flush past its soft bound may cut `held`.

        None when nothing here holds the text, so the cleaner sheds as before; 0 when nothing may
        be sent yet. A key block is held whatever its length; a key, a named value and a match
        that has closed, up to the hard bound."""
        if not self.prose:
            return None
        base = self._end - len(held)
        holds = list(self._headers.values())
        if self._forever is not None:
            holds.append(self._forever)
        if self._block is not None:
            holds.append(self._block[0])
        if not holds and self._key is not None and len(held) <= _LONGEST_HELD_KEY:
            holds.append(self._key)
        if len(held) <= _LONGEST_HELD_KEY:
            # A named value longer than the soft bound is held as a key is (cr1 M3): shed past
            # it, the cleaned text before the cut would mask the value only that far, and the
            # rest would go out later with no name in front of it. A match that has closed is
            # held too: one still held waits on the piece after its URL, and the branches would
            # cut it at that URL's start, between its name and its value.
            holds += [state[4] for state in self._names.values()]
            holds += [state[4] for state in self._guards.values()]
            holds += [hold for _, end, hold in self._closed if end > base]
        if not holds:
            return None
        return max(0, min(holds) - base)

    # -- the stream ---------------------------------------------------------------------------

    def _forget(self, base: int) -> None:
        # What the cleaner has sent or dropped since the last delta is no longer held.
        self._closed = [span for span in self._closed if span[1] > base]
        for found in (self._names, self._guards, self._users, self._headers):
            for start in [start for start in found if start < base]:
                del found[start]
        self._done = {start for start in self._done if start >= base}
        if self._block is not None and self._block[1] < base:
            self._block = None
        pieces = self._pieces
        gone = 0
        while gone < len(pieces) - 1 and -1 < pieces[gone].end <= base:
            gone += 1
        if gone:
            del pieces[:gone]
        if self._key is not None and self._key < base:
            self._key = base

    def _split(self, whole: str, base: int, old_end: int) -> None:
        """Brings the pieces up to date with the delta."""
        at = old_end
        for space in _SPACE_RUN.finditer(whole, old_end - base):
            begin = space.start() + base
            if begin > at:
                self._grow(whole, base, at, begin)
            self._close(whole, base, begin)
            self._space_end = at = space.end() + base
        if at < self._end:
            self._grow(whole, base, at, self._end)

    def _grow(self, whole: str, base: int, start: int, end: int) -> None:
        pieces = self._pieces
        if not pieces or pieces[-1].end != -1:
            before = pieces[-1] if pieces else None
            piece = _Piece(start, before)
            if piece.after is not None:
                piece.extends = before.query_end or whole[start - base] in "?&#"
            pieces.append(piece)
        piece = pieces[-1]
        if not piece.address:
            piece.address = (
                "://" in whole[max(piece.start, start - 2, base) - base : end - base]
            )

    def _address(self, whole: str, base: int, end: int) -> None:
        """Marks the piece a rule 1 match ends in as an address piece (cr2 M2). A match that
        crossed whitespace ends in a piece with no :// of its own (`ss@h:6379` in
        `redis://:pa ss@h:6379`), which is the URL the whole text's rule 1 leaves, so rule 2 may
        reach the piece after it and that piece holds from it."""
        pieces = self._pieces
        # The last piece starting before `end`, found by bisection: a walk back from the end
        # made one large delta with many such matches quadratic (cr3 M1).
        index = bisect_left(pieces, end, key=_piece_start) - 1
        if index < 0:
            return
        piece = pieces[index]
        if piece.address:
            return
        piece.address = True
        if piece.end == -1:
            return
        # It closed in this delta, so what `_close` and `_grow` decided from it is decided again.
        text = whole[max(piece.start, base) - base : piece.end - base]
        piece.query_end = text[-1:] in ("&", "?") and "?" in text
        if index + 1 < len(pieces) and base <= pieces[index + 1].start < self._end:
            following = pieces[index + 1]
            following.after = piece
            following.extends = (
                piece.query_end or whole[following.start - base] in "?&#"
            )
            if following.extends and following.end != -1:
                self._closed.append((piece.start, following.end, piece.start))

    def _close(self, whole: str, base: int, end: int) -> None:
        pieces = self._pieces
        if not pieces or pieces[-1].end != -1:
            return
        piece = pieces[-1]
        piece.end = end
        # A key's characters hold no whitespace: one in this piece has closed.
        self._key = None
        if piece.address:
            text = whole[max(piece.start, base) - base : end - base]
            piece.query_end = text[-1:] in ("&", "?") and "?" in text
        if piece.extends:
            # Rule 2 has matched: the address piece and this one go out together.
            self._closed.append((piece.after.start, end, piece.after.start))

    def _hold_of(self, at: int) -> int:
        """Where a candidate at `at` holds from: its piece's start, or the start of the address
        piece it is in or follows, so a URL's path and query stay whole (r2 H1, r4 M1)."""
        for piece in reversed(self._pieces):
            if piece.start <= at:
                if piece.address:
                    return piece.start
                if piece.after is not None:
                    return piece.after.start
                return piece.start
        return at

    # -- candidates ---------------------------------------------------------------------------

    def _look(self, whole: str, base: int, start: int) -> bool:
        """Takes the candidates that start at `start` or later. True when a key header opened a
        block: the text after it is then left to the block."""
        begin = start - base
        stop = len(whole)
        opened = False
        found = whole.find("-----BEGIN", max(begin, self._header_seen + 1 - base))
        while found >= 0:
            self._header_seen = found + base
            header = _PEM.match(whole, found)
            if header is not None:
                self._block = [
                    self._hold_of(found + base),
                    found + base,
                    header.end() + base,
                ]
                stop, opened = found, True
                break
            if _HEADER_OPEN.fullmatch(whole, found):
                self._headers[found + base] = self._hold_of(found + base)
            found = whole.find("-----BEGIN", found + 1)
        self._look_urls(whole, base, begin, stop)
        self._look_keys(whole, base, begin, stop)
        self._look_names(whole, base, begin, stop)
        return opened

    def _look_urls(self, whole: str, base: int, begin: int, stop: int) -> None:
        for mark in _URL_MARK.finditer(
            whole, max(begin, self._url_seen + 1 - base), stop
        ):
            at = mark.start()
            self._url_seen = at + base
            scheme = _SCHEME_RUN_BEFORE.search(whole, max(0, at - 64), at)
            first = scheme.start() if scheme else at
            if first < at and whole[first].isalpha():
                # Rule 1 may start here; its user runs to the first of _USER_END.
                self._users[first + base] = [
                    first + base,
                    at + 3 + base,
                    -1,
                    self._hold_of(at + base),
                ]

    def _look_keys(self, whole: str, base: int, begin: int, stop: int) -> None:
        pieces = self._pieces
        if not pieces or pieces[-1].end != -1:
            return
        for key in _SEAM_KEY.finditer(whole, max(begin, pieces[-1].start - base), stop):
            hold = self._hold_of(key.start() + base)
            self._key = hold if self._key is None else min(self._key, hold)

    def _look_names(self, whole: str, base: int, begin: int, stop: int) -> None:
        low = whole[begin:stop].translate(_ASCII_LOWER)
        for pattern, found in ((_SEAM_NAME, self._names), (_SEAM_GUARD, self._guards)):
            for name in pattern.finditer(low):
                at = begin + name.start() + base
                if at not in found and at not in self._done:
                    found[at] = [
                        _FRAME,
                        at + len(name.group()),
                        -1,
                        "",
                        self._hold_of(at),
                    ]

    def _block_end(self, whole: str, base: int) -> int:
        """Looks for the open block's END in the new text and a tail as long as END's longest
        form. Returns where the block closed, or -1 while it is open."""
        block = self._block
        start = max(block[2] - _LONGEST_END, block[1] + 1) - base
        end = _PEM_END.search(whole, start)
        limit = end.start() if end is not None else len(whole)
        later = whole.find(
            "-----BEGIN", max(start, self._header_seen + 1 - base), limit
        )
        while later >= 0:
            self._header_seen = later + base
            if _PEM.match(whole, later) or _HEADER_OPEN.fullmatch(whole, later):
                # Another header before any END: an END pairs with the nearest header before
                # it (r4 L4), so none can close this block. It holds to the message's end.
                self._forever = block[0]
                return -1
            later = whole.find("-----BEGIN", later + 1, limit)
        if end is None:
            block[2] = self._end
            return -1
        self._closed.append((block[1], end.end() + base, block[0]))
        self._block = None
        return end.end() + base

    def _follow(self, whole: str, base: int) -> None:
        """Moves each open candidate on by what the delta brought, searching new text only."""
        for found, step in (
            (self._names, self._name_step),
            (self._guards, self._guard_step),
        ):
            for at, state in list(found.items()):
                closed = step(whole, base, at, state)
                if closed is None:
                    continue
                del found[at]
                if closed > 0:
                    self._done.add(at)
                    self._closed.append((at, closed, state[4]))
        for at, state in list(self._users.items()):
            closed = self._user_step(whole, base, state)
            if closed is None:
                continue
            del self._users[at]
            if closed > 0:
                self._closed.append((at, closed, state[3]))
                self._address(whole, base, closed)
        for at in list(self._headers):
            if _PEM.match(whole, at - base) is not None:
                hold = self._headers.pop(at)
                if self._block is None:
                    self._block = [hold, at, at + 1]
                else:
                    self._forever = self._block[0]
            elif not _HEADER_OPEN.fullmatch(whole, at - base):
                del self._headers[at]

    def _name_step(self, whole: str, base: int, at: int, state: list) -> int | None:
        """None while the named value may still match; its end once it closes; 0 when it can't
        match."""
        if state[0] == _FRAME:
            low = whole[at - base : state[1] - base + _LONGEST_NAME].translate(
                _ASCII_LOWER
            )
            name = _SEAM_NAME.match(low)
            if name is None:
                return 0
            name_end = at - base + len(name.group())
            if _TAIL_OPEN.fullmatch(whole, name_end):
                return None
            if whole.startswith("::", name_end):
                return 0
            tail = _TAIL_PATTERN.match(whole, name_end)
            if tail is None:
                return 0
            value = tail.end()
            if value >= len(whole):
                return None
            if whole[value] in "\"'`":
                state[0], state[2], state[3] = _QUOTED, value + 1 + base, whole[value]
            elif _PLAIN_START.match(whole, value):
                state[0], state[2] = _PLAIN, value + base
            else:
                return 0
        if state[0] == _QUOTED:
            stop = _QUOTE_END[state[3]].search(whole, state[2] - base)
            if stop is None:
                state[2] = self._end
                return None
            closed = stop.end() if whole[stop.start()] == state[3] else stop.start()
            return closed + base
        stop = _PLAIN_END.search(whole, state[2] - base)
        if stop is None:
            state[2] = self._end
            return None
        return stop.start() + base

    def _guard_step(self, whole: str, base: int, at: int, state: list) -> int | None:
        """As `_name_step`, for aws_secret_key and azure_storage_key's own names and tail."""
        if state[0] == _FRAME:
            name_end = state[1] - base
            if _GUARD_TAIL_OPEN.fullmatch(whole, name_end):
                return None
            tail = _GUARD_TAIL.match(whole, name_end)
            if tail is None:
                return 0
            if tail.end() >= len(whole):
                return None
            state[0], state[2] = _PLAIN, tail.end() + base
        stop = _GUARD_VALUE_END.search(whole, state[2] - base)
        if stop is None:
            state[2] = self._end
            return None
        return stop.start() + base

    def _user_step(self, whole: str, base: int, state: list) -> int | None:
        """Rule 1 from this scheme: None while it may still match, its end once it does, 0 when
        it can't."""
        if state[2] < 0:
            stop = _USER_END.search(whole, state[1] - base)
            if stop is None:
                state[1] = self._end
                return None
            if whole[stop.start()] != ":":
                return 0
            state[1] = state[2] = stop.end() + base
        at = whole.find("@", state[1] - base)
        if at < 0:
            state[1] = self._end
            return None if _RULE1_OPEN.fullmatch(whole, state[2] - base) else 0
        match = _RULE1.match(whole, state[0] - base)
        return match.end() + base if match is not None else 0

    def _limit(self, base: int) -> int:
        """The cleaner's own cut, moved back before each open candidate's hold start and out of
        each closed match."""
        holds = [state[4] for state in self._names.values()]
        holds += [state[4] for state in self._guards.values()]
        holds += [state[3] for state in self._users.values() if state[2] >= 0]
        holds += self._headers.values()
        if self._block is not None:
            holds.append(self._block[0])
        if self._key is not None:
            holds.append(self._key)
        if self._pieces:
            last = self._pieces[-1]
            if last.end != -1 and last.address:
                # Rule 2 may yet reach the piece after it.
                holds.append(last.start)
            elif last.end == -1 and last.extends:
                holds.append(last.after.start)
        cut = min(self._space_end, *holds) if holds else self._space_end
        moved = True
        while moved:
            moved = False
            for start, end, hold in self._closed:
                if start < cut < end and hold < cut:
                    cut, moved = hold, True
        return max(cut, base)
