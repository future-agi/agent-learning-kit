"""Writing the scenarios, and handing that work out to writers.

The skill names live here because both halves need them: the stage names the skill it runs under,
and delegation names the one it dispatches a writer with. Holding them in either module would
make the other import it, and the dependency between them is deliberately one way.
"""

# Which skill each session is opened under. `scenarios` is the parent that plans and saves,
# `scenarios/write` is what a writer runs, `scenarios/plan` is the planning pass.
PARENT_SKILL = "scenarios"
SKILL = "scenarios/write"
PLAN_SKILL = "scenarios/plan"
