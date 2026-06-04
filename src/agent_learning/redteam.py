from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping, Optional

from ._facade import optional_module

AGENT_LEARNING_REDTEAM_KIND = "agent-learning.redteam.v1"
_SIMULATE_EXTRA = "simulate"


def _manifest() -> Any:
    return optional_module("fi.simulate.manifest", _SIMULATE_EXTRA)


def load_manifest_file(path: str | Path) -> dict[str, Any]:
    return _manifest().load_manifest_file(path)


load_manifest = load_manifest_file


def prepare_redteam_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return _manifest().prepare_redteam_manifest(manifest)


async def redteam_manifest_file(
    path: str | Path,
    *,
    options: Optional[Any] = None,
    name: Optional[str] = None,
    threshold: Optional[float] = None,
    dry_run: Optional[bool] = None,
) -> dict[str, Any]:
    payload = await _manifest().redteam_manifest_file(
        path,
        options=options,
        name=name,
        threshold=threshold,
        dry_run=dry_run,
    )
    return _public_redteam_payload(payload)


run_redteam_manifest_file = redteam_manifest_file


async def redteam_manifest(
    manifest: Mapping[str, Any],
    *,
    manifest_path: str | Path = ".",
    options: Optional[Any] = None,
    name: Optional[str] = None,
    threshold: Optional[float] = None,
    dry_run: Optional[bool] = None,
) -> dict[str, Any]:
    payload = await _manifest().redteam_manifest(
        manifest,
        manifest_path=manifest_path,
        options=options,
        name=name,
        threshold=threshold,
        dry_run=dry_run,
    )
    return _public_redteam_payload(payload)


run_redteam_manifest = redteam_manifest


def render_junit(result: Mapping[str, Any]) -> str:
    return _manifest().render_junit(result)


def render_sarif(
    result: Mapping[str, Any],
    *,
    manifest_path: str | Path = ".",
) -> str:
    return _manifest().render_sarif(result, manifest_path=manifest_path)


def render_markdown(
    result: Mapping[str, Any],
    *,
    source_path: str | Path = ".",
) -> str:
    return _manifest().render_markdown(result, source_path=source_path)


def required_manifest_env(manifest: Mapping[str, Any]) -> list[str]:
    return _manifest().required_manifest_env(manifest)


def missing_manifest_env(manifest: Mapping[str, Any]) -> list[str]:
    return _manifest().missing_manifest_env(manifest)


def validate_manifest_env(manifest: Mapping[str, Any]) -> None:
    _manifest().validate_manifest_env(manifest)


def _public_redteam_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(payload))
    result["kind"] = AGENT_LEARNING_REDTEAM_KIND
    return result


__all__ = [
    "AGENT_LEARNING_REDTEAM_KIND",
    "load_manifest",
    "load_manifest_file",
    "missing_manifest_env",
    "prepare_redteam_manifest",
    "redteam_manifest",
    "redteam_manifest_file",
    "render_junit",
    "render_markdown",
    "render_sarif",
    "required_manifest_env",
    "run_redteam_manifest",
    "run_redteam_manifest_file",
    "validate_manifest_env",
]
