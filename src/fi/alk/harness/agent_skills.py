from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from .job import JobSkill

logger = logging.getLogger(__name__)

USAGE_SOURCE = "otel_spans"
INSTRUMENTED = frozenset({"traceai", "opentelemetry"})
_SKILLS = TypeAdapter(list[JobSkill])


def read_job_skills(raw: Any) -> tuple[JobSkill, ...]:
    if not isinstance(raw, dict) or "skills" not in raw:
        return ()
    try:
        return tuple(_SKILLS.validate_python(raw["skills"]))
    except ValidationError:
        logger.error("the job's skills do not match the expected shape; none recorded")
        return ()


def job_skills(work_directory: Path) -> tuple[JobSkill, ...]:
    try:
        raw = json.loads((work_directory / "job.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    return read_job_skills(raw)


def skill_usage(
    skills: Sequence[JobSkill], instrumentation: str | None
) -> dict[str, Any] | None:
    if not skills:
        return None
    return {
        "instrumented": (instrumentation or "").strip().lower() in INSTRUMENTED,
        "observed": None,
        "source": USAGE_SOURCE,
    }
