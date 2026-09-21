"""Constrained model-assisted repair for generic authored artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .backends import FILE_TOOLS, SessionSpec, tool, tool_server
from .diagnostics import HarnessDiagnostic
from .repair_patch import (
    RepairPatchOperation,
    WorldIRRepairPatch,
    apply_world_ir_repair_patch,
)
from .session import Stage
from .source_model import SourceModel
from .world_ir import WorldIR


class _PatchSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # An empty patch cannot repair a candidate. Accepting one previously consumed a full
    # authoring budget and launched another expensive clean-room validation with unchanged
    # semantics. Require an actual, typed mutation at the only write boundary.
    operations: tuple[RepairPatchOperation, ...] = Field(min_length=1)


async def request_world_ir_patch(
    source_root: Path,
    source: SourceModel,
    world: WorldIR,
    diagnostics: tuple[HarnessDiagnostic, ...],
) -> WorldIRRepairPatch:
    """Ask for one typed patch; validation happens inside the only mutating tool."""

    allowed = {item.code for item in diagnostics if item.repair_strategy}
    captured: list[WorldIRRepairPatch] = []

    @tool(
        "submit_world_ir_patch",
        "Submit the smallest evidence-backed patch to generated World IR. This tool cannot "
        "edit submitted source, delete scenarios, weaken checks, or change infrastructure.",
        _PatchSubmission.model_json_schema(),
    )
    async def submit(arguments: dict[str, object]) -> dict[str, object]:
        try:
            submission = _PatchSubmission.model_validate(arguments)
            patch = WorldIRRepairPatch.create(
                base_world_ir_hash=world.fingerprint,
                operations=submission.operations,
            )
            apply_world_ir_repair_patch(
                world, source, patch, allowed_reason_codes=allowed
            )
        except Exception as exc:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"patch rejected: {type(exc).__name__}: {exc}",
                    }
                ],
                "is_error": True,
            }
        captured[:] = [patch]
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"patch accepted with {len(patch.operations)} operations",
                }
            ]
        }

    diagnostics_json = [
        item.model_dump(mode="json", exclude={"redacted_message"})
        | {"message": item.redacted_message}
        for item in diagnostics
    ]
    system_prompt = """You repair generated environment data for arbitrary agent runtimes.
Submitted source and the canonical source model are authoritative. Never modify source, invent
an action/service, remove a scenario, weaken a check, or alter credentials/egress. Use Read, Glob,
and Grep only to verify source evidence. Your only write capability is submit_world_ir_patch.
Every operation reason must exactly match one supplied diagnostic code. Make the smallest patch
that resolves the complete diagnostic set, call the tool, then stop."""
    briefing = (
        "Repair this candidate.\n\nDIAGNOSTICS\n"
        + json.dumps(diagnostics_json, sort_keys=True, indent=2)[:16000]
        + "\n\nCANONICAL SOURCE MODEL\n"
        + source.model_dump_json(indent=2)[:24000]
        + "\n\nCURRENT GENERATED WORLD IR\n"
        + world.model_dump_json(indent=2)[:32000]
    )
    stage = Stage(
        SessionSpec(
            system_prompt=system_prompt,
            servers={"repair": tool_server("repair", tools=[submit])},
            builtins=FILE_TOOLS,
            cwd=str(source_root.resolve()),
            # Complex repositories can require several evidence reads before the model can
            # construct a valid typed patch.  Keep repair bounded, but do not give it a
            # smaller budget than an ordinary authoring stage.  The backend terminates as
            # soon as submit_world_ir_patch succeeds, so this is a ceiling rather than a
            # target.
            max_turns=40,
            gated=True,
            thinking=True,
        ),
        name="repair-world-ir",
    )
    async with stage:
        await stage.say(briefing)
        if not captured:
            await stage.say(
                "No valid patch was submitted. Call submit_world_ir_patch now, or explicitly "
                "state that the diagnostic cannot be repaired without changing submitted source."
            )
    if not captured:
        raise RuntimeError("typed_world_ir_patch_not_submitted")
    return captured[0]


__all__ = ["request_world_ir_patch"]
