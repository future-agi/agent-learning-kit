"""Every prompt in the pipeline. Self-contained by rule: each prompt defines every term it uses.

A fresh model with no context about this codebase or this team must be able to do the task from the
prompt alone. No internal shorthand, no references to meetings or documents, no undefined jargon.
All agent-specific grounding arrives through the contract brief injected at call time.
"""

from __future__ import annotations

import json

# The scenario model, written as definitions a fresh model can act on.
SCENARIO_MODEL = """You help test an AI agent by designing test scenarios. Definitions used throughout:

- AGENT UNDER TEST: the AI system being evaluated. Its real interface (tools, argument names, valid
  values, data) is given to you as a CONTRACT. You may only ever reference what the contract lists,
  with exact spelling. Inventing a tool, argument, menu item, table, or id that is not in the
  contract makes the test worthless.
- USE CASE: one real job a user hires this agent for, stated from the user's side. Example for a
  food-ordering agent: "Order a combo meal". Example for a database agent: "Ask for a sales total".
- SCENARIO: one concrete test. It fixes ONE specific situation inside one use case: a specific state
  of the world plus a specific thing the user wants. Two scenarios are different only if the correct
  END RESULT differs, not just the wording. "The item is in stock" and "the item is out of stock"
  are two scenarios because the correct outcome differs. Never write two scenarios that are the same
  situation reworded.
- SUB-GOAL: a milestone inside one scenario that must be true for the scenario to end correctly.
  Example: "the drink was elicited", "the refund was recorded". 3 to 6 per scenario. A sub-goal is
  something a product owner would recognise, not an internal implementation step like "the JSON
  parsed" and not a micro-step like "the agent said hello".
- CHECKPOINT: the machine-checkable rule that decides whether one sub-goal was met. Checkpoints must
  test the RIGHT VALUES, not just that something happened: if the user asked for 11 PM and the agent
  booked 10 PM, a checkpoint that only verifies "a booking call happened" wrongly passes; the
  checkpoint must assert the booked time equals 11 PM.
- ENVIRONMENT: the mocked world the agent acts on during the test: seeded state (what records or
  stock exist) plus canned responses for the agent's tools. The agent's own reasoning is never
  mocked; only the world it acts on is.
- SIMULATED USER: for conversational agents (voice or chat), a separate AI plays the user during the
  test. It receives a situation instruction (who it is, what it wants, what it knows). It does NOT
  see the checkpoints, the seeded environment, or the expected outcome.

Three parts of every scenario stay strictly separate, because leaking one into another invalidates
the test:
(A) INPUT: what the agent or the simulated user is told. Never contains the answer, the checks, or
    facts about the environment the user could not know.
(B) ENVIRONMENT: the seeded world state and mock tool responses.
(C) CHECKPOINTS: the hidden pass/fail rules, graded after the run.

Quality bar for every scenario you write:
- A competent implementation of this agent could plausibly FAIL it. If any correct implementation
  passes it for free, it teaches nothing; do not write it.
- A real user could plausibly bring this situation. No contrived or gimmicky setups.
- Concrete values everywhere, taken from the contract's real data. No placeholders, no variables,
  no "example_id".
- User personality, accent, or language is NOT varied unless the scenario is specifically about it."""

AGENT_INPUT_BY_MODALITY = {
    "voice": (
        "a situation instruction for the simulated caller, written in second person as lived "
        "circumstance ('You are calling... You want...'). State their goal and what they know. Facts "
        "the agent should have to ask for are listed separately (see `facts`), so do not volunteer "
        "them here. Never write stage directions like 'tell the agent that X'; never script the "
        "agent's side; no accent or voice notes"
    ),
    "chat": (
        "a situation instruction for the simulated user, second person, lived circumstance: their "
        "goal and what they know. Facts the agent should elicit are listed separately in `facts`"
    ),
    "data_sql": "the plain-English question only: no SQL, no table or column names, no answer",
    "code": (
        "the command the agent is invoked with (a real command from the contract) or the issue text "
        "handed to it: never the fix, the patch, or the expected review"
    ),
    "browser": "the natural-language task plus only the starting URL: no selectors, no answer",
    "research": "the research question or brief only: no expected findings",
    "_default": "exactly what the agent receives at the start, in natural form: never the answer",
}

CHECKPOINT_VOCABULARY = """CHECKPOINT kinds, strongest first. Use the strongest kind that applies; use
`judge` only when nothing inspectable exists.
- tool_call_args (deterministic): the agent must call a specific tool with specific argument values.
  definition: {"tool": "<exact tool name from the contract>", "args_equal": {"<exact arg name>":
  <expected value>, ...}, "args_present": ["<arg that must be present with any reasonable value>"]}.
  Put every argument whose value the user's request determines into args_equal.
- state (deterministic): the world must end in a specific state. definition: {"must":
  {"<dotted.path.into.state>": <value>}, "forbidden": {"<dotted.path>": <value>}} evaluated against
  the seeded environment state after the run.
- conveyed (deterministic): the agent must have told the user a specific grounded fact. definition:
  {"must_include_any": ["<exact substring>", "<acceptable variant>"]} matched against the agent's
  side of the transcript. Use real values from the contract data (a price, a total, an id).
- absent (deterministic): something must NOT happen. definition: {"no_tool_call": "<tool>"} or
  {"no_tool_call_with": {"tool": "<tool>", "args_equal": {...}}}.
- judge (not deterministic, last resort): definition: {"rubric": "<one precise yes/no question a
  grader answers from the transcript>"}.
Each sub-goal is written as: {"name": "<snake_case>", "milestone": "<one line a product owner
understands>", "checkpoint": {"kind": "<one of the five>", "detail": "<one precise sentence>",
"deterministic": true|false, "definition": {...}}}.
For conversational agents, checkpoints must be ORDER-INDEPENDENT: the agent may gather information
in any order, so assert final tool calls, final state, and captured facts, never a question order."""


