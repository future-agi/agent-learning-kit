"""One error type for everything that can go wrong talking to the platform.

Every failure, whether the platform refused the call or never answered, becomes a
`PlatformError` with a stable category. The CLI turns the category into its exit code
and its JSON error document, so a script can branch on either without reading text.
"""

from __future__ import annotations

from typing import Any, Mapping

# category -> exit code, as the CLI reference specifies them; never renumber one.
EXIT_CODES = {
    "validation": 2,
    "auth": 3,
    "permission": 3,
    "allowance": 4,
    "infrastructure": 5,
}

_BY_STATUS = {
    401: "auth",
    402: "allowance",
    403: "permission",
    429: "allowance",
}

_FIXES = {
    "auth": "Sign in again with `agent-learn auth login`, using a key pair from the platform's Keys page.",
    "permission": "Ask a workspace admin for access, or act in another workspace with `--workspace`.",
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
        field: str | None = None,
        retryable: bool | None = None,
        request_id: str | None = None,
        fix: str = "",
        status: int | None = None,
    ) -> None:
        if category not in EXIT_CODES:
            raise ValueError(f"unknown error category {category!r}")
        super().__init__(message)
        self.category = category
        self.message = message
        self.code = code or category
        self.field = field
        self.retryable = (
            category == "infrastructure" if retryable is None else retryable
        )
        self.request_id = request_id
        self.fix = fix or _FIXES.get(category, "")
        # The HTTP status, when the platform answered; not part of the document.
        self.status = status

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.category]

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "code": self.code,
            "message": self.message,
            "field": self.field,
            "retryable": self.retryable,
            "request_id": self.request_id,
            "fix": self.fix,
        }


def from_response(
    status: int, body: Any, *, request_id: str | None = None
) -> PlatformError:
    """The error a non-success response stands for.

    Reads the platform's common error envelope (`type`, `code`, `message`,
    `details`) and the older `{"detail": ...}` and `{"error": {...}}` shapes some
    routes still return.
    """
    message, code, field, retryable = _parts(body)
    category = (
        "infrastructure" if status >= 500 else _BY_STATUS.get(status, "validation")
    )
    return PlatformError(
        category,
        message or f"The platform answered HTTP {status}.",
        code=code,
        field=field,
        retryable=retryable,
        request_id=request_id,
        status=status,
    )


def _parts(body: Any) -> tuple[str, str, str | None, bool | None]:
    if not isinstance(body, Mapping):
        return (str(body).strip()[:500] if body else ""), "", None, None
    nested = body.get("error")
    if isinstance(nested, Mapping):
        body = nested
    message = body.get("message") or body.get("detail") or body.get("error") or ""
    if isinstance(message, (list, tuple)):
        message = "; ".join(str(part) for part in message)
    code = body.get("error_code") or body.get("code") or ""
    details = body.get("details")
    field = (
        next(
            (name for name in details if name not in ("detail", "non_field_errors")),
            None,
        )
        if isinstance(details, Mapping)
        else None
    )
    retryable = body.get("retryable")
    return (
        str(message),
        str(code),
        field,
        retryable if isinstance(retryable, bool) else None,
    )
