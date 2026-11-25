"""Author a reviewed addition with the ordinary scenario writer in its hosted sandbox."""

import asyncio
import json
from pathlib import Path

from .catalogue import load_catalogue
from .contract import AgentContract
from .folder import read_all, write_folder, write_index
from .proofs import load_proofs
from .reviewed_build import ReviewedBuild
from .scenarios import Slice, _write_slice


async def author(workspace: Path, approved: ReviewedBuild, output: Path) -> None:
    contract = AgentContract.model_validate_json(
        (workspace / "contract.json").read_bytes()
    )
    previous = read_all(workspace)
    headings = list(dict.fromkeys(row.use_case for row in previous if row.use_case))
    names = {row.name for row in previous}
    keys = {row.scenario_key for row in previous}
    examples = [
        {
            "name": row.name,
            "situation": row.instruction.split("Reviewed context:")[0][:1200],
        }
        for row in previous[:3]
    ]
    guidance = (
        "Extend this suite using each assigned case once. Reuse a matching use case or name a "
        "specific new activity. Use descriptive names and detailed simulator briefs in the style "
        "of the existing scenarios. Keep grading answers out of the name and situation. "
        "Use a distinct scenario name for each addition. Existing headings and examples: "
        + json.dumps(
            {"headings": headings, "examples": examples, "used_names": sorted(names)}
        )
    )
    (workspace / "reviewed-source.json").write_text(json.dumps(approved.pin))
    original_catalogue = load_catalogue(workspace)
    additions = []
    for start in range(0, len(approved.cases), 3):
        assigned = approved.subset(approved.cases[start : start + 3])
        batch = Slice(
            "Approved source cases",
            count=len(assigned.cases),
            why=guidance,
            choose_heading=True,
        )
        written = await _write_slice(
            contract,
            batch,
            [],
            index=start,
            destination=workspace,
            on_event=None,
            ask=None,
            reviewed=assigned,
        )
        assigned.check_complete(written, workspace)
        if len(written) != len(assigned.cases):
            raise ValueError(
                "Writer returned a different number of scenarios than assigned"
            )
        for row in written:
            if row.name in names or row.scenario_key in keys:
                raise ValueError("Writer reused an existing scenario identity")
            keys.add(row.scenario_key)
            names.add(row.name)
            write_folder(row, load_catalogue(workspace), output)
            additions.append(row)
    approved.check_complete(additions, workspace)
    catalogue = load_catalogue(workspace)
    referenced = {goal for row in additions for goal in row.sub_goals}
    if referenced - catalogue.names():
        raise ValueError("A scenario refers to an unknown sub-goal")
    new_goals = [
        goal.model_dump(mode="json")
        for goal in catalogue.sub_goals
        if goal.name in referenced and goal.name not in original_catalogue.names()
    ]
    if new_goals:
        (output / "added-sub-goals.json").write_text(json.dumps(new_goals))
    proofs = load_proofs(workspace)
    write_index(additions, output)
    (output / "proofs.json").write_text(
        json.dumps({s.scenario_key: proofs[s.scenario_key] for s in additions})
    )


if __name__ == "__main__":
    import sys

    workspace, request, output = map(Path, sys.argv[1:])
    asyncio.run(
        author(workspace, ReviewedBuild(**json.loads(request.read_bytes())), output)
    )
