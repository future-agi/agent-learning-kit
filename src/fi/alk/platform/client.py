"""The connection to a FutureAGI platform that the CLI and Python callers share.

A `Client` holds one key pair and one workspace. Every call it makes carries the
same headers, and every failure comes back as a `PlatformError`, so callers never see
an HTTP library's exceptions or a raw response.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from . import profiles
from .errors import PlatformError, from_response

DEFAULT_TIMEOUT = 30.0
# A submission is sent at most this many times, always with the same retry key.
SUBMIT_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 0.5
RETRY_STATUSES = frozenset({502, 503, 504})


@dataclass(frozen=True)
class Identity:
    """Who a key pair signs in as, and where its calls act."""

    user: dict[str, Any]
    organization: dict[str, Any]
    workspace: dict[str, Any]
    role: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "user": self.user,
            "organization": self.organization,
            "workspace": self.workspace,
            "role": self.role,
        }


class Client:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        secret_key: str,
        workspace_id: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key or not secret_key:
            raise PlatformError(
                "auth",
                "An API key and a secret key are both required.",
                code="credentials_missing",
            )
        self.base_url = base_url.rstrip("/")
        self.workspace_id = workspace_id
        headers = {
            "X-Api-Key": api_key,
            "X-Secret-Key": secret_key,
            "Accept": "application/json",
        }
        if workspace_id:
            headers["X-Workspace-Id"] = workspace_id
        self._http = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            timeout=timeout,
            transport=transport,
        )
        # The platform answers a workspace it cannot use with its default one rather
        # than an error, so a client checks once that it acts where it was told to.
        self._identity: Identity | None = None

    @classmethod
    def from_profile(cls, name: str | None = None, **options: Any) -> "Client":
        profile = profiles.load(name)
        return cls(
            base_url=profile.base_url,
            api_key=profile.api_key,
            secret_key=profile.secret_key,
            workspace_id=profile.workspace_id,
            **options,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        """One call; the decoded JSON body, or a `PlatformError`.

        A client given a workspace never acts in another: the first call checks that
        the platform resolved that workspace and raises `workspace_not_found` if not.
        """
        if self.workspace_id and self._identity is None:
            self.whoami()
        return self._send(method, path, **kwargs)

    def _send(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise PlatformError(
                "infrastructure",
                f"Could not reach the platform at {self.base_url}: {exc}",
                code="unreachable",
            ) from exc
        body = _decode(response)
        if response.is_success:
            return body
        raise from_response(response.status_code, body)

    def submit(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        key_in: str = "header",
    ) -> Any:
        """Create something on the platform at most once.

        The call carries a retry key, `idempotency_key` or a fresh one, as the
        `Idempotency-Key` header or, for routes that take it there
        (`key_in="body"`), as the body's `idempotency_key`. When the answer is lost
        or the platform is briefly unavailable, the same call is sent again with the
        same key, so the platform returns the original instead of doing it twice.
        """
        if key_in not in ("header", "body"):
            raise ValueError("key_in is 'header' or 'body'")
        key = idempotency_key or f"alk-{uuid.uuid4().hex}"
        headers = {"Idempotency-Key": key} if key_in == "header" else {}
        body = {**payload, "idempotency_key": key} if key_in == "body" else payload
        if self.workspace_id and self._identity is None:
            self.whoami()
        for attempt in range(1, SUBMIT_ATTEMPTS + 1):
            try:
                return self._send("POST", path, json=body, headers=headers)
            except PlatformError as error:
                retryable = (
                    error.code == "unreachable" or error.status in RETRY_STATUSES
                )
                if not retryable or attempt == SUBMIT_ATTEMPTS:
                    raise
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
        raise AssertionError("unreachable")  # pragma: no cover

    def whoami(self) -> Identity:
        info = self._send("GET", "/accounts/user-info/")
        organization = info.get("organization") or {}
        identity = Identity(
            user={
                "id": info.get("id"),
                "email": info.get("email"),
                "name": info.get("name"),
            },
            organization={
                "id": organization.get("id"),
                "name": organization.get("display_name") or organization.get("name"),
            },
            workspace={
                "id": info.get("default_workspace_id"),
                "name": info.get("default_workspace_display_name")
                or info.get("default_workspace_name"),
            },
            role=info.get("organization_role"),
        )
        resolved = identity.workspace["id"]
        if self.workspace_id and resolved != self.workspace_id:
            raise PlatformError(
                "not_found",
                f"Workspace {self.workspace_id} is not one this key can act in; "
                f"the platform would have used {resolved} instead.",
                code="workspace_not_found",
                fix="Copy the workspace id from the platform's workspace switcher, or use "
                "a key that is not bound to another workspace.",
            )
        self._identity = identity
        return identity


def reachable(base_url: str, *, timeout: float = 10.0) -> None:
    """Raise an infrastructure `PlatformError` unless the platform answers its health check."""
    url = f"{base_url.rstrip('/')}/health/"
    try:
        response = httpx.get(url, timeout=timeout)
    except httpx.HTTPError as exc:
        raise PlatformError(
            "infrastructure",
            f"Could not reach the platform at {base_url}: {exc}",
            code="unreachable",
        ) from exc
    if not response.is_success:
        raise PlatformError(
            "infrastructure",
            f"The platform at {base_url} answered its health check with HTTP "
            f"{response.status_code}.",
            code="unhealthy",
            status=response.status_code,
        )


def _decode(response: httpx.Response) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return response.text