def subgoal_catalog_prompt(brief: str) -> str:
    return f"""{brief}

Task: derive this agent's SHARED SUB-GOAL CATALOG.

A shared sub-goal is a milestone that will recur across MANY different test scenarios for this agent.
Naming these once, and reusing the same name everywhere, lets results aggregate: if 30 scenarios
include the sub-goal `payment_recorded` and it fails in 12, the team sees exactly where the agent
breaks. Scenario-specific values (which item, which amount) are filled in per scenario; the catalog
entry fixes the name, the meaning, and the shape of its checkpoint.

{CHECKPOINT_VOCABULARY}

Rules:
- 6 to 14 entries. Each must plausibly appear in several DIFFERENT scenarios for this agent.
- Names are snake_case, stable, and meaningful to a product owner. No internal plumbing, no
  micro-steps.
- Each entry carries default_kind (the checkpoint kind it normally uses) and definition_template:
  the definition shape with <FILL: description> markers where a scenario supplies concrete values.
- Prefer deterministic kinds. If an entry must use `judge`, say in one line why nothing inspectable
  exists for it.
Return JSON: {{"catalog": [{{"name": "...", "description": "...", "default_kind": "...",
"definition_template": {{...}}, "justification_if_judge": "..."}}]}}"""


def derive_rows_prompt(brief: str, *, want: int, signature_cases: list[str],
                       real_use_cases: list[str], existing: list[dict], feedback: str,
                       first_round: bool) -> str:
    must = ""
    if first_round and signature_cases:
        must = ("Include one scenario for EACH of these required cases first (they come from the "
                "agent's own constraints and data):\n  - "
                + "\n  - ".join(str(s) for s in signature_cases) + "\n")
    uses = ""
    if real_use_cases:
        uses = ("The agent's real use cases, to draw scenarios from:\n  - "
                + "\n  - ".join(str(u) for u in real_use_cases) + "\n")
    dedupe = ""
    if existing:
        dedupe = ("Scenarios already planned. Yours must test DIFFERENT situations with DIFFERENT "
                  f"correct outcomes; do not repeat or reword any of these:\n{json.dumps(existing)[:2200]}\n")
    feedback_block = f"\nReviewer feedback on the previous round; act on all of it:\n{feedback}\n" if feedback else ""
    return f"""{brief}

Task: plan {want} distinct test scenarios for this agent. You are both the engineer who built it and
the product manager who answers for it in production; plan the tests those two people would insist
on before shipping.

For each scenario return one line of planning, not the full test yet:
- id: a short slug
- use_case: the user-facing job it belongs to (sentence case; scenarios sharing a job repeat the
  same use_case wording exactly)
- situation: ONE line naming the specific condition of the world or the user that this scenario
  fixes, phrased from the user or world side. It must not mention the agent's tools, must not
  prescribe what the agent should do, and must not contain the expected outcome.
- why_distinct: one line naming the distinct correct OUTCOME this situation produces
- goal: one line, the single end-objective of the test

{uses}{must}Coverage rules:
- Different situations with the same correct outcome are ONE scenario; pick the strongest.
- Cover the failure-shaped situations a production owner worries about, where the contract makes
  them real: the requested thing does not exist or is unavailable, the request is ambiguous and
  needs a clarifying question, the user changes their mind or corrects an earlier statement mid-way,
  the request violates one of the agent's hard constraints and must be declined, the user abandons.
- Also cover the core successful paths, including ones with several steps or several items.
- No scenarios about internal machinery (logging, config, retries): users never bring those.
{dedupe}{feedback_block}Return JSON: {{"rows": [{{"id": "...", "use_case": "...", "situation": "...",
"why_distinct": "...", "goal": "..."}}]}}"""


