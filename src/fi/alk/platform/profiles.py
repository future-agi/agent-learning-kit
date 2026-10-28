"""Saved sign-ins: which platform, which key pair and which workspace a profile acts in.

Profiles live in `$AGENT_LEARNING_HOME/profiles.json` (default `~/.agent-learning`).
Secrets never go there: they go to the OS keychain, or, where no keychain is
available, to `credentials.json` beside it, readable only by the owner.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import keyring
from keyring.errors import KeyringError

from .errors import PlatformError

_SERVICE = "agent-learn"
DEFAULT_PROFILE = "default"


@dataclass(frozen=True)
class Profile:
    name: str
    base_url: str
    api_key: str
    secret_key: str
    workspace_id: str | None = None

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


def save(profile: Profile) -> None:
    """Save `profile` and make it the one commands use."""
    store = _store_secret(
        profile.name,
        {"api_key": profile.api_key, "secret_key": profile.secret_key},
    )
    index = _read_json(_index_path())
    profiles = index.setdefault("profiles", {})
    profiles[profile.name] = {
        "base_url": profile.base_url,
        "workspace_id": profile.workspace_id,
        "secret_store": store,
    }
    index["current"] = profile.name
    _write_private(_index_path(), index)


def load(name: str | None = None) -> Profile:
    """The named profile, or the current one."""
    index = _read_json(_index_path())
    chosen = name or index.get("current")
    entry = index.get("profiles", {}).get(chosen or "")
    if not chosen or entry is None:
        raise PlatformError(
            "auth",
            f"No saved profile named {chosen!r}." if chosen else "Not signed in.",
            code="not_signed_in",
            fix="Run `agent-learn auth login`.",
        )
    pair = _load_secret(chosen, entry.get("secret_store", "keychain"))
    if not pair:
        raise PlatformError(
            "auth",
            f"The key pair for profile {chosen!r} is missing from the "
            f"{entry.get('secret_store', 'keychain')}.",
            code="credentials_missing",
            fix=f"Run `agent-learn auth login --profile {chosen}` again.",
        )
    return Profile(
        name=chosen,
        base_url=entry["base_url"],
        api_key=pair["api_key"],
        secret_key=pair["secret_key"],
        workspace_id=entry.get("workspace_id"),
    )


def listing() -> list[dict[str, Any]]:
    """Every saved profile, without secrets, and which one is current."""
    index = _read_json(_index_path())
    current = index.get("current")
    return [
        {
            "name": name,
            "base_url": entry["base_url"],
            "workspace_id": entry.get("workspace_id"),
            "current": name == current,
        }
        for name, entry in sorted(index.get("profiles", {}).items())
    ]


def use(name: str) -> None:
    """Make `name` the profile commands use."""
    index = _read_json(_index_path())
    if name not in index.get("profiles", {}):
        raise PlatformError(
            "not_found",
            f"No saved profile named {name!r}.",
            code="profile_not_found",
            fix="See the saved profiles with `agent-learn profile list`.",
        )
    index["current"] = name
    _write_private(_index_path(), index)


SETTABLE = {"base_url": "base_url", "workspace": "workspace_id"}


def update(name: str, field: str, value: str | None) -> Profile:
    """Change one setting of a saved profile; the key pair is changed by signing in again."""
    if field not in SETTABLE:
        raise PlatformError(
            "validation",
            f"A profile has no setting {field!r}.",
            code="profile_field_unknown",
            fix=f"Set one of: {', '.join(sorted(SETTABLE))}.",
        )
    profile = load(name)
    index = _read_json(_index_path())
    index["profiles"][profile.name][SETTABLE[field]] = value
    _write_private(_index_path(), index)
    return load(profile.name)


def remove(name: str | None = None) -> str:
    """Forget a saved profile and erase its key pair; the name of the one removed."""
    index = _read_json(_index_path())
    chosen = name or index.get("current")
    entry = index.get("profiles", {}).pop(chosen or "", None)
    if entry is None:
        raise PlatformError(
            "auth",
            f"No saved profile named {chosen!r}." if chosen else "Not signed in.",
            code="not_signed_in",
        )
    if entry.get("secret_store") == "keychain":
        try:
            keyring.delete_password(_SERVICE, chosen)
        except KeyringError:
            pass
    else:
        secrets = _read_json(_file_secrets_path())
        secrets.pop(chosen, None)
        _write_private(_file_secrets_path(), secrets)
    if index.get("current") == chosen:
        index["current"] = next(iter(sorted(index["profiles"])), None)
    _write_private(_index_path(), index)
    return chosen
