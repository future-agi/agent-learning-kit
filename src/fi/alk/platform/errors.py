"""One error type for everything that can go wrong talking to the platform.

Every failure, whether the platform refused the call or never answered, becomes a
`PlatformError` with a stable category. The CLI turns the category into its exit code
and its JSON error document, so a script can branch on either without reading text.
"""

from __future__ import annotations

from typing import Any, Mapping

# category -> exit code. Documented in docs/reference/cli.md; never renumber one.
EXIT_CODES = {
    "validation": 3,
    "auth": 4,
    "permission": 5,
    "allowance": 6,
    "not_found": 7,
    "conflict": 8,
    "infrastructure": 9,
}

_BY_STATUS = {
    400: "validation",
    401: "auth",
    402: "allowance",
    403: "permission",
    404: "not_found",
    409: "conflict",
    413: "validation",
    415: "validation",
    422: "validation",
    429: "allowance",
}

_FIXES = {
    "auth": "Sign in again with `agent-learn auth login`, using a key pair from the platform's Keys page.",
    "permission": "Ask a workspace admin for access, or pick another workspace with `agent-learn profile set workspace <id>`.",
    "allowance": "Check the organization's plan and usage on the platform's Billing page.",
    "infrastructure": "Check the platform URL and your connection, then retry; `agent-learn doctor` shows what is reachable.",
}


class PlatformError(Exception):
    """A call that did not succeed, with what kind of failure it was and how to fix it."""

    def __init__(
        self,
        category: str,
        message: str,
        *,
        code: str = "",
        fix: str = "",
        status: int | None = None,
    ) -> None:
        if category not in EXIT_CODES:
            raise ValueError(f"unknown error category {category!r}")
        super().__init__(message)
        self.category = category
        self.message = message
        self.code = code or category
        self.fix = fix or _FIXES.get(category, "")
        self.status = status

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.category]

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "code": self.code,
            "message": self.message,
            "fix": self.fix,
            "status": self.status,
        }


def from_response(status: int, body: Any) -> PlatformError:
    """The error a non-success response stands for.

    Reads the platform's common error envelope (`type`, `code`, `message`) and the
    older `{"detail": ...}` and `{"error": {...}}` shapes some routes still return.
    """
    message, code = _message_and_code(body)
    if status >= 500:
        category = "infrastructure"
    else:
        category = _BY_STATUS.get(status, "validation")
    return PlatformError(
        category,
        message or f"The platform answered HTTP {status}.",
        code=code,
        status=status,
    )


def _message_and_code(body: Any) -> tuple[str, str]:
    if not isinstance(body, Mapping):
        return (str(body).strip()[:500] if body else ""), ""
    nested = body.get("error")
    if isinstance(nested, Mapping):
        return str(nested.get("message") or ""), str(nested.get("code") or "")
    message = body.get("message") or body.get("detail") or body.get("error") or ""
    if isinstance(message, (list, tuple)):
        message = "; ".join(str(part) for part in message)
    code = body.get("error_code") or body.get("code") or ""
    return str(message), str(code)
