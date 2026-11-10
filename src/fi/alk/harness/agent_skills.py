from __future__ import annotations

import hashlib
import json
import logging
import shutil
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import TypeAdapter, ValidationError

from .job import JobSkill

logger = logging.getLogger(__name__)

SKILL_TRACE_SOURCE = "alk_skill"
OBSERVER_MARKER_SUFFIX = ".skill-observer"
CONTEXT_FILES = frozenset({"AGENTS.md", "CLAUDE.md"})
_CLAUDE_SDK_MODULE = "claude_agent_sdk"
_SKILLS = TypeAdapter(list[JobSkill])


class SkillPlacementError(ValueError):
    pass


def read_job_skills(raw: Any) -> tuple[JobSkill, ...]:
    if not isinstance(raw, dict) or "skills" not in raw:
        return ()
    try:
        return tuple(_SKILLS.validate_python(raw["skills"]))
    except ValidationError:
        logger.error("the job's skills do not match the expected shape; none placed")
        return ()


def job_skills(work_directory: Path) -> tuple[JobSkill, ...]:
    try:
        raw = json.loads((work_directory / "job.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    return read_job_skills(raw)


def files_hash(skill: JobSkill) -> str:
    pairs = sorted(
        [file.path, hashlib.sha256(file.content.encode("utf-8")).hexdigest()]
        for file in skill.files
    )
    return hashlib.sha256(
        json.dumps(pairs, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _relative(raw: str) -> PurePosixPath:
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SkillPlacementError(f"skill file path is not relative: {raw!r}")
    return path


def _uses_claude_sdk(build_dir: Path) -> bool:
    from .instrumentation_evidence import _python_imports

    return any(
        name.split(".")[0] == _CLAUDE_SDK_MODULE for name in _python_imports(build_dir)
    )


def skills_directory(build_dir: Path) -> Path:
    if _uses_claude_sdk(build_dir):
        return build_dir / ".claude" / "skills"
    return build_dir / "skills"


def _write(target_root: Path, skill: JobSkill) -> None:
    for file in skill.files:
        target = target_root / _relative(file.path)
        if not target.resolve().is_relative_to(target_root.resolve()):
            raise SkillPlacementError(f"skill file escapes its folder: {file.path!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file.content, encoding="utf-8")


def place_skills(build_dir: Path, skills: Sequence[JobSkill]) -> None:
    skills = [skill for skill in skills if skill.origin == "library"]
    if not skills:
        return
    root = skills_directory(build_dir)
    for skill in skills:
        if files_hash(skill) != skill.files_hash:
            raise SkillPlacementError(
                f"skill {skill.name} v{skill.version_number} does not match its hash"
            )
        if skill.kind == "context":
            names = {_relative(file.path).as_posix() for file in skill.files}
            if not names & CONTEXT_FILES:
                raise SkillPlacementError(
                    f"context {skill.name} holds no AGENTS.md or CLAUDE.md"
                )
            _write(build_dir, skill)
            continue
        folder = root / skill.name
        if folder.is_symlink() or folder.is_file():
            folder.unlink()
        elif folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        _write(folder, skill)
    logger.info(
        "placed %d skill(s) under %s",
        len(skills),
        root.relative_to(build_dir).as_posix(),
    )


def observer_marker(trace_path: Path) -> Path:
    return trace_path.with_name(trace_path.name + OBSERVER_MARKER_SUFFIX)


def _loaded_names(trace_path: Path) -> dict[str, float]:
    try:
        lines = trace_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    first: dict[str, float] = {}
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("source") != SKILL_TRACE_SOURCE:
            continue
        name = record.get("skill")
        at = record.get("at")
        if isinstance(name, str) and name:
            stamp = float(at) if isinstance(at, (int, float)) else 0.0
            first.setdefault(name, stamp)
    return first


def collect_skill_loads(
    trace_path: Path | None, skills: Sequence[JobSkill]
) -> list[dict[str, Any]] | None:
    placed = [skill for skill in skills if skill.kind == "skill"]
    if not placed:
        return None
    observed = trace_path is not None and observer_marker(trace_path).is_file()
    loaded = _loaded_names(trace_path) if observed and trace_path is not None else {}
    return [
        {
            "name": skill.name,
            "origin": skill.origin,
            "version_number": skill.version_number,
            "observed": observed,
            "loaded": ("yes" if skill.name in loaded else "no")
            if observed
            else "unknown",
            "at": loaded.get(skill.name),
        }
        for skill in placed
    ]