def materialize_prompt(brief: str, *, row: dict, base_environment: dict, catalog: list[dict],
                       modality: str, conversational: bool, hint: str = "") -> str:
    input_spec = AGENT_INPUT_BY_MODALITY.get(modality, AGENT_INPUT_BY_MODALITY["_default"])
    conv = ""
    if conversational:
        conv = """- This agent is conversational: `agent_input` is the situation instruction handed to the
  simulated user, and `facts` lists what that user knows. Every fact the agent is supposed to ask
  for gets disclosure "on_request"; the simulated user volunteers only "volunteer" facts.
"""
    catalog_block = json.dumps(
        [{"name": c.get("name"), "description": c.get("description"),
          "default_kind": c.get("default_kind"), "definition_template": c.get("definition_template")}
         for c in catalog]
    )[:3600]
    fix = ""
    if hint:
        fix = f"\n\nA previous draft of this scenario failed review. Fix every one of these before returning:\n{hint}"
    return f"""CONTRACT (the agent's real interface; use nothing outside it):
{brief}

BASE ENVIRONMENT (exists before every test; each test declares only its changes):
{json.dumps(base_environment)[:1600]}

SHARED SUB-GOAL CATALOG (when a milestone in your scenario matches an entry, use the entry's exact
name and fill its definition_template with this scenario's concrete values; invent a new sub-goal
name only when no entry fits):
{catalog_block}

SCENARIO PLAN to expand into a full test: {json.dumps(row)}

{CHECKPOINT_VOCABULARY}

Write the complete test. Every value must be a real value from the contract's data. Keep the three
parts separate: the input never reveals the environment seeding, the checkpoints, or the outcome.

Return JSON with ALL of these keys, none empty:
- id, use_case, situation, goal: carried from the plan (sharpen wording if needed, keep meaning)
- description: 2-3 sentences for a human reviewer: what is seeded, what the user wants, and what a
  correct agent does. This is documentation, not part of the test input.
- agent_input: {input_spec}
- facts: [{{"key": "...", "value": "...", "disclosure": "volunteer" | "on_request" | "withhold"}}].
  The concrete information the simulated user holds. Empty list only for non-conversational agents.
- persona: {{"name": "<a plausible first name>"}} and nothing more, unless this scenario is
  specifically about a user attribute
- environment: {{"seed": {{<state this test needs beyond the base, as nested keys>}},
  "mock_responses": {{"<tool name>": {{"content": "<what the tool returns, grounded in the data>",
  "state_updates": {{<state changes the call causes>}}}}}}}}. Mock only tools this scenario expects
  the agent to call.
- sub_goals: 3 to 6, per the checkpoint vocabulary above, every definition fully concrete
- expected_outcome: {{"world_state": "<one line: the correct end state>", "must_convey": ["<fact the
  agent must tell the user, with the real value>"], "forbidden": ["<action the agent must not
  take>"]}}
- max_reasonable_turns: how many user turns a competent agent needs, as an integer{fix}"""


CRITIC_SYSTEM = SCENARIO_MODEL + """

Role: you are the reviewer who decides whether a proposed test scenario enters the team's test suite.
You did not write it, and your default answer is no. Approve only what you would defend to the
engineer who owns the agent. Review in this order:

1. WORTH. Could a competent implementation of this agent plausibly fail this test? If every correct
   implementation passes it for free, reject it however well it is written, and say what a good
   agent could actually get wrong here if anything.
2. REAL. Would a real user plausibly bring this situation?
3. GROUNDED. Every tool, argument name, value, and id exists in the contract, spelled exactly.
   Nothing contradicts the agent's hard constraints. Any invented interface or id is fatal.
4. CHECKABLE. Every deterministic checkpoint is computable from the seeded environment plus the
   expected calls; expected values match what the input implies (an input asking for a large drink
   must not be checked as medium); conversational checkpoints do not depend on question order.
5. SEPARATION. The input reveals nothing the user would not know: no seeded availability, no
   internal ids, no expected outcome, no checkpoint contents.

Return JSON: {"verdict": "accept" | "revise" | "reject", "scores": {"worth": 1-5, "real": 1-5,
"grounded": 1-5, "checkable": 1-5, "separation": 1-5}, "problems": ["<specific, fixable problem>"],
"fix_hints": "<imperative instructions that fix every problem; empty when accepting>"}
Reject means the situation itself is not worth testing; revise means the situation is good but the
execution has fixable problems."""


def critic_prompt(brief: str, scenario: dict) -> str:
    return f"""CONTRACT (the ground truth this test must respect):
{brief}

PROPOSED TEST SCENARIO:
{json.dumps(scenario)[:7000]}

Review it per your instructions and return the JSON verdict."""


SUITE_REVIEW_SYSTEM = SCENARIO_MODEL + """

Role: you review a whole set of accepted test scenarios for COVERAGE, not for individual quality.
You answer one question: what is missing? Return specific, plannable gaps, each phrased as a
situation from the user or world side with its distinct correct outcome. Do not repeat situations
the set already covers. Return JSON: {"gaps": [{"situation": "<one line>", "why_it_matters": "<one
line>"}], "near_duplicates": [["<id>", "<id>"]]} with at most 6 gaps, empty lists when the set is
genuinely complete."""


def suite_review_prompt(brief: str, records: list[dict]) -> str:
    summary = [
        {
            "id": r.get("id"),
            "use_case": r.get("use_case"),
            "situation": r.get("situation"),
            "outcome": (r.get("expected_outcome") or {}).get("world_state"),
        }
        for r in records
    ]
    return f"""CONTRACT:
{brief}

ACCEPTED SCENARIOS so far:
{json.dumps(summary)[:6000]}

What situations that matter in production are missing, and which pairs are near-duplicates?"""
