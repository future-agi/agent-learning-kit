"""Saved profiles: which platform, key pair, workspace and run defaults a command uses.

Profiles live in `$AGENT_LEARNING_HOME/profiles.json` (default `~/.agent-learning`).
Secrets never go there: they go to the OS keychain, or, where no keychain is
available, to `credentials.json` beside it, readable only by the owner. A profile
can exist before anyone signs in to it (`profile set`), and signing out keeps the
profile's settings.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import keyring
from keyring.errors import KeyringError

from .errors import PlatformError

_SERVICE = "agent-learn"
DEFAULT_PROFILE = "default"
DEFAULT_API_URL = "https://api.futureagi.com"

# Non-secret settings a profile keeps, as `profile show` names them.
SETTINGS = (
    "api_url",
    "workspace_id",
    "default_mode",
    "default_budget",
    "default_budget_unit",
)
MODES = ("local", "hybrid", "hosted")
BUDGET_UNITS = ("credits", "USD")


@dataclass(frozen=True)
class Profile:
    name: str
    api_url: str
    api_key: str
    secret_key: str
    workspace_id: str | None = None
    settings: dict[str, Any] = field(default_factory=dict)

    @property
    def masked_key(self) -> str:
        return "…" + self.api_key[-4:]


def home() -> Path:
    return Path(
        os.environ.get("AGENT_LEARNING_HOME") or Path.home() / ".agent-learning"
    )


def _index_path() -> Path:
    return home() / "profiles.json"


def _file_secrets_path() -> Path:
    return home() / "credentials.json"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_private(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(path)


def _store_secret(name: str, pair: dict[str, str]) -> str:
    try:
        keyring.set_password(_SERVICE, name, json.dumps(pair))
        return "keychain"
    except KeyringError:
        secrets = _read_json(_file_secrets_path())
        secrets[name] = pair
        _write_private(_file_secrets_path(), secrets)
        return "file"


def _load_secret(name: str, store: str) -> dict[str, str] | None:
    if store == "keychain":
        try:
            raw = keyring.get_password(_SERVICE, name)
        except KeyringError:
            raw = None
        return json.loads(raw) if raw else None
    return _read_json(_file_secrets_path()).get(name)


def _erase_secret(name: str, store: str | None) -> None:
    if store == "keychain":
        try:
            keyring.delete_password(_SERVICE, name)
        except KeyringError:
            pass
    elif store == "file":
        secrets = _read_json(_file_secrets_path())
        if secrets.pop(name, None) is not None:
            _write_private(_file_secrets_path(), secrets)


def current_name() -> str:
    return _read_json(_index_path()).get("current") or DEFAULT_PROFILE


def settings(name: str | None = None) -> dict[str, Any]:
    """A profile's non-secret settings; empty for a profile never saved."""
    entry = (
        _read_json(_index_path()).get("profiles", {}).get(name or current_name(), {})
    )
    return {key: entry.get(key) for key in SETTINGS}


def sign_in(
    name: str, api_url: str, api_key: str, secret_key: str, workspace_id: str | None
) -> None:
    """Save a key pair to profile `name`, creating it if needed, and make it current."""
    store = _store_secret(name, {"api_key": api_key, "secret_key": secret_key})
    index = _read_json(_index_path())
    entry = index.setdefault("profiles", {}).setdefault(name, {})
    entry.update(api_url=api_url, workspace_id=workspace_id, secret_store=store)
    index["current"] = name
    _write_private(_index_path(), index)


def load(name: str | None = None) -> Profile:
    """The named profile, or the current one, with its key pair."""
    chosen = name or current_name()
    entry = _read_json(_index_path()).get("profiles", {}).get(chosen)
    pair = _load_secret(chosen, entry.get("secret_store", "")) if entry else None
    if not pair:
        raise PlatformError(
            "auth",
            f"Profile {chosen!r} is not signed in.",
            code="not_signed_in",
            fix=f"Run `agent-learn auth login --profile {chosen}`."
            if chosen != DEFAULT_PROFILE
            else "Run `agent-learn auth login`.",
        )
    return Profile(
        name=chosen,
        api_url=entry["api_url"],
        api_key=pair["api_key"],
        secret_key=pair["secret_key"],
        workspace_id=entry.get("workspace_id"),
        settings={key: entry.get(key) for key in SETTINGS},
    )


def listing() -> list[dict[str, Any]]:
    """Every saved profile, without secrets, and which one is current."""
    index = _read_json(_index_path())
    current = index.get("current")
    return [
        {
            "name": name,
            **{key: entry.get(key) for key in SETTINGS},
            "signed_in": bool(entry.get("secret_store")),
            "current": name == current,
        }
        for name, entry in sorted(index.get("profiles", {}).items())
    ]


def use(name: str) -> None:
    """Make `name` the profile commands use."""
    index = _read_json(_index_path())
    if name not in index.get("profiles", {}):
        raise PlatformError(
            "validation",
            f"No saved profile named {name!r}.",
            code="profile_not_found",
            field="profile",
            fix="See the saved profiles with `agent-learn profile list`.",
        )
    index["current"] = name
    _write_private(_index_path(), index)


def update(name: str, changes: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    """Create or change profile `name`'s settings; the `(before, after)` of each change."""
    unknown = set(changes) - set(SETTINGS)
    if unknown:
        raise ValueError(f"not profile settings: {sorted(unknown)}")
    index = _read_json(_index_path())
    entry = index.setdefault("profiles", {}).setdefault(name, {})
    diff = {
        key: (entry.get(key), value)
        for key, value in changes.items()
        if entry.get(key) != value
    }
    entry.update(changes)
    entry.setdefault("api_url", DEFAULT_API_URL)
    index.setdefault("current", name)
    _write_private(_index_path(), index)
    return diff


def sign_out(names: list[str]) -> list[str]:
    """Erase the key pairs of `names`; their settings stay. The names that were signed in."""
    index = _read_json(_index_path())
    signed_out = []
    for name in names:
        entry = index.get("profiles", {}).get(name)
        if entry and entry.get("secret_store"):
            _erase_secret(name, entry.pop("secret_store"))
            signed_out.append(name)
    _write_private(_index_path(), index)
    return signed_out
